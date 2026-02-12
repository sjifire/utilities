# SJI Fire District Utilities

Utility scripts for SJI Fire District integrations between Aladtec (scheduling) and Microsoft Entra ID (identity management).

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) package manager

## Setup

1. Clone the repository and install dependencies:

```bash
uv sync --group dev
```

2. Create a `.env` file with required credentials:

```bash
# Aladtec credentials
ALADTEC_URL=https://your-org.aladtec.com
ALADTEC_USERNAME=your-username
ALADTEC_PASSWORD=your-password

# Microsoft Graph API credentials
MS_GRAPH_TENANT_ID=your-tenant-id
MS_GRAPH_CLIENT_ID=your-client-id
MS_GRAPH_CLIENT_SECRET=your-client-secret

# iSpyFire credentials
ISPYFIRE_URL=https://your-org.ispyfire.com
ISPYFIRE_USERNAME=your-username
ISPYFIRE_PASSWORD=your-password
```

## CLI Commands

### Aladtec to Entra User Sync

**Sync members to Entra ID:**
```bash
uv run entra-user-sync                      # Sync all active members
uv run entra-user-sync --dry-run            # Preview changes without applying
uv run entra-user-sync --json               # Output results as JSON
uv run entra-user-sync --disable-inactive   # Also disable accounts for inactive members
uv run entra-user-sync --individual EMAIL   # Sync a single member by email
```

The sync:
- Creates new Entra ID accounts for Aladtec members with @sjifire.org emails
- Updates user fields: display name, first/last name, employee ID, phone, hire date
- Sets extension attributes:
  - `extensionAttribute1`: Rank (Captain, Lieutenant, Chief, etc.)
  - `extensionAttribute2`: EVIP expiration date
  - `extensionAttribute3`: Positions (comma-delimited scheduling positions)
  - `extensionAttribute4`: Schedules (comma-delimited schedule visibility)
- Prefixes display names with rank (e.g., "Chief John Smith")
- Automatically backs up Entra ID users before making changes

### Microsoft Group Sync

**Sync groups from Entra ID user data (unified M365 and Exchange):**
```bash
uv run ms-group-sync --all              # Sync all group strategies
uv run ms-group-sync --all --dry-run    # Preview changes without applying
uv run ms-group-sync --strategy stations # Sync only station groups
uv run ms-group-sync --strategy ff --strategy wff  # Sync specific strategies
uv run ms-group-sync --all --new-type m365  # Create new groups as M365 (default: exchange)
```

Available strategies:

| Strategy | Group | Membership Criteria |
|----------|-------|---------------------|
| `stations` | Station 31, 32, etc. | Station assignment (office location) |
| `support` | Support | Has "Support" position |
| `ff` | FF | Has "Firefighter" position |
| `wff` | WFF | Has "Wildland Firefighter" position |
| `ao` | Apparatus Operator | Has EVIP certification |
| `marine` | Marine | Has "Mate" or "Pilot" position |
| `volunteers` | Volunteers | Work Group = "Volunteer" + operational position |
| `mobe` | State Mobilization | Has schedule containing "mobe" (e.g., "State Mobe") |

The sync:
- **Automatically detects** if a group exists as M365 or Exchange
- Syncs existing groups using the appropriate backend (Graph API or PowerShell)
- **Creates new groups as Exchange** mail-enabled security groups by default (no SharePoint sprawl)
- Use `--new-type m365` to create new groups as M365 instead
- Flags conflicts (groups existing in both systems) and skips them
- Adds/removes members based on Aladtec data

**Automated sync:** Runs weekdays at noon Pacific via GitHub Actions. See `.github/workflows/entra-sync.yml`.

### iSpyFire Sync

**Sync operational personnel to iSpyFire:**
```bash
uv run ispyfire-sync --dry-run    # Preview changes without applying
uv run ispyfire-sync              # Apply changes
uv run ispyfire-sync --email user@sjifire.org  # Sync single user
uv run ispyfire-sync -v           # Verbose logging
```

The sync:
- Syncs Entra ID users with operational positions to iSpyFire
- Only includes @sjifire.org users with cell phones
- Positions synced: Firefighter, Apparatus Operator, Support, Wildland Firefighter, Mate, Pilot
- Detects duplicates by name to avoid creating duplicate entries
- Excludes utility/service accounts from automatic removal
- New users receive invite email to set their password
- Deactivation logs out push notifications, removes devices, and disables login
- Reactivation sends password reset email
- Rate limiting handled with tenacity retry logic
- Automatically backs up iSpyFire people before making changes

**Automated sync:** Runs every 30 minutes via GitHub Actions. See `.github/workflows/ispyfire-sync.yml`.

### iSpyFire Admin

**Manage iSpyFire users:**
```bash
uv run ispyfire-admin list                    # List all active users
uv run ispyfire-admin list --inactive         # Include inactive users
uv run ispyfire-admin activate user@sjifire.org   # Reactivate and send password reset
uv run ispyfire-admin deactivate user@sjifire.org # Logout devices and deactivate
```

### Calendar Sync

**Duty calendar sync (On Duty events to shared group calendar):**
```bash
uv run duty-calendar-sync --mailbox all-personnel@sjifire.org --month "Feb 2026" --dry-run
uv run duty-calendar-sync --mailbox all-personnel@sjifire.org --month "Feb 2026"
```

The sync:
- Fetches schedule data from Aladtec for the specified month
- Creates all-day "On Duty" events for each filled position
- Clears existing events in the target date range before creating new ones
- Events include position, section, and Aladtec reference link

**Personal calendar sync (individual schedules to user calendars):**
```bash
uv run personal-calendar-sync --user user@sjifire.org --month "Feb 2026" --dry-run
uv run personal-calendar-sync --user user@sjifire.org --month "Feb 2026"
uv run personal-calendar-sync --all --months 4
uv run personal-calendar-sync --user user@sjifire.org --purge --dry-run
```

The sync:
- Adds events to user's **primary calendar** with orange "Aladtec" category
- Creates the Aladtec category in each user's Outlook automatically
- Creates events matching actual shift times (supports partial shifts)
- Skips entries with empty position (e.g., Trades)
- Compares existing events to avoid unnecessary updates
- Use `--force` to update all events regardless of content changes
- Use `--purge` to delete all Aladtec-categorized events

### Aladtec Tools

**List members:**
```bash
uv run aladtec-list                    # List active members (table format)
uv run aladtec-list --format json      # JSON output
uv run aladtec-list --format csv       # CSV output
uv run aladtec-list --include-inactive # Include inactive members
```

**Audit members:**
```bash
uv run entra-audit                     # Full audit with Entra ID comparison
uv run entra-audit --skip-entra        # Aladtec data quality checks only
```

The audit checks for:
- Members without positions
- Members without @sjifire.org email
- Members without employee ID
- Inactive members
- Aladtec members not in Entra ID
- Entra ID users not in Aladtec
- Entra ID users to deactivate (matched to inactive Aladtec members)

### Exchange Online Prerequisites

For creating new groups (which default to Exchange mail-enabled security groups), you need:

1. PowerShell 7+ (`pwsh`) - see [Installing PowerShell](https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell)
2. ExchangeOnlineManagement module: `pwsh -Command "Install-Module -Name ExchangeOnlineManagement -Force"`
3. Azure AD App Registration with Exchange.ManageAsApp permission
4. Certificate for app-only authentication (stored in Key Vault or local .pfx file)
5. App assigned "Exchange Administrator" role in Azure AD

**Environment variables:**
```bash
# Exchange Online credentials (add to .env)
EXCHANGE_ORGANIZATION=sjifire.org
# Option 1: Certificate thumbprint (Windows with installed cert)
EXCHANGE_CERTIFICATE_THUMBPRINT=your-thumbprint
# Option 2: Certificate file (cross-platform)
EXCHANGE_CERTIFICATE_PATH=/path/to/certificate.pfx
EXCHANGE_CERTIFICATE_PASSWORD=your-cert-password  # empty for Key Vault certs
```

### Entra ID Tools

**Analyze group mappings:**
```bash
uv run analyze-mappings                # Analyze position-to-group mappings
```

## Configuration

### Entra Sync Configuration

Sync settings in `config/entra_sync.json`:

```json
{
  "company_name": "San Juan Island Fire & Rescue",
  "domain": "sjifire.org",
  "skip_emails": ["service-account@sjifire.org"]
}
```

### Group Mappings

Group mappings are configured in `config/group_mappings.json`:

- `ms_365_group_ids`: Microsoft 365 group name to ID mappings
- `ms_security_group_ids`: Security group name to ID mappings
- `position_mappings`: Aladtec position to M365/security group assignments
- `work_group_mappings`: Aladtec work group to M365 group assignments
- `conditional_mappings`: Complex rules (e.g., "Apparatus Operator but not Firefighter")

## GitHub Actions

GitHub Actions authenticate to Azure using OIDC (OpenID Connect) and fetch secrets from Azure Key Vault at runtime. No secrets are stored in GitHub.

### CI (ci.yml)
Runs on push/PR to main:
- Lint with ruff
- Run tests with pytest

### Entra Sync (entra-sync.yml)
Runs weekdays at noon Pacific:
- Syncs Aladtec members to Entra ID users
- Syncs Aladtec data to M365 groups (all strategies)
- Uploads backup artifacts (30-day retention)
- Can be triggered manually with dry-run option

**Secrets (from Key Vault):**
- `ALADTEC-URL`, `ALADTEC-USERNAME`, `ALADTEC-PASSWORD`
- `MS-GRAPH-TENANT-ID`, `MS-GRAPH-CLIENT-ID`, `MS-GRAPH-CLIENT-SECRET`

### iSpyFire Sync (ispyfire-sync.yml)
Runs every 30 minutes:
- Syncs Entra ID users with operational positions to iSpyFire
- Creates new users with invite emails
- Deactivates users no longer in Entra (with device logout)
- Uploads backup artifacts (30-day retention)
- Can be triggered manually with dry-run option

**Secrets (from Key Vault):**
- `MS-GRAPH-TENANT-ID`, `MS-GRAPH-CLIENT-ID`, `MS-GRAPH-CLIENT-SECRET`
- `ISPYFIRE-URL`, `ISPYFIRE-USERNAME`, `ISPYFIRE-PASSWORD`

## Azure Key Vault

All secrets are stored in Azure Key Vault `gh-website-utilities` in resource group `rg-staticweb-prod-westus2`.

### Pull secrets for local development

```bash
./scripts/pull-secrets.sh           # Pull all secrets to .env
./scripts/pull-secrets.sh --list    # List available secrets
./scripts/pull-secrets.sh MS-GRAPH-TENANT-ID ALADTEC-URL  # Pull specific secrets
```

Requires Azure CLI login (`az login`).

### OIDC Configuration

GitHub Actions use the `utilities-sync` app registration with federated credentials:
- `repo:sjifire/utilities:environment:production`

The app has `get` and `list` permissions on the Key Vault secrets.

## Development

### Linting

```bash
uv run ruff check .                    # Check for issues
uv run ruff check . --fix              # Auto-fix issues
uv run ruff format .                   # Format code
```

### Type Checking

```bash
uv run ty check
```

### Testing

```bash
uv run pytest                          # Run all tests
uv run pytest -v                       # Verbose output
uv run pytest --cov=sjifire            # With coverage report
uv run pytest --cov=sjifire --cov-report=html  # HTML coverage report
```

## Project Structure

```
src/sjifire/
├── aladtec/           # Aladtec integration
│   ├── client.py          # HTTP client with login/session management
│   ├── member_scraper.py  # Web scraper for member CSV export
│   ├── models.py          # Member data model
│   └── schedule_scraper.py # Schedule scraper for calendar data
├── core/              # Shared utilities
│   ├── backup.py          # Backup utilities for users and groups
│   ├── config.py          # Configuration loading
│   ├── constants.py       # Position constants (OPERATIONAL_POSITIONS, etc.)
│   ├── group_strategies.py # Group sync strategy classes
│   ├── msgraph_client.py  # MS Graph client
│   └── normalize.py       # Name normalization utilities
├── entra/             # Entra ID integration
│   ├── aladtec_import.py  # Aladtec to Entra user sync logic
│   ├── groups.py          # Group management (create, update, members)
│   └── users.py           # User management
├── exchange/          # Exchange Online integration
│   └── client.py          # PowerShell-based Exchange client
├── ispyfire/          # iSpyFire integration
│   ├── client.py          # API client for iSpyFire
│   ├── models.py          # ISpyFirePerson data model
│   └── sync.py            # Sync logic and comparison
├── calendar/          # Calendar sync
│   ├── models.py          # OnDutyEvent, SyncResult dataclasses
│   ├── duty_sync.py       # DutyCalendarSync for shared mailbox
│   └── personal_sync.py   # PersonalCalendarSync for user calendars
└── scripts/           # CLI entry points
    ├── aladtec_list.py
    ├── analyze_mappings.py
    ├── compare_group_memberships.py
    ├── duty_calendar_sync.py
    ├── entra_audit.py
    ├── entra_user_sync.py
    ├── ispyfire_admin.py
    ├── ispyfire_sync.py
    ├── m365_group_scan.py
    ├── ms_group_sync.py
    └── personal_calendar_sync.py
```
