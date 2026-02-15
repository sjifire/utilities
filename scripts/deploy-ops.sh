#!/bin/bash
# =============================================================================
# Deploy Ops Server to Azure Container Apps
# =============================================================================
#
# Builds a container image via ACR and updates the running Container App.
# Used by both local dev deploys and GitHub Actions (ops-deploy.yml).
#
# Usage:
#   ./scripts/deploy-ops.sh              # Build & deploy
#   ./scripts/deploy-ops.sh --build-only # Build image without deploying
#   ./scripts/deploy-ops.sh --health     # Just run health check
#
# Environment:
#   TAG=<version>  Override image tag (default: dev-YYYYMMDD-HHMMSS)
#
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration (matches setup-azure-ops.sh)
# ---------------------------------------------------------------------------

RESOURCE_GROUP="rg-sjifire-mcp"
ACR_NAME="sjifiremcp"
CONTAINER_APP="sjifire-mcp"
IMAGE_NAME="sjifire-mcp"
CUSTOM_DOMAIN="ops.sjifire.org"
KEY_VAULT="gh-website-utilities"

TAG="${TAG:-dev-$(date +%Y%m%d-%H%M%S)}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
fail()  { echo -e "${RED}✗${NC} $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------

BUILD_ONLY=false
HEALTH_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --build-only) BUILD_ONLY=true ;;
        --health)     HEALTH_ONLY=true ;;
        *)            fail "Unknown option: $arg" ;;
    esac
done

# ---------------------------------------------------------------------------
# Health check function
# ---------------------------------------------------------------------------

health_check() {
    info "Health check: https://${CUSTOM_DOMAIN}/health"
    for i in 1 2 3 4 5; do
        STATUS=$(curl -s -o /dev/null -w "%{http_code}" "https://${CUSTOM_DOMAIN}/health" 2>/dev/null || true)
        if [ "$STATUS" = "200" ]; then
            ok "Health check passed (attempt $i)"
            curl -s "https://${CUSTOM_DOMAIN}/health" | python3 -m json.tool
            return 0
        fi
        echo "  Attempt $i: HTTP $STATUS — retrying in 10s..."
        sleep 10
    done
    warn "Health check did not pass after 5 attempts"
    return 1
}

if [ "$HEALTH_ONLY" = true ]; then
    health_check
    exit $?
fi

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

if ! az account show &>/dev/null; then
    fail "Not logged in to Azure CLI. Run 'az login' first."
fi

ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --query loginServer -o tsv 2>/dev/null) || \
    fail "ACR $ACR_NAME not found. Run setup-azure-ops.sh first."

info "Image: ${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${TAG}"

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

info "Building image via ACR..."
az acr build \
    --registry "$ACR_NAME" \
    --image "${IMAGE_NAME}:${TAG}" \
    --image "${IMAGE_NAME}:latest" \
    . \
    --output none
ok "Image built: ${IMAGE_NAME}:${TAG}"

if [ "$BUILD_ONLY" = true ]; then
    ok "Build-only mode — skipping deploy"
    exit 0
fi

# ---------------------------------------------------------------------------
# Fetch config from Key Vault
# ---------------------------------------------------------------------------

VAULT_URL="https://${KEY_VAULT}.vault.azure.net"

_get_secret() {
    az keyvault secret show --vault-name "$KEY_VAULT" --name "$1" --query value -o tsv 2>/dev/null || echo ""
}

info "Fetching config from Key Vault..."
ENTRA_MCP_API_CLIENT_ID=$(_get_secret "ENTRA-MCP-API-CLIENT-ID")
ENTRA_MCP_OFFICER_GROUP_ID=$(_get_secret "ENTRA-MCP-OFFICER-GROUP-ID")
COSMOS_ENDPOINT=$(_get_secret "COSMOS-ENDPOINT")
MS_GRAPH_TENANT_ID=$(_get_secret "MS-GRAPH-TENANT-ID")
MS_GRAPH_CLIENT_ID=$(_get_secret "MS-GRAPH-CLIENT-ID")
ALADTEC_URL=$(_get_secret "ALADTEC-URL")
ok "Config fetched"

# ---------------------------------------------------------------------------
# Ensure managed identity can read Key Vault secrets (idempotent)
# ---------------------------------------------------------------------------

info "Ensuring Key Vault access for managed identity..."
MANAGED_ID=$(az containerapp identity show \
    --name "$CONTAINER_APP" \
    --resource-group "$RESOURCE_GROUP" \
    --query principalId -o tsv 2>/dev/null || true)

if [ -z "$MANAGED_ID" ]; then
    info "Enabling system-assigned managed identity..."
    MANAGED_ID=$(az containerapp identity assign \
        --name "$CONTAINER_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --system-assigned \
        --query principalId -o tsv)
fi

az keyvault set-policy \
    --name "$KEY_VAULT" \
    --object-id "$MANAGED_ID" \
    --secret-permissions get \
    --output none 2>/dev/null || true
ok "Managed identity has Key Vault access"

# ---------------------------------------------------------------------------
# Wire secrets as Key Vault references (no secret values in CLI output)
# ---------------------------------------------------------------------------

info "Configuring Key Vault secret references..."
az containerapp secret set \
    --name "$CONTAINER_APP" \
    --resource-group "$RESOURCE_GROUP" \
    --secrets \
        "aladtec-password=keyvaultref:${VAULT_URL}/secrets/ALADTEC-PASSWORD,identityref:system" \
        "aladtec-username=keyvaultref:${VAULT_URL}/secrets/ALADTEC-USERNAME,identityref:system" \
        "ms-graph-client-secret=keyvaultref:${VAULT_URL}/secrets/MS-GRAPH-CLIENT-SECRET,identityref:system" \
        "entra-mcp-api-client-secret=keyvaultref:${VAULT_URL}/secrets/ENTRA-MCP-API-CLIENT-SECRET,identityref:system" \
        "ispyfire-url=keyvaultref:${VAULT_URL}/secrets/ISPYFIRE-URL,identityref:system" \
        "ispyfire-username=keyvaultref:${VAULT_URL}/secrets/ISPYFIRE-USERNAME,identityref:system" \
        "ispyfire-password=keyvaultref:${VAULT_URL}/secrets/ISPYFIRE-PASSWORD,identityref:system" \
        "neris-client-id=keyvaultref:${VAULT_URL}/secrets/NERIS-CLIENT-ID,identityref:system" \
        "neris-client-secret=keyvaultref:${VAULT_URL}/secrets/NERIS-CLIENT-SECRET,identityref:system" \
        "anthropic-api-key=keyvaultref:${VAULT_URL}/secrets/ANTHROPIC-API-KEY,identityref:system" \
    --output none
ok "Secrets linked to Key Vault"

# ---------------------------------------------------------------------------
# Update image and env vars (full replacement — single source of truth)
# ---------------------------------------------------------------------------

info "Updating Container App image and env vars..."
az containerapp update \
    --name "$CONTAINER_APP" \
    --resource-group "$RESOURCE_GROUP" \
    --image "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${TAG}" \
    --replace-env-vars \
        "ENTRA_MCP_API_TENANT_ID=${MS_GRAPH_TENANT_ID}" \
        "ENTRA_MCP_API_CLIENT_ID=${ENTRA_MCP_API_CLIENT_ID}" \
        "ENTRA_MCP_OFFICER_GROUP_ID=${ENTRA_MCP_OFFICER_GROUP_ID}" \
        "ENTRA_MCP_API_CLIENT_SECRET=secretref:entra-mcp-api-client-secret" \
        "COSMOS_ENDPOINT=${COSMOS_ENDPOINT}" \
        "MS_GRAPH_TENANT_ID=${MS_GRAPH_TENANT_ID}" \
        "MS_GRAPH_CLIENT_ID=${MS_GRAPH_CLIENT_ID}" \
        "MS_GRAPH_CLIENT_SECRET=secretref:ms-graph-client-secret" \
        "ALADTEC_URL=${ALADTEC_URL}" \
        "ALADTEC_USERNAME=secretref:aladtec-username" \
        "ALADTEC_PASSWORD=secretref:aladtec-password" \
        "ISPYFIRE_URL=secretref:ispyfire-url" \
        "ISPYFIRE_USERNAME=secretref:ispyfire-username" \
        "ISPYFIRE_PASSWORD=secretref:ispyfire-password" \
        "NERIS_CLIENT_ID=secretref:neris-client-id" \
        "NERIS_CLIENT_SECRET=secretref:neris-client-secret" \
        "ANTHROPIC_API_KEY=secretref:anthropic-api-key" \
        "MCP_SERVER_URL=https://${CUSTOM_DOMAIN}" \
        "BUILD_VERSION=${TAG}" \
    --output none
ok "Container App updated with ${IMAGE_NAME}:${TAG}"

# ---------------------------------------------------------------------------
# Configure EasyAuth (Azure Container Apps built-in auth)
# ---------------------------------------------------------------------------

info "Configuring EasyAuth..."
az containerapp auth update \
    --name "$CONTAINER_APP" \
    --resource-group "$RESOURCE_GROUP" \
    --unauthenticated-client-action AllowAnonymous \
    --enabled true \
    --output none

az containerapp auth microsoft update \
    --name "$CONTAINER_APP" \
    --resource-group "$RESOURCE_GROUP" \
    --client-id "$ENTRA_MCP_API_CLIENT_ID" \
    --client-secret-name entra-mcp-api-client-secret \
    --tenant-id "$MS_GRAPH_TENANT_ID" \
    --yes \
    --output none
# Extend EasyAuth session cookie to 72 hours (default is 8h).
# authConfigs only supports PUT (not PATCH), so read-modify-write.
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
AUTH_URL="https://management.azure.com/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.App/containerApps/${CONTAINER_APP}/authConfigs/current?api-version=2024-03-01"
AUTH_CONFIG=$(az rest --method get --url "$AUTH_URL" 2>/dev/null || echo '{}')
UPDATED_CONFIG=$(echo "$AUTH_CONFIG" | python3 -c "
import json, sys
cfg = json.load(sys.stdin)
props = cfg.setdefault('properties', {})
login = props.setdefault('login', {})
login['cookieExpiration'] = {
    'convention': 'FixedTime',
    'timeToExpiration': '3.00:00:00',
}
json.dump(cfg, sys.stdout)
")
az rest --method put --url "$AUTH_URL" --body "$UPDATED_CONFIG" --output none
ok "EasyAuth configured (72h session)"

# ---------------------------------------------------------------------------
# Health check — verify the NEW version is serving
# ---------------------------------------------------------------------------

echo ""
info "Waiting for new revision to provision..."
for i in $(seq 1 20); do
    REV_STATE=$(az containerapp revision list \
        --name "$CONTAINER_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --query "sort_by([], &properties.createdTime)[-1].properties.runningState" \
        -o tsv 2>/dev/null || true)
    if [ "$REV_STATE" = "Running" ]; then
        ok "Revision running"
        break
    fi
    printf "  %s (%s)...\r" "$REV_STATE" "${i}"
    sleep 3
done

# ---------------------------------------------------------------------------
# Purge old images (keep latest 5 + latest tag)
# ---------------------------------------------------------------------------

info "Purging old ACR images (keeping 5 most recent)..."
az acr run \
    --registry "$ACR_NAME" \
    --cmd "acr purge --filter '${IMAGE_NAME}:.*' --ago 0d --keep 5 --untagged" \
    /dev/null --output none 2>/dev/null || warn "ACR purge failed (non-critical)"
ok "ACR cleanup complete"

# ---------------------------------------------------------------------------
# Verify version
# ---------------------------------------------------------------------------

info "Verifying version ${TAG} is serving..."
for i in $(seq 1 10); do
    BODY=$(curl -s "https://${CUSTOM_DOMAIN}/health" 2>/dev/null || true)
    VER=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('version',''))" 2>/dev/null || true)
    if [ "$VER" = "$TAG" ]; then
        ok "Version ${TAG} live"
        echo "$BODY" | python3 -m json.tool
        exit 0
    fi
    [ -n "$VER" ] && printf "  serving %s, want %s...\r" "$VER" "$TAG" || printf "  waiting...\r"
    sleep 3
done
warn "Version ${TAG} not confirmed (serving: ${VER:-none})"
