#!/usr/bin/env bash
#
# Ducts Manufacturing — Promote Dev → Production
# ================================================
# Validates that dev is stable, compares environments, and promotes
# to production with full safety checks.
#
# Usage:
#   ./scripts/promote.sh              # Promote dev → prod (interactive)
#   ./scripts/promote.sh --dry-run    # Show what would change, don't deploy
#
set -euo pipefail

DRY_RUN="${1:-}"
PROJECT="ducts"

# Source and target
SRC_APP="fn-${PROJECT}-dev"
SRC_RG="rg-${PROJECT}-dev"
DST_APP="fn-${PROJECT}-prod"
DST_RG="rg-${PROJECT}-inventory"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_step()  { echo -e "${BLUE}[STEP]${NC}  $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_err()   { echo -e "${RED}[FAIL]${NC}  $1"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
FUNCTIONS_DIR="${PROJECT_ROOT}/functions"

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Promote: dev → prod${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Run full test suite
# ═══════════════════════════════════════════════════════════════════════════════

log_step "1/6 — Running tests"
cd "$FUNCTIONS_DIR"
TEST_OUTPUT=$(python -m pytest tests/ -q --tb=line 2>&1)
if echo "$TEST_OUTPUT" | grep -q "failed"; then
    log_err "Tests failed — cannot promote"
    echo "$TEST_OUTPUT" | tail -5
    exit 1
fi
TEST_COUNT=$(echo "$TEST_OUTPUT" | grep "passed" | grep -oP '\d+ passed')
log_ok "Tests passed: $TEST_COUNT"

# ═══════════════════════════════════════════════════════════════════════════════
# 2. Compare environment variables (dev vs prod)
# ═══════════════════════════════════════════════════════════════════════════════

log_step "2/6 — Comparing environment variables"

DEV_VARS=$(az functionapp config appsettings list \
    --name "$SRC_APP" --resource-group "$SRC_RG" \
    --query "[].name" -o tsv 2>/dev/null | sort)

PROD_VARS=$(az functionapp config appsettings list \
    --name "$DST_APP" --resource-group "$DST_RG" \
    --query "[].name" -o tsv 2>/dev/null | sort)

# Variables in dev but not prod (new vars that need to be added to prod)
NEW_IN_DEV=$(comm -23 <(echo "$DEV_VARS") <(echo "$PROD_VARS") || true)
if [[ -n "$NEW_IN_DEV" ]]; then
    echo -e "${YELLOW}  New variables in dev (add to prod before deploying):${NC}"
    echo "$NEW_IN_DEV" | while read -r var; do
        # Get the dev value
        DEV_VAL=$(az functionapp config appsettings list \
            --name "$SRC_APP" --resource-group "$SRC_RG" \
            --query "[?name=='${var}'].value" -o tsv 2>/dev/null || true)
        # Redact secrets
        if echo "$var" | grep -qiE "KEY|SECRET|TOKEN|PASSWORD|SIG"; then
            echo "    + $var = ***REDACTED***"
        else
            echo "    + $var = $DEV_VAL"
        fi
    done
    echo ""
    log_warn "Add these variables to prod BEFORE deploying code"
else
    log_ok "No new environment variables"
fi

# Variables in prod but not dev (removed or legacy)
REMOVED=$(comm -13 <(echo "$DEV_VARS") <(echo "$PROD_VARS") || true)
if [[ -n "$REMOVED" ]]; then
    echo -e "${YELLOW}  Variables in prod but not dev (possibly legacy):${NC}"
    echo "$REMOVED" | while read -r var; do echo "    ? $var"; done
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 3. Compare function inventory (local vs prod)
# ═══════════════════════════════════════════════════════════════════════════════

log_step "3/6 — Comparing functions"

LOCAL_FNS=$(ls -d "$FUNCTIONS_DIR"/fn_*/ 2>/dev/null | xargs -I{} basename {} | sort)
PROD_FNS=$(az functionapp function list --name "$DST_APP" --resource-group "$DST_RG" \
    --query "[].name" -o tsv 2>/dev/null | sort || true)

NEW_FNS=$(comm -23 <(echo "$LOCAL_FNS") <(echo "$PROD_FNS") || true)
REMOVED_FNS=$(comm -13 <(echo "$LOCAL_FNS") <(echo "$PROD_FNS") || true)

if [[ -n "$NEW_FNS" ]]; then
    echo -e "${GREEN}  New functions:${NC}"
    echo "$NEW_FNS" | while read -r fn; do echo "    + $fn"; done
fi
if [[ -n "$REMOVED_FNS" ]]; then
    echo -e "${YELLOW}  Functions being removed:${NC}"
    echo "$REMOVED_FNS" | while read -r fn; do echo "    - $fn"; done
fi
if [[ -z "$NEW_FNS" && -z "$REMOVED_FNS" ]]; then
    log_ok "Function inventory matches prod"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 4. Compare logical_names.py changes (new sheets/columns)
# ═══════════════════════════════════════════════════════════════════════════════

log_step "4/6 — Checking for new Smartsheet columns"
LOGICAL_DIFF=$(git -C "$PROJECT_ROOT" diff origin/master -- functions/shared/logical_names.py 2>/dev/null || true)
if [[ -n "$LOGICAL_DIFF" ]]; then
    # Count new column definitions
    NEW_COLS=$(echo "$LOGICAL_DIFF" | grep "^+" | grep -c "= \"" || true)
    if [[ $NEW_COLS -gt 0 ]]; then
        log_warn "$NEW_COLS new column definitions in logical_names.py"
        echo "    Ensure prod workspace_manifest.json is refreshed if new columns were added to Smartsheet"
    fi
else
    log_ok "No logical_names.py changes"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 5. Check manifest freshness
# ═══════════════════════════════════════════════════════════════════════════════

log_step "5/6 — Checking manifest"
MANIFEST="${FUNCTIONS_DIR}/workspace_manifest.json"
if [[ -f "$MANIFEST" ]]; then
    SHEET_COUNT=$(python3 -c "import json; m=json.load(open('$MANIFEST')); print(len(m.get('sheets', {})))")
    GENERATED=$(python3 -c "import json; m=json.load(open('$MANIFEST')); print(m.get('_meta',{}).get('generated_at','unknown'))" 2>/dev/null || echo "unknown")
    log_ok "Manifest: $SHEET_COUNT sheets, generated: $GENERATED"
    log_warn "Ensure this manifest matches the PROD workspace (not dev)"
else
    log_err "workspace_manifest.json not found"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 6. Show git diff summary
# ═══════════════════════════════════════════════════════════════════════════════

log_step "6/6 — Change summary"
COMMIT=$(git -C "$PROJECT_ROOT" log --oneline -1 2>/dev/null || echo "unknown")
echo "  Latest commit: $COMMIT"
echo ""

DIFF_STAT=$(git -C "$PROJECT_ROOT" diff --stat origin/master 2>/dev/null | tail -1 || echo "no changes")
echo "  Changes: $DIFF_STAT"
echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# Summary + Deploy
# ═══════════════════════════════════════════════════════════════════════════════

if [[ "$DRY_RUN" == "--dry-run" ]]; then
    echo -e "${YELLOW}Dry run complete. No changes made.${NC}"
    exit 0
fi

echo -e "${RED}═══ PRODUCTION PROMOTION CHECKLIST ═══${NC}"
echo ""
echo "  Before confirming, verify:"
echo "  [ ] All new env vars added to prod (see above)"
echo "  [ ] workspace_manifest.json is for PROD workspace"
echo "  [ ] Power Automate flows updated if needed"
echo "  [ ] Smartsheet columns added to prod workspace if needed"
echo ""
read -p "Type 'promote-to-prod' to deploy: " CONFIRM
if [[ "$CONFIRM" != "promote-to-prod" ]]; then
    echo "Aborted."
    exit 1
fi

echo ""
log_step "Deploying to production..."
cd "$FUNCTIONS_DIR"
func azure functionapp publish "$DST_APP" --python

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Production deployment complete!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "  Monitor: az functionapp log tail --name $DST_APP --resource-group $DST_RG"
echo "  Dashboard: https://portal.azure.com/#@/resource/subscriptions/.../resourceGroups/$DST_RG"
echo ""
