#!/usr/bin/env bash
#
# Ducts Manufacturing — Environment Provisioning Script
# ======================================================
# Creates a complete Azure environment (dev/staging/prod) with all resources,
# configures settings, and generates local.settings.json for local development.
#
# Usage:
#   ./scripts/setup-env.sh dev              # Create dev environment
#   ./scripts/setup-env.sh staging          # Create staging environment
#   ./scripts/setup-env.sh dev --destroy    # Tear down dev environment
#
# Prerequisites:
#   - Azure CLI logged in (az login)
#   - Correct subscription selected (az account set -s <id>)
#   - Smartsheet API key available
#
set -euo pipefail

# ═══════════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════════

ENV="${1:-}"
DESTROY="${2:-}"
PROJECT="ducts"
LOCATION="eastus"  # Match prod region

# Validate input
if [[ -z "$ENV" ]] || [[ ! "$ENV" =~ ^(dev|staging|uat)$ ]]; then
    echo "Usage: $0 <dev|staging|uat> [--destroy]"
    echo ""
    echo "Examples:"
    echo "  $0 dev           # Provision dev environment"
    echo "  $0 staging       # Provision staging environment"
    echo "  $0 dev --destroy # Tear down dev environment"
    exit 1
fi

# Derived names (match prod naming convention)
RG="rg-${PROJECT}-${ENV}"
STORAGE="stg${PROJECT}${ENV}001"
FUNC_APP="fn-${PROJECT}-${ENV}"
APP_INSIGHTS="ai-${PROJECT}-${ENV}"
LOG_ANALYTICS="law-${PROJECT}-${ENV}"
QUEUE_NAME="allocation-locks"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }
log_ok()   { echo -e "${GREEN}[OK]${NC}   $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_err()  { echo -e "${RED}[ERR]${NC}  $1"; }

# ═══════════════════════════════════════════════════════════════════════════════
# Destroy mode
# ═══════════════════════════════════════════════════════════════════════════════

if [[ "$DESTROY" == "--destroy" ]]; then
    echo -e "${RED}WARNING: This will delete ALL resources in resource group '${RG}'${NC}"
    read -p "Type the environment name to confirm: " CONFIRM
    if [[ "$CONFIRM" != "$ENV" ]]; then
        echo "Aborted."
        exit 1
    fi
    az group delete --name "$RG" --yes --no-wait
    echo -e "${GREEN}Resource group '${RG}' deletion initiated.${NC}"
    exit 0
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Pre-flight checks
# ═══════════════════════════════════════════════════════════════════════════════

log_step "Pre-flight checks..."

# Check Azure CLI
if ! command -v az &> /dev/null; then
    log_err "Azure CLI not found. Install: https://docs.microsoft.com/en-us/cli/azure/install-azure-cli"
    exit 1
fi

# Check logged in
ACCOUNT=$(az account show --query "name" -o tsv 2>/dev/null || true)
if [[ -z "$ACCOUNT" ]]; then
    log_err "Not logged in. Run: az login"
    exit 1
fi
log_ok "Azure CLI logged in: $ACCOUNT"

# Check func CLI
if ! command -v func &> /dev/null; then
    log_warn "Azure Functions Core Tools not found. Install for local dev + deployment."
fi

# Prompt for secrets
echo ""
echo -e "${YELLOW}═══ Secrets Configuration ═══${NC}"

# Try to read from prod settings first
PROD_SMARTSHEET_KEY=$(az functionapp config appsettings list \
    --name fn-ducts-prod --resource-group rg-ducts-inventory \
    --query "[?name=='SMARTSHEET_API_KEY'].value" -o tsv 2>/dev/null || true)

if [[ -n "$PROD_SMARTSHEET_KEY" ]]; then
    echo "Found Smartsheet API key from prod (will reuse)."
    SMARTSHEET_API_KEY="$PROD_SMARTSHEET_KEY"
else
    read -sp "Smartsheet API Key: " SMARTSHEET_API_KEY
    echo ""
fi

read -p "Smartsheet DEV Workspace ID (create workspace first, enter ID): " WORKSPACE_ID

# SharePoint (optional)
read -p "SharePoint Base URL for $ENV (press Enter to skip): " SP_BASE_URL
SP_BASE_URL="${SP_BASE_URL:-}"

echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Resource Group
# ═══════════════════════════════════════════════════════════════════════════════

log_step "1/7 — Creating Resource Group: $RG"
if az group show --name "$RG" &>/dev/null; then
    log_ok "Resource group '$RG' already exists"
else
    az group create --name "$RG" --location "$LOCATION" -o none
    log_ok "Created resource group: $RG"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 2. Storage Account + Queue
# ═══════════════════════════════════════════════════════════════════════════════

log_step "2/7 — Creating Storage Account: $STORAGE"
if az storage account show --name "$STORAGE" --resource-group "$RG" &>/dev/null; then
    log_ok "Storage account '$STORAGE' already exists"
else
    az storage account create \
        --name "$STORAGE" \
        --resource-group "$RG" \
        --location "$LOCATION" \
        --sku Standard_LRS \
        --kind StorageV2 \
        -o none
    log_ok "Created storage account: $STORAGE"
fi

# Get connection string
STORAGE_CONN=$(az storage account show-connection-string \
    --name "$STORAGE" --resource-group "$RG" --query "connectionString" -o tsv)

# Create allocation-locks queue (used by distributed locking)
log_step "   Creating queue: $QUEUE_NAME"
az storage queue create --name "$QUEUE_NAME" --connection-string "$STORAGE_CONN" -o none 2>/dev/null || true
log_ok "Queue '$QUEUE_NAME' ready"

# ═══════════════════════════════════════════════════════════════════════════════
# 3. Log Analytics Workspace
# ═══════════════════════════════════════════════════════════════════════════════

log_step "3/7 — Creating Log Analytics: $LOG_ANALYTICS"
if az monitor log-analytics workspace show --workspace-name "$LOG_ANALYTICS" --resource-group "$RG" &>/dev/null; then
    log_ok "Log Analytics '$LOG_ANALYTICS' already exists"
else
    az monitor log-analytics workspace create \
        --workspace-name "$LOG_ANALYTICS" \
        --resource-group "$RG" \
        --location "$LOCATION" \
        --retention-time 30 \
        -o none
    log_ok "Created Log Analytics: $LOG_ANALYTICS"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 4. Application Insights
# ═══════════════════════════════════════════════════════════════════════════════

log_step "4/7 — Creating Application Insights: $APP_INSIGHTS"
if az monitor app-insights component show --app "$APP_INSIGHTS" --resource-group "$RG" &>/dev/null; then
    log_ok "App Insights '$APP_INSIGHTS' already exists"
    AI_CONN=$(az monitor app-insights component show \
        --app "$APP_INSIGHTS" --resource-group "$RG" \
        --query "connectionString" -o tsv)
else
    AI_CONN=$(az monitor app-insights component create \
        --app "$APP_INSIGHTS" \
        --location "$LOCATION" \
        --resource-group "$RG" \
        --kind web \
        --workspace "$LOG_ANALYTICS" \
        --query "connectionString" -o tsv)
    log_ok "Created App Insights: $APP_INSIGHTS"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 5. Function App (Consumption Plan, Python 3.11, Linux)
# ═══════════════════════════════════════════════════════════════════════════════

log_step "5/7 — Creating Function App: $FUNC_APP"
if az functionapp show --name "$FUNC_APP" --resource-group "$RG" &>/dev/null; then
    log_ok "Function App '$FUNC_APP' already exists"
else
    az functionapp create \
        --name "$FUNC_APP" \
        --resource-group "$RG" \
        --storage-account "$STORAGE" \
        --consumption-plan-location "$LOCATION" \
        --runtime python \
        --runtime-version 3.11 \
        --functions-version 4 \
        --os-type linux \
        --app-insights "$APP_INSIGHTS" \
        -o none
    log_ok "Created Function App: $FUNC_APP"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 6. Configure All Environment Variables
# ═══════════════════════════════════════════════════════════════════════════════

log_step "6/7 — Configuring environment variables..."

az functionapp config appsettings set \
    --name "$FUNC_APP" \
    --resource-group "$RG" \
    --settings \
        FUNCTIONS_WORKER_RUNTIME="python" \
        LOG_LEVEL="DEBUG" \
        SMARTSHEET_API_KEY="$SMARTSHEET_API_KEY" \
        SMARTSHEET_BASE_URL="https://api.smartsheet.eu/2.0" \
        SMARTSHEET_WORKSPACE_ID="$WORKSPACE_ID" \
        AZURE_STORAGE_CONNECTION_STRING="$STORAGE_CONN" \
        FLOW_CONNECT_TIMEOUT="5.0" \
        FLOW_READ_TIMEOUT="10.0" \
        FLOW_MAX_RETRIES="3" \
        FLOW_FIRE_AND_FORGET="true" \
        LPO_SUBFOLDERS="LPO Documents,Costing,Tag Sheets,Cut Sessions,BOMs,Deliveries,PODs,Invoices" \
        APPLICATIONINSIGHTS_CONNECTION_STRING="$AI_CONN" \
    -o none

log_ok "Core settings configured"

# Power Automate URLs — set as placeholders (user fills after importing solution)
az functionapp config appsettings set \
    --name "$FUNC_APP" \
    --resource-group "$RG" \
    --settings \
        POWER_AUTOMATE_CREATE_FOLDERS_URL="PLACEHOLDER_SET_AFTER_FLOW_IMPORT" \
        POWER_AUTOMATE_NESTING_COMPLETE_URL="PLACEHOLDER_SET_AFTER_FLOW_IMPORT" \
        POWER_AUTOMATE_UPLOAD_FILES_URL="PLACEHOLDER_SET_AFTER_FLOW_IMPORT" \
        POWER_AUTOMATE_MANAGER_APPROVAL_URL="PLACEHOLDER_SET_AFTER_FLOW_IMPORT" \
        POWER_AUTOMATE_SAP_CONFLICT_URL="PLACEHOLDER_SET_AFTER_FLOW_IMPORT" \
    -o none

log_ok "Power Automate placeholders set (update after importing solution)"

# SharePoint (if provided)
if [[ -n "$SP_BASE_URL" ]]; then
    az functionapp config appsettings set \
        --name "$FUNC_APP" \
        --resource-group "$RG" \
        --settings \
            SHAREPOINT_BASE_URL="$SP_BASE_URL" \
            SHAREPOINT_DOC_LIBRARY_URL="${SP_BASE_URL}/LPOs" \
            SHAREPOINT_SITE_URL="$SP_BASE_URL" \
        -o none
    log_ok "SharePoint settings configured"
else
    log_warn "SharePoint URLs not set — configure manually if needed"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 7. Generate local.settings.json for local development
# ═══════════════════════════════════════════════════════════════════════════════

log_step "7/7 — Generating local.settings.${ENV}.json"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOCAL_SETTINGS_FILE="${PROJECT_ROOT}/functions/local.settings.${ENV}.json"

cat > "$LOCAL_SETTINGS_FILE" << SETTINGS_EOF
{
    "IsEncrypted": false,
    "Values": {
        "FUNCTIONS_WORKER_RUNTIME": "python",
        "AzureWebJobsStorage": "${STORAGE_CONN}",
        "AZURE_STORAGE_CONNECTION_STRING": "${STORAGE_CONN}",
        "LOG_LEVEL": "DEBUG",
        "SMARTSHEET_API_KEY": "${SMARTSHEET_API_KEY}",
        "SMARTSHEET_BASE_URL": "https://api.smartsheet.eu/2.0",
        "SMARTSHEET_WORKSPACE_ID": "${WORKSPACE_ID}",
        "FLOW_CONNECT_TIMEOUT": "5.0",
        "FLOW_READ_TIMEOUT": "10.0",
        "FLOW_MAX_RETRIES": "3",
        "FLOW_FIRE_AND_FORGET": "true",
        "LPO_SUBFOLDERS": "LPO Documents,Costing,Tag Sheets,Cut Sessions,BOMs,Deliveries,PODs,Invoices",
        "POWER_AUTOMATE_CREATE_FOLDERS_URL": "",
        "POWER_AUTOMATE_NESTING_COMPLETE_URL": "",
        "POWER_AUTOMATE_UPLOAD_FILES_URL": "",
        "POWER_AUTOMATE_MANAGER_APPROVAL_URL": "",
        "POWER_AUTOMATE_SAP_CONFLICT_URL": "",
        "SHAREPOINT_BASE_URL": "${SP_BASE_URL}",
        "SHAREPOINT_DOC_LIBRARY_URL": "${SP_BASE_URL}/LPOs",
        "SHAREPOINT_SITE_URL": "${SP_BASE_URL}",
        "APPLICATIONINSIGHTS_CONNECTION_STRING": "${AI_CONN}"
    }
}
SETTINGS_EOF

log_ok "Generated: $LOCAL_SETTINGS_FILE"

# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Environment '${ENV}' provisioned successfully!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "  Resource Group:    $RG"
echo "  Function App:      https://${FUNC_APP}.azurewebsites.net"
echo "  Storage Account:   $STORAGE"
echo "  App Insights:      $APP_INSIGHTS"
echo "  Local Settings:    functions/local.settings.${ENV}.json"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Create Smartsheet DEV workspace & copy sheets from prod"
echo "  2. Run manifest refresh against DEV workspace"
echo "  3. Import Power Automate solution into DEV environment"
echo "  4. Update POWER_AUTOMATE_* URLs in Azure Function App settings:"
echo "     az functionapp config appsettings set --name $FUNC_APP -g $RG \\"
echo "       --settings POWER_AUTOMATE_CREATE_FOLDERS_URL='<url>'"
echo "  5. Deploy code:"
echo "     cd functions && func azure functionapp publish $FUNC_APP --python"
echo "  6. Verify:"
echo "     curl https://${FUNC_APP}.azurewebsites.net/api/stock/snapshot?plant=PLANT-A"
echo ""
