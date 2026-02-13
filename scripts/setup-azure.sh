#!/bin/bash
# =============================================================================
# SJI Fire MCP Server — Azure Infrastructure Setup
# =============================================================================
#
# One-time script to provision Azure resources for the remote MCP server.
# Requires: az cli logged in with Owner/Contributor on the target subscription.
#
# Usage:
#   ./scripts/setup-azure.sh              # Provision everything
#   ./scripts/setup-azure.sh --phase N    # Run only phase N (1-6)
#
# Existing Azure Resources (reference — NOT created by this script):
# ┌────────────────────┬──────────────────────────────┬──────────────────────────────┐
# │ Resource           │ Name                         │ Resource Group               │
# ├────────────────────┼──────────────────────────────┼──────────────────────────────┤
# │ Entra ID Tenant    │ 4122848f-c317-4267-9c07-...  │ —                            │
# │ Key Vault          │ gh-website-utilities         │ rg-staticweb-prod-westus2    │
# │ App Registration   │ utilities-sync (bb2cb591...) │ —  (OIDC + Graph + Exchange) │
# │ App Registration   │ website-admin                │ —  (SWA auth, admin portal)  │
# │ Cosmos DB (Mongo)  │ website-tinacms              │ rg-staticweb-prod-westus2    │
# │ Static Web App     │ sjifire.org website          │ rg-staticweb-prod-westus2    │
# └────────────────────┴──────────────────────────────┴──────────────────────────────┘
#
# Why a NEW Cosmos DB account?
#   The existing 'website-tinacms' uses the MongoDB API (mongodb+srv://...).
#   Our MCP server uses the NoSQL/SQL API (azure.cosmos.aio → *.documents.azure.com).
#   Cosmos DB accounts are locked to one API type at creation, so we need a separate account.
#
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Read domain from config/organization.json
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ORG_CONFIG="$PROJECT_ROOT/config/organization.json"

if [ ! -f "$ORG_CONFIG" ]; then
    echo "Error: $ORG_CONFIG not found" >&2
    exit 1
fi

DOMAIN=$(python3 -c "import json; print(json.load(open('$ORG_CONFIG'))['domain'])")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCATION="westus2"
RESOURCE_GROUP="rg-sjifire-mcp"
KEY_VAULT="gh-website-utilities"
TENANT_ID="4122848f-c317-4267-9c07-7c504150d1bd"

# Cosmos DB
COSMOS_ACCOUNT="sjifire-mcp-cosmos"
COSMOS_DB="sjifire-incidents"

# Container Registry + Container Apps
ACR_NAME="sjifiremcp"
CA_ENV="sjifire-mcp-env"
CA_APP="sjifire-mcp"

# Custom domain (derived from config/organization.json)
CUSTOM_DOMAIN="mcp.${DOMAIN}"

# GitHub Actions service principal (utilities-sync)
GH_ACTIONS_SP="bb2cb591-58ff-4687-bf14-502049dee01b"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()  { echo -e "${CYAN}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
fail()  { echo -e "${RED}✗${NC} $*" >&2; exit 1; }

store_secret() {
    local name="$1" value="$2"
    info "Storing secret $name in Key Vault..."
    az keyvault secret set \
        --vault-name "$KEY_VAULT" \
        --name "$name" \
        --value "$value" \
        --output none 2>/dev/null || true
    ok "Secret $name stored"
}

# Parse args
PHASE="${2:-}"
if [ "${1:-}" = "--phase" ] && [ -n "$PHASE" ]; then
    RUN_PHASE="$PHASE"
else
    RUN_PHASE="all"
fi

should_run() {
    [ "$RUN_PHASE" = "all" ] || [ "$RUN_PHASE" = "$1" ]
}

# Check prerequisites
if ! az account show &>/dev/null; then
    fail "Not logged in to Azure CLI. Run 'az login' first."
fi

SUB_ID=$(az account show --query id -o tsv)
info "Using subscription: $SUB_ID"
info "Tenant: $TENANT_ID"

# Register resource providers (idempotent, no-op if already registered)
for ns in Microsoft.DocumentDB Microsoft.ContainerRegistry Microsoft.App; do
    STATE=$(az provider show --namespace "$ns" --query registrationState -o tsv 2>/dev/null || echo "NotRegistered")
    if [ "$STATE" != "Registered" ]; then
        info "Registering provider $ns..."
        az provider register --namespace "$ns" --output none
    fi
done
# Wait for registration to complete
for ns in Microsoft.DocumentDB Microsoft.ContainerRegistry Microsoft.App; do
    STATE=$(az provider show --namespace "$ns" --query registrationState -o tsv)
    if [ "$STATE" != "Registered" ]; then
        info "Waiting for $ns to register..."
        while [ "$(az provider show --namespace "$ns" --query registrationState -o tsv)" != "Registered" ]; do
            sleep 5
        done
    fi
    ok "Provider $ns registered"
done
echo ""

# =============================================================================
# Phase 1: Entra ID App Registration
# =============================================================================

if should_run 1; then
    echo -e "${CYAN}━━━ Phase 1: Entra ID App Registration ━━━${NC}"

    # Check if app already exists
    EXISTING_APP=$(az ad app list --display-name "SJI Fire MCP Server" --query "[0].appId" -o tsv 2>/dev/null || true)

    if [ -n "$EXISTING_APP" ]; then
        MCP_CLIENT_ID="$EXISTING_APP"
        warn "App registration already exists: $MCP_CLIENT_ID"
    else
        info "Creating app registration 'SJI Fire MCP Server'..."
        MCP_CLIENT_ID=$(az ad app create \
            --display-name "SJI Fire MCP Server" \
            --sign-in-audience AzureADMyOrg \
            --enable-id-token-issuance true \
            --enable-access-token-issuance true \
            --query appId -o tsv)
        ok "App registered: $MCP_CLIENT_ID"
    fi

    # Ensure /callback redirect URI is on the Web platform (server-side token exchange)
    # SPA platform won't work — Entra enforces browser-origin redemption for SPA codes.
    # With isFallbackPublicClient=true, Entra accepts PKCE without client_secret on Web.
    info "Ensuring Web redirect URI for /callback..."

    # Clear any SPA redirect URI (wrong platform for server-side exchange)
    az ad app update --id "$MCP_CLIENT_ID" \
        --set "spa={\"redirectUris\":[]}" 2>/dev/null || true

    EXISTING_WEB=$(az ad app show --id "$MCP_CLIENT_ID" \
        --query "web.redirectUris" -o json 2>/dev/null || echo "[]")
    if echo "$EXISTING_WEB" | python3 -c "import sys,json; uris=json.load(sys.stdin); sys.exit(0 if 'https://mcp.${DOMAIN}/callback' in uris else 1)" 2>/dev/null; then
        ok "Web redirect URI /callback already present"
    else
        az ad app update --id "$MCP_CLIENT_ID" \
            --web-redirect-uris "https://mcp.${DOMAIN}/callback"
        ok "Added Web redirect URI: https://mcp.${DOMAIN}/callback"
    fi

    # Create client secret for server-side token exchange (if not already stored)
    EXISTING_SECRET=$(az keyvault secret show --vault-name "$KEY_VAULT" --name "ENTRA-MCP-API-CLIENT-SECRET" --query value -o tsv 2>/dev/null || true)
    if [ -z "$EXISTING_SECRET" ]; then
        info "Creating client secret..."
        MCP_CLIENT_SECRET=$(az ad app credential reset \
            --id "$MCP_CLIENT_ID" \
            --display-name "MCP OAuth proxy" \
            --years 2 \
            --query password -o tsv)
        store_secret "ENTRA-MCP-API-CLIENT-SECRET" "$MCP_CLIENT_SECRET"
    else
        ok "Client secret already in Key Vault"
    fi

    # Set identifier URI for API scope
    info "Setting identifier URI..."
    az ad app update --id "$MCP_CLIENT_ID" \
        --identifier-uris "api://$MCP_CLIENT_ID" 2>/dev/null || true
    ok "Identifier URI: api://$MCP_CLIENT_ID"

    # Add oauth2PermissionScopes (mcp.access)
    info "Adding mcp.access scope..."
    # Generate a deterministic GUID from the client ID for the scope
    SCOPE_ID=$(python3 -c "import uuid; print(uuid.uuid5(uuid.NAMESPACE_URL, 'mcp.access'))")
    az ad app update --id "$MCP_CLIENT_ID" \
        --set "api={\"oauth2PermissionScopes\":[{\"adminConsentDescription\":\"Access MCP incident tools\",\"adminConsentDisplayName\":\"MCP Access\",\"id\":\"$SCOPE_ID\",\"isEnabled\":true,\"type\":\"User\",\"userConsentDescription\":\"Access MCP incident tools\",\"userConsentDisplayName\":\"MCP Access\",\"value\":\"mcp.access\"}]}" \
        2>/dev/null || warn "Scope may already exist (expected if re-running)"
    ok "Scope mcp.access configured"

    # Include groups claim in access token (for officer RBAC)
    info "Enabling groups claim in tokens..."
    az ad app update --id "$MCP_CLIENT_ID" \
        --set "groupMembershipClaims=\"SecurityGroup\""
    ok "Groups claim enabled"

    # Grant delegated permissions: openid, profile, email
    info "Adding Microsoft Graph delegated permissions..."
    az ad app permission add --id "$MCP_CLIENT_ID" \
        --api 00000003-0000-0000-c000-000000000000 \
        --api-permissions \
            "e1fe6dd8-ba31-4d61-89e7-88639da4683d=Scope" \
            "14dad69e-099b-42c9-810b-d002981feec1=Scope" \
            "37f7f235-527c-4136-accd-4a02d197296e=Scope" \
        2>/dev/null || warn "Permissions may already be granted"
    ok "Permissions added (openid, profile, email)"

    # Admin consent
    info "Granting admin consent..."
    az ad app permission admin-consent --id "$MCP_CLIENT_ID" 2>/dev/null || \
        warn "Admin consent may require Global Admin — grant manually if needed"

    # Create officer security group
    EXISTING_GROUP=$(az ad group list --display-name "MCP Incident Officers" --query "[0].id" -o tsv 2>/dev/null || true)
    if [ -n "$EXISTING_GROUP" ]; then
        OFFICER_GROUP_ID="$EXISTING_GROUP"
        warn "Officer group already exists: $OFFICER_GROUP_ID"
    else
        info "Creating 'MCP Incident Officers' security group..."
        OFFICER_GROUP_ID=$(az ad group create \
            --display-name "MCP Incident Officers" \
            --mail-nickname "mcp-incident-officers" \
            --query id -o tsv)
        ok "Officer group created: $OFFICER_GROUP_ID"
    fi

    # Store secrets in Key Vault
    store_secret "ENTRA-MCP-API-CLIENT-ID" "$MCP_CLIENT_ID"
    store_secret "ENTRA-MCP-OFFICER-GROUP-ID" "$OFFICER_GROUP_ID"

    ok "Phase 1 complete"
    echo ""
fi

# =============================================================================
# Phase 2: Cosmos DB (Serverless, NoSQL API)
# =============================================================================

if should_run 2; then
    echo -e "${CYAN}━━━ Phase 2: Cosmos DB (Serverless, NoSQL API) ━━━${NC}"

    # Resource group
    if az group show --name "$RESOURCE_GROUP" &>/dev/null; then
        warn "Resource group $RESOURCE_GROUP already exists"
    else
        info "Creating resource group $RESOURCE_GROUP..."
        az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
        ok "Resource group created"
    fi

    # Cosmos DB account
    if az cosmosdb show --name "$COSMOS_ACCOUNT" --resource-group "$RESOURCE_GROUP" &>/dev/null; then
        warn "Cosmos DB account $COSMOS_ACCOUNT already exists"
    else
        info "Creating Cosmos DB account (serverless, NoSQL API)..."
        info "This takes 3-5 minutes..."
        az cosmosdb create \
            --name "$COSMOS_ACCOUNT" \
            --resource-group "$RESOURCE_GROUP" \
            --capabilities EnableServerless \
            --default-consistency-level Session \
            --output none
        ok "Cosmos DB account created"
    fi

    # Database
    info "Creating database $COSMOS_DB..."
    az cosmosdb sql database create \
        --account-name "$COSMOS_ACCOUNT" \
        --resource-group "$RESOURCE_GROUP" \
        --name "$COSMOS_DB" \
        --output none 2>/dev/null || true
    ok "Database ready"

    # Container: incidents (partition key: /station)
    info "Creating container 'incidents'..."
    az cosmosdb sql container create \
        --account-name "$COSMOS_ACCOUNT" \
        --resource-group "$RESOURCE_GROUP" \
        --database-name "$COSMOS_DB" \
        --name "incidents" \
        --partition-key-path "/station" \
        --output none 2>/dev/null || true
    ok "Container 'incidents' ready (partition: /station)"

    # Container: schedules (partition key: /date)
    info "Creating container 'schedules'..."
    az cosmosdb sql container create \
        --account-name "$COSMOS_ACCOUNT" \
        --resource-group "$RESOURCE_GROUP" \
        --database-name "$COSMOS_DB" \
        --name "schedules" \
        --partition-key-path "/date" \
        --output none 2>/dev/null || true
    ok "Container 'schedules' ready (partition: /date)"

    # Store endpoint and key in Key Vault
    COSMOS_ENDPOINT=$(az cosmosdb show \
        --name "$COSMOS_ACCOUNT" \
        --resource-group "$RESOURCE_GROUP" \
        --query documentEndpoint -o tsv)
    COSMOS_KEY=$(az cosmosdb keys list \
        --name "$COSMOS_ACCOUNT" \
        --resource-group "$RESOURCE_GROUP" \
        --query primaryMasterKey -o tsv)

    store_secret "COSMOS-ENDPOINT" "$COSMOS_ENDPOINT"
    store_secret "COSMOS-KEY" "$COSMOS_KEY"

    ok "Phase 2 complete"
    echo ""
fi

# =============================================================================
# Phase 3: ACR + Container Apps
# =============================================================================

if should_run 3; then
    echo -e "${CYAN}━━━ Phase 3: ACR + Container Apps ━━━${NC}"

    # Ensure resource group exists
    az group show --name "$RESOURCE_GROUP" &>/dev/null || \
        fail "Resource group $RESOURCE_GROUP not found. Run phase 2 first."

    # Azure Container Registry (Basic ~$5/mo)
    if az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" &>/dev/null; then
        warn "ACR $ACR_NAME already exists"
    else
        info "Creating Azure Container Registry ($ACR_NAME, Basic SKU)..."
        az acr create \
            --name "$ACR_NAME" \
            --resource-group "$RESOURCE_GROUP" \
            --sku Basic \
            --admin-enabled true \
            --output none
        ok "ACR created"
    fi

    # Store ACR credentials in Key Vault
    ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)
    ACR_CREDS=$(az acr credential show --name "$ACR_NAME")
    ACR_USERNAME=$(echo "$ACR_CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['username'])")
    ACR_PASSWORD=$(echo "$ACR_CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['passwords'][0]['value'])")

    store_secret "ACR-LOGIN-SERVER" "$ACR_LOGIN_SERVER"
    store_secret "ACR-USERNAME" "$ACR_USERNAME"
    store_secret "ACR-PASSWORD" "$ACR_PASSWORD"

    # Container Apps Environment (consumption = scale-to-zero)
    if az containerapp env show --name "$CA_ENV" --resource-group "$RESOURCE_GROUP" &>/dev/null; then
        warn "Container Apps environment $CA_ENV already exists"
    else
        info "Creating Container Apps environment..."
        az containerapp env create \
            --name "$CA_ENV" \
            --resource-group "$RESOURCE_GROUP" \
            --location "$LOCATION" \
            --output none
        ok "Container Apps environment created"
    fi

    # Build and push initial image
    info "Building initial Docker image via ACR..."
    az acr build \
        --registry "$ACR_NAME" \
        --image "sjifire-mcp:initial" \
        . \
        --output none
    ok "Image built and pushed"

    # Fetch secrets for Container App env vars
    info "Fetching secrets for container configuration..."
    _get_secret() {
        az keyvault secret show --vault-name "$KEY_VAULT" --name "$1" --query value -o tsv 2>/dev/null || echo ""
    }

    ENTRA_MCP_CLIENT_ID=$(_get_secret "ENTRA-MCP-API-CLIENT-ID")
    ENTRA_MCP_OFFICER_GROUP=$(_get_secret "ENTRA-MCP-OFFICER-GROUP-ID")
    COSMOS_ENDPOINT_VAL=$(_get_secret "COSMOS-ENDPOINT")
    MS_GRAPH_TENANT_ID=$(_get_secret "MS-GRAPH-TENANT-ID")
    MS_GRAPH_CLIENT_ID=$(_get_secret "MS-GRAPH-CLIENT-ID")
    MS_GRAPH_CLIENT_SECRET=$(_get_secret "MS-GRAPH-CLIENT-SECRET")
    ALADTEC_URL=$(_get_secret "ALADTEC-URL")
    ALADTEC_USERNAME=$(_get_secret "ALADTEC-USERNAME")
    ALADTEC_PASSWORD=$(_get_secret "ALADTEC-PASSWORD")

    # Create Container App
    if az containerapp show --name "$CA_APP" --resource-group "$RESOURCE_GROUP" &>/dev/null; then
        warn "Container App $CA_APP already exists"
    else
        info "Creating Container App..."
        az containerapp create \
            --name "$CA_APP" \
            --resource-group "$RESOURCE_GROUP" \
            --environment "$CA_ENV" \
            --image "$ACR_LOGIN_SERVER/sjifire-mcp:initial" \
            --registry-server "$ACR_LOGIN_SERVER" \
            --registry-username "$ACR_USERNAME" \
            --registry-password "$ACR_PASSWORD" \
            --target-port 8000 \
            --ingress external \
            --min-replicas 0 \
            --max-replicas 3 \
            --cpu 0.5 \
            --memory 1.0Gi \
            --env-vars \
                "ENTRA_MCP_API_TENANT_ID=$MS_GRAPH_TENANT_ID" \
                "ENTRA_MCP_API_CLIENT_ID=$ENTRA_MCP_CLIENT_ID" \
                "ENTRA_MCP_OFFICER_GROUP_ID=$ENTRA_MCP_OFFICER_GROUP" \
                "COSMOS_ENDPOINT=$COSMOS_ENDPOINT_VAL" \
                "MS_GRAPH_TENANT_ID=$MS_GRAPH_TENANT_ID" \
                "MS_GRAPH_CLIENT_ID=$MS_GRAPH_CLIENT_ID" \
                "MS_GRAPH_CLIENT_SECRET=secretref:ms-graph-client-secret" \
                "ALADTEC_URL=$ALADTEC_URL" \
                "ALADTEC_USERNAME=secretref:aladtec-username" \
                "ALADTEC_PASSWORD=secretref:aladtec-password" \
            --secrets \
                "ms-graph-client-secret=$MS_GRAPH_CLIENT_SECRET" \
                "aladtec-username=$ALADTEC_USERNAME" \
                "aladtec-password=$ALADTEC_PASSWORD" \
            --output none
        ok "Container App created"
    fi

    # Enable managed identity
    info "Enabling system-assigned managed identity..."
    MANAGED_IDENTITY_ID=$(az containerapp identity assign \
        --name "$CA_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --system-assigned \
        --query principalId -o tsv 2>/dev/null || true)

    if [ -n "$MANAGED_IDENTITY_ID" ]; then
        ok "Managed identity: $MANAGED_IDENTITY_ID"

        # Grant Cosmos DB data access via RBAC (no key needed in production)
        info "Granting Cosmos DB data access to managed identity..."
        COSMOS_RESOURCE_ID=$(az cosmosdb show \
            --name "$COSMOS_ACCOUNT" \
            --resource-group "$RESOURCE_GROUP" \
            --query id -o tsv)

        az cosmosdb sql role assignment create \
            --account-name "$COSMOS_ACCOUNT" \
            --resource-group "$RESOURCE_GROUP" \
            --role-definition-name "Cosmos DB Built-in Data Contributor" \
            --principal-id "$MANAGED_IDENTITY_ID" \
            --scope "$COSMOS_RESOURCE_ID" \
            --output none 2>/dev/null || warn "Role assignment may already exist"
        ok "Cosmos DB RBAC configured"

        # Grant Key Vault secret read access (for keyvaultref secrets)
        info "Granting Key Vault secret access to managed identity..."
        az keyvault set-policy \
            --name "$KEY_VAULT" \
            --object-id "$MANAGED_IDENTITY_ID" \
            --secret-permissions get \
            --output none 2>/dev/null || warn "Key Vault policy may already exist"
        ok "Key Vault access configured"
    fi

    # Print the FQDN
    CA_FQDN=$(az containerapp show \
        --name "$CA_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --query "properties.configuration.ingress.fqdn" -o tsv)
    echo ""
    ok "Container App FQDN: https://$CA_FQDN"

    # Set MCP_SERVER_URL to custom domain (falls back to auto FQDN before Phase 6)
    info "Setting MCP_SERVER_URL on container app..."
    az containerapp update \
        --name "$CA_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --set-env-vars "MCP_SERVER_URL=https://$CUSTOM_DOMAIN" \
        --output none
    ok "MCP_SERVER_URL set to https://$CUSTOM_DOMAIN"

    ok "Phase 3 complete"
    echo ""
fi

# =============================================================================
# Phase 4: Key Vault Secrets (verify all present)
# =============================================================================

if should_run 4; then
    echo -e "${CYAN}━━━ Phase 4: Verify Key Vault Secrets ━━━${NC}"

    REQUIRED_SECRETS=(
        "ENTRA-MCP-API-CLIENT-ID"
        "ENTRA-MCP-API-CLIENT-SECRET"
        "ENTRA-MCP-OFFICER-GROUP-ID"
        "COSMOS-ENDPOINT"
        "COSMOS-KEY"
        "ACR-LOGIN-SERVER"
        "ACR-USERNAME"
        "ACR-PASSWORD"
        "MS-GRAPH-TENANT-ID"
        "MS-GRAPH-CLIENT-ID"
        "MS-GRAPH-CLIENT-SECRET"
        "ALADTEC-URL"
        "ALADTEC-USERNAME"
        "ALADTEC-PASSWORD"
    )

    ALL_OK=true
    for secret in "${REQUIRED_SECRETS[@]}"; do
        if az keyvault secret show --vault-name "$KEY_VAULT" --name "$secret" &>/dev/null; then
            ok "$secret"
        else
            warn "MISSING: $secret"
            ALL_OK=false
        fi
    done

    if [ "$ALL_OK" = true ]; then
        ok "All required secrets present in Key Vault"
    else
        warn "Some secrets are missing — MCP server may not function fully"
    fi

    echo ""
    ok "Phase 4 complete"
    echo ""
fi

# =============================================================================
# Phase 5: RBAC for GitHub Actions
# =============================================================================

if should_run 5; then
    echo -e "${CYAN}━━━ Phase 5: RBAC for GitHub Actions ━━━${NC}"

    # Grant utilities-sync service principal Contributor on the MCP resource group
    info "Granting Contributor role to utilities-sync on $RESOURCE_GROUP..."
    az role assignment create \
        --assignee "$GH_ACTIONS_SP" \
        --role "Contributor" \
        --scope "/subscriptions/$SUB_ID/resourceGroups/$RESOURCE_GROUP" \
        --output none 2>/dev/null || warn "Role assignment may already exist"
    ok "utilities-sync has Contributor on $RESOURCE_GROUP"

    # Grant AcrPush so GitHub Actions can push images
    info "Granting AcrPush role to utilities-sync on $ACR_NAME..."
    ACR_ID=$(az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv 2>/dev/null || true)
    if [ -n "$ACR_ID" ]; then
        az role assignment create \
            --assignee "$GH_ACTIONS_SP" \
            --role "AcrPush" \
            --scope "$ACR_ID" \
            --output none 2>/dev/null || warn "Role assignment may already exist"
        ok "utilities-sync has AcrPush on $ACR_NAME"
    else
        warn "ACR not found — run phase 3 first"
    fi

    ok "Phase 5 complete"
    echo ""
fi

# =============================================================================
# Phase 6: Custom Domain (mcp.sjifire.org)
# =============================================================================

if should_run 6; then
    echo -e "${CYAN}━━━ Phase 6: Custom Domain ($CUSTOM_DOMAIN) ━━━${NC}"

    # Get the auto-generated FQDN (CNAME target)
    CA_FQDN=$(az containerapp show \
        --name "$CA_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --query "properties.configuration.ingress.fqdn" -o tsv)

    # Find the Azure DNS zone resource group (search across all resource groups)
    DNS_RG=$(az network dns zone list --query "[?name=='$DOMAIN'].resourceGroup | [0]" -o tsv 2>/dev/null || true)

    # Get the domain verification ID for TXT record
    VERIFICATION_ID=$(az containerapp show \
        --name "$CA_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --query "properties.customDomainVerificationId" -o tsv)

    if [ -n "$DNS_RG" ]; then
        # Create CNAME record via Azure DNS
        info "Creating CNAME record: $CUSTOM_DOMAIN → $CA_FQDN"
        az network dns record-set cname set-record \
            --resource-group "$DNS_RG" \
            --zone-name "$DOMAIN" \
            --record-set-name "mcp" \
            --cname "$CA_FQDN" \
            --output none 2>/dev/null || warn "CNAME record may already exist"
        ok "CNAME record created"

        # Create TXT verification record (asuid.mcp → verification ID)
        info "Creating TXT verification record: asuid.mcp.$DOMAIN"
        az network dns record-set txt add-record \
            --resource-group "$DNS_RG" \
            --zone-name "$DOMAIN" \
            --record-set-name "asuid.mcp" \
            --value "$VERIFICATION_ID" \
            --output none 2>/dev/null || warn "TXT record may already exist"
        ok "TXT verification record created"
    else
        # DNS not in Azure — manual step
        echo ""
        echo -e "${YELLOW}DNS Setup Required${NC}"
        echo "  Add these records in your DNS provider:"
        echo ""
        echo "    $CUSTOM_DOMAIN       CNAME  $CA_FQDN"
        echo "    asuid.mcp.$DOMAIN    TXT    $VERIFICATION_ID"
        echo ""
        read -p "Press Enter after both records have been created (or Ctrl+C to skip)..."
    fi

    # Verify DNS resolution
    info "Verifying DNS resolution..."
    RESOLVED=$(dig +short "$CUSTOM_DOMAIN" CNAME 2>/dev/null || true)
    if [ -z "$RESOLVED" ]; then
        warn "CNAME not detected yet — DNS propagation may take a few minutes"
        warn "Continuing anyway (Azure will retry validation)..."
    else
        ok "DNS resolves: $CUSTOM_DOMAIN → $RESOLVED"
    fi

    # Add custom hostname to Container App
    info "Adding hostname $CUSTOM_DOMAIN to Container App..."
    az containerapp hostname add \
        --name "$CA_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --hostname "$CUSTOM_DOMAIN" \
        --output none 2>/dev/null || warn "Hostname may already be added"
    ok "Hostname added"

    # Bind managed TLS certificate (free, auto-renewed by Azure)
    info "Binding managed TLS certificate (this may take 1-2 minutes)..."
    az containerapp hostname bind \
        --name "$CA_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --hostname "$CUSTOM_DOMAIN" \
        --environment "$CA_ENV" \
        --validation-method CNAME \
        --output none 2>/dev/null || warn "Certificate binding may already exist or DNS not yet propagated"
    ok "Managed TLS certificate bound"

    echo ""
    ok "Custom domain ready: https://$CUSTOM_DOMAIN"
    ok "Phase 6 complete"
    echo ""
fi

# =============================================================================
# Summary
# =============================================================================

echo -e "${GREEN}━━━ Setup Complete ━━━${NC}"
