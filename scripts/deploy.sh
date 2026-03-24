#!/usr/bin/env bash
#
# Ducts Manufacturing — Deployment Script
# ========================================
# Deploys code to an Azure Function App with pre-deployment verification.
#
# Usage:
#   ./scripts/deploy.sh prod          # Deploy to production
#   ./scripts/deploy.sh dev           # Deploy to dev
#   ./scripts/deploy.sh prod --force  # Skip confirmations (CI/CD)
#
set -euo pipefail

ENV="${1:-}"
FORCE="${2:-}"
PROJECT="ducts"

if [[ -z "$ENV" ]]; then
    echo "Usage: $0 <dev|staging|prod> [--force]"
    exit 1
fi

# Resolve names
case "$ENV" in
    prod)
        FUNC_APP="fn-${PROJECT}-prod"
        RG="rg-${PROJECT}-inventory"
        ;;
    *)
        FUNC_APP="fn-${PROJECT}-${ENV}"
        RG="rg-${PROJECT}-${ENV}"
        ;;
esac

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
log_check() { echo -e "${GREEN}  [✓]${NC}   $1"; }
log_cross() { echo -e "${RED}  [✗]${NC}   $1"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
FUNCTIONS_DIR="${PROJECT_ROOT}/functions"
ERRORS=0

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Deploying to: ${ENV} (${FUNC_APP})${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 1: Git status — no uncommitted changes
# ═══════════════════════════════════════════════════════════════════════════════

log_step "1/8 — Git status check"
DIRTY=$(git -C "$PROJECT_ROOT" status --porcelain 2>/dev/null | grep -v '^??' | head -5 || true)
if [[ -n "$DIRTY" ]]; then
    log_cross "Uncommitted changes detected:"
    echo "$DIRTY"
    if [[ "$ENV" == "prod" && "$FORCE" != "--force" ]]; then
        log_err "Cannot deploy to prod with uncommitted changes. Commit first."
        exit 1
    else
        log_warn "Proceeding with uncommitted changes (non-prod)"
    fi
else
    log_check "Working tree clean"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 2: All tests pass
# ═══════════════════════════════════════════════════════════════════════════════

log_step "2/8 — Running test suite"
cd "$FUNCTIONS_DIR"

if python -m pytest tests/ -x -q --tb=line 2>&1 | tail -3; then
    TEST_RESULT=$(python -m pytest tests/ -x -q --tb=line 2>&1 | tail -1)
    if echo "$TEST_RESULT" | grep -q "failed"; then
        log_cross "Tests failed"
        ERRORS=$((ERRORS + 1))
    else
        log_check "All tests passed"
    fi
else
    log_cross "Test execution failed"
    ERRORS=$((ERRORS + 1))
fi

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 3: All function.json files exist for every fn_* directory
# ═══════════════════════════════════════════════════════════════════════════════

log_step "3/8 — Checking function.json files"
MISSING_JSON=0
for fn_dir in "$FUNCTIONS_DIR"/fn_*/; do
    fn_name=$(basename "$fn_dir")
    if [[ ! -f "${fn_dir}/function.json" ]]; then
        log_cross "Missing: ${fn_name}/function.json"
        MISSING_JSON=$((MISSING_JSON + 1))
    fi
done

if [[ $MISSING_JSON -eq 0 ]]; then
    FN_COUNT=$(ls -d "$FUNCTIONS_DIR"/fn_*/ 2>/dev/null | wc -l)
    log_check "All $FN_COUNT functions have function.json"
else
    ERRORS=$((ERRORS + 1))
fi

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 4: Verify Azure target exists and is reachable
# ═══════════════════════════════════════════════════════════════════════════════

log_step "4/8 — Verifying Azure target"
if az functionapp show --name "$FUNC_APP" --resource-group "$RG" --query "state" -o tsv &>/dev/null; then
    log_check "Function App '$FUNC_APP' exists and is accessible"
else
    log_cross "Function App '$FUNC_APP' not found in resource group '$RG'"
    ERRORS=$((ERRORS + 1))
fi

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 5: Verify all required environment variables are set
# ═══════════════════════════════════════════════════════════════════════════════

log_step "5/8 — Checking environment variables"

REQUIRED_VARS=(
    "SMARTSHEET_API_KEY"
    "SMARTSHEET_BASE_URL"
    "SMARTSHEET_WORKSPACE_ID"
    "AZURE_STORAGE_CONNECTION_STRING"
    "FUNCTIONS_WORKER_RUNTIME"
)

FLOW_VARS=(
    "POWER_AUTOMATE_CREATE_FOLDERS_URL"
    "POWER_AUTOMATE_NESTING_COMPLETE_URL"
    "POWER_AUTOMATE_UPLOAD_FILES_URL"
    "POWER_AUTOMATE_MANAGER_APPROVAL_URL"
    "POWER_AUTOMATE_SAP_CONFLICT_URL"
)

REMOTE_SETTINGS=$(az functionapp config appsettings list \
    --name "$FUNC_APP" --resource-group "$RG" \
    --query "[].name" -o tsv 2>/dev/null || true)

MISSING_REQUIRED=0
for VAR in "${REQUIRED_VARS[@]}"; do
    if echo "$REMOTE_SETTINGS" | grep -q "^${VAR}$"; then
        log_check "$VAR"
    else
        log_cross "$VAR — MISSING (required)"
        MISSING_REQUIRED=$((MISSING_REQUIRED + 1))
    fi
done

for VAR in "${FLOW_VARS[@]}"; do
    VAL=$(az functionapp config appsettings list \
        --name "$FUNC_APP" --resource-group "$RG" \
        --query "[?name=='${VAR}'].value" -o tsv 2>/dev/null || true)
    if [[ -z "$VAL" ]] || [[ "$VAL" == "PLACEHOLDER"* ]]; then
        log_warn "$VAR — placeholder (update after flow import)"
    else
        log_check "$VAR"
    fi
done

if [[ $MISSING_REQUIRED -gt 0 ]]; then
    ERRORS=$((ERRORS + 1))
fi

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 6: Verify workspace_manifest.json is present and valid
# ═══════════════════════════════════════════════════════════════════════════════

log_step "6/8 — Checking workspace manifest"
MANIFEST="${FUNCTIONS_DIR}/workspace_manifest.json"
if [[ -f "$MANIFEST" ]]; then
    SHEET_COUNT=$(python3 -c "import json; m=json.load(open('$MANIFEST')); print(len(m.get('sheets', {})))" 2>/dev/null || echo "0")
    if [[ "$SHEET_COUNT" -gt 0 ]]; then
        log_check "Manifest valid: $SHEET_COUNT sheets"
    else
        log_cross "Manifest has 0 sheets — run manifest refresh"
        ERRORS=$((ERRORS + 1))
    fi
else
    log_cross "workspace_manifest.json not found"
    ERRORS=$((ERRORS + 1))
fi

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 7: Compare local vs remote function list
# ═══════════════════════════════════════════════════════════════════════════════

log_step "7/8 — Comparing function inventory"
LOCAL_FNS=$(ls -d "$FUNCTIONS_DIR"/fn_*/ 2>/dev/null | xargs -I{} basename {} | sort)
REMOTE_FNS=$(az functionapp function list --name "$FUNC_APP" --resource-group "$RG" \
    --query "[].name" -o tsv 2>/dev/null | sort || true)

NEW_FNS=$(comm -23 <(echo "$LOCAL_FNS") <(echo "$REMOTE_FNS") || true)
REMOVED_FNS=$(comm -13 <(echo "$LOCAL_FNS") <(echo "$REMOTE_FNS") || true)

if [[ -n "$NEW_FNS" ]]; then
    log_warn "New functions to deploy:"
    echo "$NEW_FNS" | while read -r fn; do echo "    + $fn"; done
fi
if [[ -n "$REMOVED_FNS" ]]; then
    log_warn "Functions in Azure but not local (will be removed):"
    echo "$REMOVED_FNS" | while read -r fn; do echo "    - $fn"; done
fi
if [[ -z "$NEW_FNS" && -z "$REMOVED_FNS" ]]; then
    log_check "Function inventory matches"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# GATE: Stop if errors found
# ═══════════════════════════════════════════════════════════════════════════════

echo ""
if [[ $ERRORS -gt 0 ]]; then
    log_err "$ERRORS pre-deployment check(s) failed. Fix issues above before deploying."
    exit 1
fi

log_ok "All pre-deployment checks passed"

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 8: Confirmation (prod only)
# ═══════════════════════════════════════════════════════════════════════════════

if [[ "$ENV" == "prod" && "$FORCE" != "--force" ]]; then
    echo ""
    echo -e "${RED}═══ PRODUCTION DEPLOYMENT ═══${NC}"
    echo "Target: $FUNC_APP"
    COMMIT=$(git -C "$PROJECT_ROOT" log --oneline -1 2>/dev/null || echo "unknown")
    echo "Commit: $COMMIT"
    echo ""
    read -p "Type 'deploy-prod' to confirm: " CONFIRM
    if [[ "$CONFIRM" != "deploy-prod" ]]; then
        echo "Aborted."
        exit 1
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
# DEPLOY
# ═══════════════════════════════════════════════════════════════════════════════

log_step "8/8 — Deploying to $FUNC_APP..."
cd "$FUNCTIONS_DIR"
func azure functionapp publish "$FUNC_APP" --python

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Deployment to '${ENV}' complete!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Post-deployment: list deployed functions
log_step "Verifying deployed functions..."
az functionapp function list --name "$FUNC_APP" --resource-group "$RG" \
    --query "[].{name: name, language: language}" -o table 2>/dev/null || true

echo ""
echo "Logs: az functionapp log tail --name $FUNC_APP --resource-group $RG"
echo ""
