# Dispatch Email Setup

This guide covers setting up the email dispatch system that receives incident reports from SJI County dispatch.

## Prerequisites

- Azure CLI (`az`) - [Install](https://aka.ms/installazurecli)
- Azure Functions Core Tools (`func`) - [Install](https://aka.ms/azure-functions-core-tools)
- Microsoft 365 mailbox for `dispatch_incident_report@sjifire.org`

## 1. Azure Resources (One-Time Setup)

Log in to Azure CLI:

```bash
az login
```

Create the resources:

```bash
# Resource group
az group create \
  --name sjifire-dispatch-rg \
  --location westus2

# Storage account (name must be globally unique, lowercase, no hyphens)
az storage account create \
  --name sjifiredispatchstor \
  --resource-group sjifire-dispatch-rg \
  --location westus2 \
  --sku Standard_LRS

# Function app
az functionapp create \
  --name sjifire-dispatch-func \
  --resource-group sjifire-dispatch-rg \
  --storage-account sjifiredispatchstor \
  --consumption-plan-location westus2 \
  --runtime python \
  --runtime-version 3.11 \
  --functions-version 4 \
  --os-type Linux
```

## 2. Configure App Settings

Copy your credentials to the Function App:

```bash
az functionapp config appsettings set \
  --name sjifire-dispatch-func \
  --resource-group sjifire-dispatch-rg \
  --settings \
    MS_GRAPH_TENANT_ID="<your-tenant-id>" \
    MS_GRAPH_CLIENT_ID="<your-client-id>" \
    MS_GRAPH_CLIENT_SECRET="<your-client-secret>" \
    DISPATCH_MAILBOX_USER_ID="<mailbox-user-id>"
```

To get the mailbox user ID:

```bash
az rest --method GET \
  --url "https://graph.microsoft.com/v1.0/users/dispatch_incident_report@sjifire.org" \
  --query id --output tsv
```

## 3. Deploy and Subscribe

Deploy the function code and create the MS Graph subscription:

```bash
uv run dispatch-setup --deploy --subscribe
```

## CLI Reference

```bash
# Deploy function code to Azure
uv run dispatch-setup --deploy

# Create/refresh MS Graph webhook subscription
uv run dispatch-setup --subscribe

# List existing subscriptions
uv run dispatch-setup --list

# Deploy and subscribe in one command
uv run dispatch-setup --deploy --subscribe
```

## Subscription Renewal

MS Graph mail subscriptions expire after **3 days maximum**. The function should handle renewal automatically, but you can manually refresh with:

```bash
uv run dispatch-setup --subscribe
```

## Local Development

Start the function locally:

```bash
cd functions
func start
```

Note: Local development requires [ngrok](https://ngrok.com/) or similar to expose the webhook URL for MS Graph subscriptions.

## Cleanup

To delete all Azure resources:

```bash
az group delete --name sjifire-dispatch-rg --yes
```
