# Microsoft Forms Programmatic Management

This document describes how to programmatically create and manage Microsoft Forms, including the credentials and approaches available.

## Overview of Options

| Approach | Create Forms | Update Questions | Update Choices | Automation |
|----------|--------------|------------------|----------------|------------|
| Microsoft Graph API | No | Limited | No | Full |
| Power Automate | Yes | Yes | Yes | Full |
| Office Scripts | No | No | No | Limited |
| Manual + Webhooks | Manual | Manual | Manual | Partial |

## Option 1: Power Automate (Recommended)

Power Automate provides the most complete solution for managing Microsoft Forms programmatically.

### Capabilities
- Create new forms
- Add/update questions
- Update dropdown choices
- Trigger flows on file changes (GitHub, SharePoint)
- Run on schedule or via HTTP trigger

### Required Licenses
- Microsoft 365 Business Basic or higher
- Power Automate license (included in M365)
- Premium connectors may require Power Automate Premium ($15/user/month)

### Setup Steps

1. **Create a Power Automate Flow**
   - Go to https://make.powerautomate.com
   - Sign in with your organization account
   - Create a new flow

2. **Trigger Options**
   - **HTTP Trigger**: Allow GitHub webhooks to trigger updates
   - **Schedule**: Run periodically to sync changes
   - **When a file is modified**: Watch SharePoint/OneDrive for config file changes

3. **Use Microsoft Forms Connector**
   - Action: "Create a form"
   - Action: "Create a question"
   - Action: "Get form details"

### Example: GitHub Webhook → Update Form

```json
{
  "trigger": {
    "type": "http",
    "method": "POST"
  },
  "actions": [
    {
      "type": "Parse JSON",
      "inputs": {
        "content": "@triggerBody()"
      }
    },
    {
      "type": "Microsoft Forms - Get form",
      "inputs": {
        "formId": "your-form-id"
      }
    },
    {
      "type": "Microsoft Forms - Update question",
      "inputs": {
        "formId": "your-form-id",
        "questionId": "apparatus-question-id",
        "choices": "@body('Parse_JSON')?['apparatus']"
      }
    }
  ]
}
```

## Option 2: Microsoft Graph API

The Graph API has limited support for Forms management.

### Available Endpoints (Read-Only Mostly)
- `GET /me/forms` - List forms
- `GET /me/forms/{formId}` - Get form details
- `GET /me/forms/{formId}/questions` - Get questions
- `GET /me/forms/{formId}/responses` - Get responses

### Limitations
- **Cannot create forms** via Graph API
- **Cannot modify questions** via Graph API
- **Cannot update choices** via Graph API
- Mainly useful for reading form data and responses

### Required Permissions (Azure AD App Registration)
```
Forms.Read.All
Forms.ReadWrite.All (if available)
```

### Credential Types

#### Delegated Permissions (User Context)
- User must consent to app permissions
- Best for interactive applications
- Supports all Forms operations that user can do

#### Application Permissions (Service Account)
- Limited Forms support
- Cannot create/modify forms
- Good for reading responses

## Option 3: Hybrid Approach (Recommended for Your Use Case)

Combine Power Automate with GitHub for the best automation:

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        GitHub Repository                         │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ apparatus.csv   │  │ personnel.json  │  │ form-spec.json  │  │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  │
└───────────┼─────────────────────┼─────────────────────┼──────────┘
            │                     │                     │
            ▼                     ▼                     ▼
     ┌──────────────────────────────────────────────────────┐
     │              GitHub Actions Workflow                  │
     │  • Triggers on push to config files                  │
     │  • Sends webhook to Power Automate                   │
     └────────────────────────┬─────────────────────────────┘
                              │
                              ▼
     ┌──────────────────────────────────────────────────────┐
     │              Power Automate Flow                      │
     │  • Receives webhook with updated data                │
     │  • Updates Microsoft Form questions/choices          │
     └────────────────────────┬─────────────────────────────┘
                              │
                              ▼
     ┌──────────────────────────────────────────────────────┐
     │              Microsoft Form                           │
     │  • Fire Incident Report                              │
     │  • Updated apparatus/personnel dropdowns             │
     └──────────────────────────────────────────────────────┘
```

### Implementation Steps

1. **Set up Power Automate Flow with HTTP Trigger**
   - Create flow with "When an HTTP request is received" trigger
   - Copy the HTTP POST URL
   - Add Microsoft Forms actions to update questions

2. **Create GitHub Actions Workflow**
   - Trigger on push to `config/` directory
   - Send webhook to Power Automate with updated data

3. **Store Secrets**
   - Add Power Automate URL as GitHub secret
   - Keep sensitive data out of repository

## Credentials Required

### For Power Automate
| Credential | How to Get | Where to Store |
|------------|-----------|----------------|
| M365 Account | Organization admin creates | Power Automate login |
| Forms permissions | Automatic with M365 | N/A |

### For GitHub Actions
| Credential | How to Get | Where to Store |
|------------|-----------|----------------|
| Power Automate URL | Flow designer | GitHub Secrets |
| GitHub Token | Automatic | `${{ secrets.GITHUB_TOKEN }}` |

### For Microsoft Graph API (Optional)
| Credential | How to Get | Where to Store |
|------------|-----------|----------------|
| Tenant ID | Azure Portal > Entra ID | GitHub Secrets / .env |
| Client ID | App Registration | GitHub Secrets / .env |
| Client Secret | App Registration | GitHub Secrets / .env |

## Setting Up Power Automate for Forms Management

### Step 1: Create the Power Automate Flow

1. Go to https://make.powerautomate.com
2. Click **+ Create** > **Instant cloud flow**
3. Name: "Update Incident Form from GitHub"
4. Trigger: "When an HTTP request is received"
5. Click **Create**

### Step 2: Configure HTTP Trigger

1. In the trigger, set Request Body JSON Schema:
```json
{
  "type": "object",
  "properties": {
    "updateType": {
      "type": "string"
    },
    "apparatus": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "code": { "type": "string" },
          "name": { "type": "string" }
        }
      }
    },
    "personnel": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "esoId": { "type": "string" },
          "fullName": { "type": "string" }
        }
      }
    }
  }
}
```

2. Save the flow to get the HTTP POST URL

### Step 3: Add Form Update Actions

1. Add action: **Microsoft Forms** > **Get form details**
2. Add action: **Microsoft Forms** > **Update a question** (for each dropdown)
3. Use expressions to build choice lists from the trigger data

### Step 4: Create GitHub Actions Workflow

Create `.github/workflows/update-forms.yml`:
```yaml
name: Update Microsoft Forms

on:
  push:
    paths:
      - 'config/apparatus.csv'
      - 'config/personnel.json'

jobs:
  update-forms:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Parse config files
        id: parse
        run: |
          # Parse apparatus.csv
          APPARATUS=$(node -e "
            const fs = require('fs');
            const csv = fs.readFileSync('config/apparatus.csv', 'utf-8');
            const lines = csv.split('\n').slice(1).filter(l => l.trim());
            const items = lines.map(l => {
              const [code, name] = l.split(',');
              return { code, name: name?.replace(/\"/g, '') };
            });
            console.log(JSON.stringify(items));
          ")
          echo "apparatus=$APPARATUS" >> $GITHUB_OUTPUT

          # Parse personnel.json
          PERSONNEL=$(cat config/personnel.json | jq -c '.personnel | map({esoId, fullName})')
          echo "personnel=$PERSONNEL" >> $GITHUB_OUTPUT

      - name: Trigger Power Automate
        run: |
          curl -X POST "${{ secrets.POWER_AUTOMATE_URL }}" \
            -H "Content-Type: application/json" \
            -d '{
              "updateType": "config",
              "apparatus": ${{ steps.parse.outputs.apparatus }},
              "personnel": ${{ steps.parse.outputs.personnel }}
            }'
```

## Alternative: Forms REST API (Undocumented)

Microsoft Forms has an internal REST API that Power Automate uses. This is **not officially supported** but can be explored:

Base URL: `https://forms.office.com/formapi/api/`

Endpoints (may change without notice):
- `GET /{tenantId}/users/{userId}/forms` - List forms
- `GET /{tenantId}/users/{userId}/forms/{formId}` - Get form
- `PATCH /{tenantId}/users/{userId}/forms/{formId}/questions/{questionId}` - Update question

**Warning**: Using undocumented APIs is not recommended for production systems.

## Recommendation Summary

For your use case (updating apparatus and personnel dropdowns when config files change):

1. **Create the form manually** in Microsoft Forms
2. **Set up Power Automate** with HTTP trigger
3. **Configure GitHub Actions** to send webhooks when config files change
4. **Test the full flow** with a small change

This approach:
- Uses fully supported Microsoft services
- Provides reliable automation
- Requires minimal ongoing maintenance
- Works within your existing M365 environment
