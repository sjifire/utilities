#!/bin/bash
# =============================================================================
# Deploy Ops Server to Azure Container Apps
# =============================================================================
#
# Builds a container image via ACR and updates the running Container App.
# Used by both local dev deploys and GitHub Actions (ops-deploy.yml).
#
# Optimized for fast rollout: image update fires first, housekeeping runs
# in parallel while the new revision provisions.
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
CONTAINER_APP="sjifire-ops"
IMAGE_NAME="sjifire-ops"
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

FULL_IMAGE="${ACR_LOGIN_SERVER}/${IMAGE_NAME}"

if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
    info "Building image locally (Docker)..."
    az acr login --name "$ACR_NAME" --output none

    docker build --platform linux/amd64 \
        -t "${FULL_IMAGE}:${TAG}" \
        -t "${FULL_IMAGE}:latest" \
        .

    info "Pushing image to ACR..."
    docker push "${FULL_IMAGE}:${TAG}" --quiet
    docker push "${FULL_IMAGE}:latest" --quiet
    ok "Image built & pushed: ${IMAGE_NAME}:${TAG}"
else
    info "Building image via ACR (no local Docker)..."
    az acr build \
        --registry "$ACR_NAME" \
        --image "${IMAGE_NAME}:${TAG}" \
        --image "${IMAGE_NAME}:latest" \
        . \
        --output none
    ok "Image built: ${IMAGE_NAME}:${TAG}"
fi

if [ "$BUILD_ONLY" = true ]; then
    ok "Build-only mode — skipping deploy"
    exit 0
fi

# ---------------------------------------------------------------------------
# Temp dir for parallel output
# ---------------------------------------------------------------------------

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# ---------------------------------------------------------------------------
# Fetch config from Key Vault (parallel — ~12s → ~2s)
# ---------------------------------------------------------------------------

VAULT_URL="https://${KEY_VAULT}.vault.azure.net"

_get_secret() {
    az keyvault secret show --vault-name "$KEY_VAULT" --name "$1" --query value -o tsv 2>/dev/null || echo ""
}

info "Fetching config from Key Vault..."
_get_secret "ENTRA-MCP-API-CLIENT-ID"      > "$TMPDIR/kv-1" &
_get_secret "ENTRA-REPORT-EDITORS-GROUP-ID" > "$TMPDIR/kv-2" &
_get_secret "COSMOS-ENDPOINT"               > "$TMPDIR/kv-3" &
_get_secret "MS-GRAPH-TENANT-ID"            > "$TMPDIR/kv-4" &
_get_secret "MS-GRAPH-CLIENT-ID"            > "$TMPDIR/kv-5" &
_get_secret "ALADTEC-URL"                   > "$TMPDIR/kv-6" &
_get_secret "AZURE-STORAGE-ACCOUNT-URL"     > "$TMPDIR/kv-7" &
_get_secret "AZURE-STORAGE-ACCOUNT-KEY"     > "$TMPDIR/kv-8" &
wait
ENTRA_MCP_API_CLIENT_ID=$(cat "$TMPDIR/kv-1")
ENTRA_REPORT_EDITORS_GROUP_ID=$(cat "$TMPDIR/kv-2")
COSMOS_ENDPOINT=$(cat "$TMPDIR/kv-3")
MS_GRAPH_TENANT_ID=$(cat "$TMPDIR/kv-4")
MS_GRAPH_CLIENT_ID=$(cat "$TMPDIR/kv-5")
ALADTEC_URL=$(cat "$TMPDIR/kv-6")
AZURE_STORAGE_ACCOUNT_URL=$(cat "$TMPDIR/kv-7")
AZURE_STORAGE_ACCOUNT_KEY=$(cat "$TMPDIR/kv-8")
ok "Config fetched"

# ---------------------------------------------------------------------------
# First-deploy check — run prerequisites if secrets not yet configured
# ---------------------------------------------------------------------------

EXISTING_SECRETS=$(az containerapp secret list \
    --name "$CONTAINER_APP" --resource-group "$RESOURCE_GROUP" \
    --query "length(@)" -o tsv 2>/dev/null || echo "0")

if [ "$EXISTING_SECRETS" -lt 5 ]; then
    warn "First deploy detected (${EXISTING_SECRETS} secrets) — configuring prerequisites..."

    # Ensure managed identity
    info "Enabling managed identity..."
    MANAGED_ID=$(az containerapp identity show \
        --name "$CONTAINER_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --query principalId -o tsv 2>/dev/null || true)

    if [ -z "$MANAGED_ID" ]; then
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

    # Configure secrets before the update
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
            "cosmos-key=keyvaultref:${VAULT_URL}/secrets/COSMOS-KEY,identityref:system" \
            "azure-maps-key=keyvaultref:${VAULT_URL}/secrets/AZURE-MAPS-KEY,identityref:system" \
            "kiosk-signing-key=keyvaultref:${VAULT_URL}/secrets/KIOSK-SIGNING-KEY,identityref:system" \
            "centrifugo-api-key=keyvaultref:${VAULT_URL}/secrets/CENTRIFUGO-API-KEY,identityref:system" \
        --output none
    ok "Secrets configured"
fi

# ---------------------------------------------------------------------------
# Update via YAML template — starts the rollout
# ---------------------------------------------------------------------------

YAML_TEMPLATE="$(cd "$(dirname "$0")/.." && pwd)/containerapp.yaml"
if [ ! -f "$YAML_TEMPLATE" ]; then
    fail "containerapp.yaml not found at $YAML_TEMPLATE"
fi

# Substitute placeholders into a temp copy
YAML_RENDERED="$TMPDIR/containerapp-rendered.yaml"
sed \
    -e "s|__IMAGE_TAG__|${TAG}|g" \
    -e "s|__MS_GRAPH_TENANT_ID__|${MS_GRAPH_TENANT_ID}|g" \
    -e "s|__ENTRA_MCP_API_CLIENT_ID__|${ENTRA_MCP_API_CLIENT_ID}|g" \
    -e "s|__ENTRA_REPORT_EDITORS_GROUP_ID__|${ENTRA_REPORT_EDITORS_GROUP_ID}|g" \
    -e "s|__COSMOS_ENDPOINT__|${COSMOS_ENDPOINT}|g" \
    -e "s|__MS_GRAPH_CLIENT_ID__|${MS_GRAPH_CLIENT_ID}|g" \
    -e "s|__ALADTEC_URL__|${ALADTEC_URL}|g" \
    -e "s|__MCP_SERVER_URL__|https://${CUSTOM_DOMAIN}|g" \
    -e "s|__AZURE_STORAGE_ACCOUNT_URL__|${AZURE_STORAGE_ACCOUNT_URL}|g" \
    -e "s|__AZURE_STORAGE_ACCOUNT_KEY__|${AZURE_STORAGE_ACCOUNT_KEY}|g" \
    "$YAML_TEMPLATE" > "$YAML_RENDERED"

info "Updating Container App via YAML (ops-server + centrifugo sidecar)..."
az containerapp update \
    --name "$CONTAINER_APP" \
    --resource-group "$RESOURCE_GROUP" \
    --yaml "$YAML_RENDERED" \
    --output none
ok "Container App updated with ${IMAGE_NAME}:${TAG} — rollout started"

# ---------------------------------------------------------------------------
# Housekeeping (parallel, while revision provisions)
# ---------------------------------------------------------------------------

_housekeeping_identity() {
    MANAGED_ID=$(az containerapp identity show \
        --name "$CONTAINER_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --query principalId -o tsv 2>/dev/null || true)

    if [ -z "$MANAGED_ID" ]; then
        MANAGED_ID=$(az containerapp identity assign \
            --name "$CONTAINER_APP" \
            --resource-group "$RESOURCE_GROUP" \
            --system-assigned \
            --query principalId -o tsv 2>/dev/null || true)
    fi

    if [ -n "$MANAGED_ID" ]; then
        az keyvault set-policy \
            --name "$KEY_VAULT" \
            --object-id "$MANAGED_ID" \
            --secret-permissions get \
            --output none 2>/dev/null || true
    fi
    echo "ok" > "$TMPDIR/hk-identity"
}

_housekeeping_secrets() {
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
            "cosmos-key=keyvaultref:${VAULT_URL}/secrets/COSMOS-KEY,identityref:system" \
            "azure-maps-key=keyvaultref:${VAULT_URL}/secrets/AZURE-MAPS-KEY,identityref:system" \
            "kiosk-signing-key=keyvaultref:${VAULT_URL}/secrets/KIOSK-SIGNING-KEY,identityref:system" \
            "centrifugo-api-key=keyvaultref:${VAULT_URL}/secrets/CENTRIFUGO-API-KEY,identityref:system" \
        --output none 2>/dev/null || true
    echo "ok" > "$TMPDIR/hk-secrets"
}

_housekeeping_easyauth() {
    az containerapp auth update \
        --name "$CONTAINER_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --unauthenticated-client-action AllowAnonymous \
        --enabled true \
        --output none 2>/dev/null || true

    az containerapp auth microsoft update \
        --name "$CONTAINER_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --client-id "$ENTRA_MCP_API_CLIENT_ID" \
        --client-secret-name entra-mcp-api-client-secret \
        --tenant-id "$MS_GRAPH_TENANT_ID" \
        --yes \
        --output none 2>/dev/null || true
    echo "ok" > "$TMPDIR/hk-easyauth"
}

_housekeeping_job() {
    CA_JOB="sjifire-ops-tasks"
    if az containerapp job show --name "$CA_JOB" --resource-group "$RESOURCE_GROUP" &>/dev/null; then
        az containerapp job update \
            --name "$CA_JOB" \
            --resource-group "$RESOURCE_GROUP" \
            --image "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${TAG}" \
            --output none 2>/dev/null || true
        echo "ok" > "$TMPDIR/hk-job"
    else
        echo "skip" > "$TMPDIR/hk-job"
    fi
}

_housekeeping_acr_purge() {
    az acr run \
        --registry "$ACR_NAME" \
        --cmd "acr purge --filter '${IMAGE_NAME}:.*' --ago 0d --keep 5 --untagged" \
        /dev/null --output none 2>/dev/null || true
    echo "ok" > "$TMPDIR/hk-acr"
}

info "Running housekeeping in parallel..."
_housekeeping_identity   > "$TMPDIR/hk-identity-log" 2>&1 &
_housekeeping_secrets    > "$TMPDIR/hk-secrets-log"  2>&1 &
_housekeeping_easyauth   > "$TMPDIR/hk-easyauth-log" 2>&1 &
_housekeeping_job        > "$TMPDIR/hk-job-log"      2>&1 &
_housekeeping_acr_purge  > "$TMPDIR/hk-acr-log"      2>&1 &
wait

# Report housekeeping results
[ -f "$TMPDIR/hk-identity" ] && ok "Managed identity verified"  || warn "Managed identity check had issues"
[ -f "$TMPDIR/hk-secrets" ]  && ok "Secret refs verified"       || warn "Secret refs had issues"
[ -f "$TMPDIR/hk-easyauth" ] && ok "EasyAuth verified"          || warn "EasyAuth had issues"
if [ -f "$TMPDIR/hk-job" ]; then
    JOB_RESULT=$(cat "$TMPDIR/hk-job")
    [ "$JOB_RESULT" = "ok" ] && ok "CA Job updated" || warn "CA Job not found — skipping"
else
    warn "CA Job update had issues"
fi
[ -f "$TMPDIR/hk-acr" ] && ok "ACR cleanup complete" || warn "ACR purge had issues"

ok "Housekeeping complete"

# ---------------------------------------------------------------------------
# Verify version
# ---------------------------------------------------------------------------

echo ""
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
