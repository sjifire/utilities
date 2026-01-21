# Power Automate Setup Guide

This guide walks you through setting up a Power Automate flow to automatically update your Microsoft Form when configuration files change in GitHub.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              GitHub Repository                               │
│                         github.com/sjifire/utilities                         │
│                                                                              │
│   ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐        │
│   │ config/         │    │ config/         │    │ .github/        │        │
│   │ apparatus.csv   │    │ personnel.json  │    │ workflows/      │        │
│   │                 │    │                 │    │ update-ms-      │        │
│   │ BN31,Battalion  │    │ {               │    │ forms.yml       │        │
│   │ E31,Engine 31   │    │   "personnel":  │    │                 │        │
│   │ L31,Ladder 31   │    │   [...]         │    │                 │        │
│   │ ...             │    │ }               │    │                 │        │
│   └────────┬────────┘    └────────┬────────┘    └────────┬────────┘        │
│            │                      │                      │                  │
│            └──────────────────────┼──────────────────────┘                  │
│                                   │                                          │
│                          On Push to main                                     │
└───────────────────────────────────┼──────────────────────────────────────────┘
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                           GitHub Actions Workflow                             │
│                                                                               │
│  1. Checkout repository                                                       │
│  2. Parse apparatus.csv → JSON array                                          │
│  3. Parse personnel.json → JSON array                                         │
│  4. Build payload with choices formatted for Forms                            │
│  5. POST to Power Automate HTTP trigger URL                                   │
│                                                                               │
└───────────────────────────────────┬───────────────────────────────────────────┘
                                    │
                                    │ HTTP POST (JSON payload)
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                           Power Automate Flow                                 │
│                     "Update Fire Incident Form"                               │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │ TRIGGER: When an HTTP request is received                               │ │
│  │          POST https://prod-XX.westus.logic.azure.com/workflows/...      │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                    │                                          │
│                                    ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │ ACTION: Parse JSON                                                       │ │
│  │         Extract apparatus[] and personnel[] from request body            │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                    │                                          │
│                                    ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │ ACTION: Update Form Question - "Unit/Apparatus Name"                     │ │
│  │         Set choices to apparatusChoices array                            │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                    │                                          │
│                                    ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │ ACTION: Update Form Question - "Personnel on Unit"                       │ │
│  │         Set choices to personnelChoices array                            │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                    │                                          │
│                                    ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │ ACTION: Response (200 OK)                                                │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                               │
└───────────────────────────────────┬───────────────────────────────────────────┘
                                    │
                                    │ Updates
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                           Microsoft Form                                      │
│                     "Fire Incident Report"                                    │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │ Question: Unit/Apparatus Name                    [Dropdown ▼]            │ │
│  │           ├─ BN31 - Battalion 31                                         │ │
│  │           ├─ E31 - Engine 31                                             │ │
│  │           ├─ E32 - Engine 32                                             │ │
│  │           └─ ... (from apparatus.csv)                                    │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │ Question: Personnel on Unit                      [Dropdown ▼]            │ │
│  │           ├─ Adam Greene - 231                                           │ │
│  │           ├─ Brad Smith - 252                                            │ │
│  │           ├─ Chad Warmenhoven - 2114                                     │ │
│  │           └─ ... (from personnel.json)                                   │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                               │
└───────────────────────────────────────────────────────────────────────────────┘
```

## Step-by-Step Setup

### Prerequisites
- Microsoft 365 account with Power Automate access
- A Microsoft Form created (we'll create one together)
- Admin access to the GitHub repository

---

## Part 1: Create the Microsoft Form

### Step 1.1: Go to Microsoft Forms
1. Open https://forms.microsoft.com
2. Sign in with your Microsoft 365 account

### Step 1.2: Create a New Form
1. Click **+ New Form**
2. Click on "Untitled form" and rename to: **Fire Incident Report**
3. Add a description: "Incident report form for San Juan Island Fire & Rescue"

### Step 1.3: Add the Apparatus Question
1. Click **+ Add new**
2. Select **Choice**
3. Set the question text: **Unit/Apparatus Name**
4. Add initial options (these will be replaced by automation):
   - BN31 - Battalion 31
   - E31 - Engine 31
   - E32 - Engine 32
5. Toggle **Required** ON
6. Click the **...** menu → **Drop-down** (to make it a dropdown instead of radio buttons)

### Step 1.4: Add the Personnel Question
1. Click **+ Add new**
2. Select **Choice**
3. Set the question text: **Personnel on Unit**
4. Add initial options:
   - Placeholder 1
   - Placeholder 2
5. Toggle **Multiple answers** ON (if multiple personnel can be selected)
6. Click the **...** menu → **Drop-down**

### Step 1.5: Get the Form ID
1. Click **Share** in the top right
2. Look at the URL - it will look like:
   ```
   https://forms.office.com/Pages/ResponsePage.aspx?id=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX
   ```
3. Copy the ID (the part after `id=`) - you'll need this for Power Automate

---

## Part 2: Create the Power Automate Flow

### Step 2.1: Open Power Automate
1. Go to https://make.powerautomate.com
2. Sign in with the same Microsoft 365 account

### Step 2.2: Create a New Flow
1. Click **+ Create** in the left sidebar
2. Select **Instant cloud flow**
3. Name: **Update Fire Incident Form**
4. Under "Choose how to trigger this flow", select **When an HTTP request is received**
5. Click **Create**

### Step 2.3: Configure the HTTP Trigger
1. Click on the trigger box to expand it
2. In **Request Body JSON Schema**, paste:

```json
{
    "type": "object",
    "properties": {
        "updateType": {
            "type": "string"
        },
        "timestamp": {
            "type": "string"
        },
        "commit": {
            "type": "string"
        },
        "apparatus": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "station": {"type": "string"},
                    "active": {"type": "boolean"}
                }
            }
        },
        "personnel": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "esoId": {"type": "string"},
                    "fullName": {"type": "string"},
                    "lastName": {"type": "string"},
                    "firstName": {"type": "string"}
                }
            }
        },
        "apparatusChoices": {
            "type": "array",
            "items": {"type": "string"}
        },
        "personnelChoices": {
            "type": "array",
            "items": {"type": "string"}
        }
    }
}
```

### Step 2.4: Add Parse JSON Action (Optional but Recommended)
1. Click **+ New step**
2. Search for **Parse JSON**
3. In **Content**, select the trigger body: `triggerBody()`
4. In **Schema**, use the same schema from above

### Step 2.5: Add "Get form details" Action
1. Click **+ New step**
2. Search for **Microsoft Forms**
3. Select **Get form details**
4. In **Form Id**, enter your form ID from Step 1.5
5. This will show you the question IDs you need

### Step 2.6: Add "Update question" Actions

**For Apparatus Question:**
1. Click **+ New step**
2. Search for **Microsoft Forms**
3. Select **Update a choice question** (or similar)
4. **Form Id**: Your form ID
5. **Question Id**: The ID for "Unit/Apparatus Name" (from Get form details)
6. **Choices**: Select `apparatusChoices` from the parsed JSON

**For Personnel Question:**
1. Click **+ New step**
2. Repeat for the Personnel question using `personnelChoices`

### Step 2.7: Add Response Action
1. Click **+ New step**
2. Search for **Response**
3. **Status Code**: 200
4. **Body**:
```json
{
  "status": "success",
  "updated": "@{utcNow()}"
}
```

### Step 2.8: Save and Get the URL
1. Click **Save** in the top right
2. Click on the HTTP trigger to expand it
3. Copy the **HTTP POST URL** - it will look like:
   ```
   https://prod-XX.westus.logic.azure.com:443/workflows/XXXXX/triggers/manual/paths/invoke?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=XXXXX
   ```

---

## Part 3: Connect GitHub

### Step 3.1: Add Power Automate URL to GitHub
1. Go to your GitHub repository: https://github.com/sjifire/utilities
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **Variables** tab
4. Click **New repository variable**
5. **Name**: `POWER_AUTOMATE_URL`
6. **Value**: Paste the HTTP POST URL from Power Automate
7. Click **Add variable**

### Step 3.2: Test the Workflow
1. Make a small change to `config/apparatus.csv` (e.g., add a comment line)
2. Commit and push to main
3. Go to **Actions** tab in GitHub to see the workflow run
4. Check your Microsoft Form to see if the dropdowns updated

---

## Part 4: Local Testing

### Step 4.1: Set Up Local Environment
```bash
cd /path/to/utilities
cp .env.example .env
# Edit .env with your credentials
```

### Step 4.2: Test Locally
```bash
# Test the payload generation
node test-form-update.mjs --dry-run

# Actually trigger Power Automate
node test-form-update.mjs
```

---

## Troubleshooting

### Flow Not Triggering
1. Check the GitHub Actions log for errors
2. Verify the Power Automate URL is correct
3. Make sure the flow is turned **On** in Power Automate

### Form Not Updating
1. Check the Power Automate run history for errors
2. Verify the Form ID and Question IDs are correct
3. Make sure you have edit permissions on the form

### Invalid JSON Error
1. Check that your config files are valid JSON/CSV
2. Look at the GitHub Actions log to see the parsed payload

---

## Alternative: Microsoft Forms Pro (Dynamics 365)

If you need more advanced form management, Microsoft Forms Pro (now Dynamics 365 Customer Voice) has a more complete API. However, it requires additional licensing.

---

## Security Notes

1. **Keep the Power Automate URL secret** - anyone with the URL can trigger your flow
2. The URL includes a signature (`sig=`) that acts as authentication
3. Consider adding additional validation in your flow (check for expected fields)
