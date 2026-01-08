<#
.SYNOPSIS
    Deploys the Tag Ingestion Function to Azure.
.DESCRIPTION
    This script automates the creation of Azure resources and deployment of the Function App.
    It checks for prerequisites (Azure CLI, Core Tools), creates a Resource Group, Storage Account,
    and Function App, sets necessary App Settings, and publishes the code.
.PARAMETER AppName
    The globally unique name for the Function App.
.PARAMETER ResourceGroup
    The name of the Resource Group.
.PARAMETER Location
    The Azure region (e.g., uksouth, eastus).
.PARAMETER StorageAccount
    The name for the Storage Account (must be lowercase alphanumeric).
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$AppName,

    [string]$ResourceGroup = "rg-ducts-inventory-production",

    [string]$Location = "uaenorth", 

    [string]$StorageAccount
)

# ---------------------------------------------------------------------------
# Setup & Validations
# ---------------------------------------------------------------------------

$ErrorActionPreference = "Stop"

function Write-Status {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "[SUCCESS] $Message" -ForegroundColor Green
}

function Write-ErrorMsg {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

# Generate Storage Account Name if not provided
if (-not $StorageAccount) {
    # Remove hyphens and take first 20 chars of AppName + random suffix
    $cleanName = $AppName -replace "-", "" -replace "_", ""
    if ($cleanName.Length -gt 15) { $cleanName = $cleanName.Substring(0, 15) }
    $StorageAccount = "store$cleanName"
    Write-Status "Auto-generated Storage Account Name: $StorageAccount"
}

# Check prerequisites
Write-Status "Checking prerequisites..."
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-ErrorMsg "Azure CLI (az) is not installed. Please install it first."
    exit 1
}
if (-not (Get-Command func -ErrorAction SilentlyContinue)) {
    Write-ErrorMsg "Azure Functions Core Tools (func) is not installed. Please install it first."
    exit 1
}

# Check Login
Write-Status "Checking Azure login status..."
try {
    $account = az account show | ConvertFrom-Json
    Write-Success "Logged in as $($account.user.name) in subscription $($account.name)"
} catch {
    Write-Status "Not logged in. Initiating login..."
    az login
}

# ---------------------------------------------------------------------------
# Resource Creation
# ---------------------------------------------------------------------------

# Create Resource Group
Write-Status "Creating Resource Group '$ResourceGroup' in '$Location'..."
try {
    az group create --name $ResourceGroup --location $Location | Out-Null
    Write-Success "Resource Group created/verified."
} catch {
    Write-ErrorMsg "Failed to create Resource Group. Details: $_"
    exit 1
}

# Create Storage Account
Write-Status "Creating Storage Account '$StorageAccount'..."
try {
    az storage account create --name $StorageAccount --location $Location --resource-group $ResourceGroup --sku Standard_LRS --encryption-services blob | Out-Null
    Write-Success "Storage Account created/verified."
} catch {
    Write-ErrorMsg "Failed to create Storage Account. Ensure name is unique and lowercase alphanumeric. Details: $_"
    exit 1
}

# Create Function App
Write-Status "Creating Function App '$AppName'..."
try {
    az functionapp create --resource-group $ResourceGroup --consumption-plan-location $Location --runtime python --runtime-version 3.11 --functions-version 4 --name $AppName --storage-account $StorageAccount --os-type Linux | Out-Null
    Write-Success "Function App created/verified."
} catch {
    Write-ErrorMsg "Failed to create Function App. Details: $_"
    exit 1
}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

Write-Status "Configuring App Settings..."

# Helper to read local.settings.json
$localSettingsPath = Join-Path $PSScriptRoot "local.settings.json"
if (Test-Path $localSettingsPath) {
    try {
        $settingsJson = Get-Content $localSettingsPath -Raw | ConvertFrom-Json
        $values = $settingsJson.Values
        
        $envSettings = @()
        
        if ($values.SMARTSHEET_API_KEY) {
            $envSettings += "SMARTSHEET_API_KEY=$($values.SMARTSHEET_API_KEY)"
        }
        if ($values.SMARTSHEET_WORKSPACE_ID) {
            $envSettings += "SMARTSHEET_WORKSPACE_ID=$($values.SMARTSHEET_WORKSPACE_ID)"
        }
        if ($values.SMARTSHEET_BASE_URL) {
            $envSettings += "SMARTSHEET_BASE_URL=$($values.SMARTSHEET_BASE_URL)"
        }
        
        if ($envSettings.Count -gt 0) {
            Write-Status "Pushing settings from local.settings.json to Azure..."
            az functionapp config appsettings set --name $AppName --resource-group $ResourceGroup --settings $envSettings | Out-Null
            Write-Success "App settings configured ($($envSettings.Count) settings)."
        } else {
            Write-Warn "No Smartsheet settings found in local.settings.json."
        }
    } catch {
        Write-ErrorMsg "Error reading local.settings.json: $_"
    }
} else {
    Write-Warn "local.settings.json not found. You must set app settings manually."
}

# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

Write-Status "Ready to deploy code to '$AppName'."
Write-Host "This will publish the current folder content to Azure." -ForegroundColor Yellow
$confirmation = Read-Host "Proceed with deployment? (y/n)"

if ($confirmation -eq 'y') {
    Write-Status "Publishing..."
    # Ensure requirements are installed locally to be safe, though func does remote build
    func azure functionapp publish $AppName --python
    Write-Success "Deployment completed successfully!"
    Write-Host "Function URL: https://$AppName.azurewebsites.net/api/tags/ingest" -ForegroundColor Cyan
} else {
    Write-Status "Deployment cancelled."
}
