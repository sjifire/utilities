#!/bin/bash
# =============================================================================
# Deploy MCP Server to Azure Container Apps
# =============================================================================
#
# Builds a container image via ACR and updates the running Container App.
# Use this for dev/testing deployments without merging to main.
#
# Usage:
#   ./scripts/deploy-mcp.sh              # Build & deploy
#   ./scripts/deploy-mcp.sh --build-only # Build image without deploying
#   ./scripts/deploy-mcp.sh --health     # Just run health check
#
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration (matches setup-azure.sh and mcp-deploy.yml)
# ---------------------------------------------------------------------------

RESOURCE_GROUP="rg-sjifire-mcp"
ACR_NAME="sjifiremcp"
CONTAINER_APP="sjifire-mcp"
IMAGE_NAME="sjifire-mcp"
CUSTOM_DOMAIN="mcp.sjifire.org"

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
    fail "ACR $ACR_NAME not found. Run setup-azure.sh first."

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
# Deploy
# ---------------------------------------------------------------------------

KEY_VAULT="gh-website-utilities"
VAULT_URL="https://${KEY_VAULT}.vault.azure.net"

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
    --output none
ok "Secrets linked to Key Vault"

# ---------------------------------------------------------------------------
# Update image and env vars
# ---------------------------------------------------------------------------

info "Updating Container App image..."
az containerapp update \
    --name "$CONTAINER_APP" \
    --resource-group "$RESOURCE_GROUP" \
    --image "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${TAG}" \
    --set-env-vars \
        "ENTRA_MCP_API_CLIENT_SECRET=secretref:entra-mcp-api-client-secret" \
        "ISPYFIRE_URL=secretref:ispyfire-url" \
        "ISPYFIRE_USERNAME=secretref:ispyfire-username" \
        "ISPYFIRE_PASSWORD=secretref:ispyfire-password" \
        "NERIS_CLIENT_ID=secretref:neris-client-id" \
        "NERIS_CLIENT_SECRET=secretref:neris-client-secret" \
        "BUILD_VERSION=${TAG}" \
    --output none
ok "Container App updated with ${IMAGE_NAME}:${TAG}"

# ---------------------------------------------------------------------------
# Configure EasyAuth (Azure Container Apps built-in auth)
# ---------------------------------------------------------------------------

info "Configuring EasyAuth..."
EA_CLIENT_ID=$(az keyvault secret show --vault-name "$KEY_VAULT" --name ENTRA-MCP-API-CLIENT-ID --query value -o tsv)
EA_TENANT_ID=$(az keyvault secret show --vault-name "$KEY_VAULT" --name MS-GRAPH-TENANT-ID --query value -o tsv)

az containerapp auth update \
    --name "$CONTAINER_APP" \
    --resource-group "$RESOURCE_GROUP" \
    --unauthenticated-client-action AllowAnonymous \
    --enabled true \
    --output none

az containerapp auth microsoft update \
    --name "$CONTAINER_APP" \
    --resource-group "$RESOURCE_GROUP" \
    --client-id "$EA_CLIENT_ID" \
    --client-secret-name entra-mcp-api-client-secret \
    --tenant-id "$EA_TENANT_ID" \
    --yes \
    --output none
ok "EasyAuth configured"

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
