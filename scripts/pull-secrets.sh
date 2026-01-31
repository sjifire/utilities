#!/bin/bash
# Fetches secrets from Azure Key Vault and writes to .env
# Requires: az cli logged in (run 'az login' first)
#
# Usage:
#   ./scripts/pull-secrets.sh              # Pull all secrets to .env
#   ./scripts/pull-secrets.sh --all        # Same as above
#   ./scripts/pull-secrets.sh SECRET-NAME  # Pull specific secret(s)
#   ./scripts/pull-secrets.sh --list       # List available secrets

set -e

VAULT="gh-website-utilities"
OUTPUT_FILE=".env"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if az cli is logged in
if ! az account show &>/dev/null; then
    echo -e "${RED}Error: Not logged in to Azure CLI${NC}"
    echo "Run 'az login' first"
    exit 1
fi

# List secrets
if [ "$1" = "--list" ]; then
    echo "Available secrets in $VAULT:"
    az keyvault secret list --vault-name "$VAULT" --query "[].name" -o tsv | sort
    exit 0
fi

# Determine which secrets to fetch
if [ $# -eq 0 ] || [ "$1" = "--all" ]; then
    echo -e "${YELLOW}Fetching all secrets from $VAULT...${NC}"
    SECRETS=$(az keyvault secret list --vault-name "$VAULT" --query "[].name" -o tsv)
else
    SECRETS="$@"
fi

# Clear or create .env file
> "$OUTPUT_FILE"

# Fetch each secret
for name in $SECRETS; do
    echo -n "  $name... "
    value=$(az keyvault secret show --vault-name "$VAULT" --name "$name" --query value -o tsv 2>/dev/null)
    if [ $? -eq 0 ] && [ -n "$value" ]; then
        # Convert hyphens to underscores for env var name
        env_name=$(echo "$name" | tr '-' '_')
        echo "${env_name}=${value}" >> "$OUTPUT_FILE"
        echo -e "${GREEN}OK${NC}"
    else
        echo -e "${RED}FAILED${NC}"
    fi
done

echo -e "\n${GREEN}Secrets written to $OUTPUT_FILE${NC}"
echo -e "${YELLOW}Remember: Don't commit .env to git!${NC}"
