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
```

## CLI Commands

### Aladtec to Entra Sync

**Sync members to Entra ID:**
```bash
uv run aladtec-import                  # Sync all active members
uv run aladtec-import --dry-run        # Preview changes without applying
uv run aladtec-import --json           # Output results as JSON
uv run aladtec-import --disable-inactive  # Also disable accounts for inactive members
uv run aladtec-import --individual EMAIL  # Sync a single member by email
```

The sync:
- Creates new Entra ID accounts for Aladtec members with @sjifire.org emails
- Updates user fields: display name, first/last name, employee ID, phone, hire date
- Sets extension attributes:
  - `extensionAttribute1`: Rank (Captain, Lieutenant, Chief, etc.)
  - `extensionAttribute2`: EVIP expiration date
  - `extensionAttribute3`: Positions (comma-delimited scheduling positions)
- Prefixes display names with rank (e.g., "Chief John Smith")
- Automatically backs up Entra ID users before making changes

**Automated sync:** Runs daily at 5:00 AM Pacific via GitHub Actions. See `.github/workflows/entra-sync.yml`.

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
uv run aladtec-audit                   # Full audit with Entra ID comparison
uv run aladtec-audit --skip-entra      # Aladtec data quality checks only
```

The audit checks for:
- Members without positions
- Members without @sjifire.org email
- Members without employee ID
- Inactive members
- Aladtec members not in Entra ID
- Entra ID users not in Aladtec
- Entra ID users to deactivate (matched to inactive Aladtec members)

### Entra ID Tools

**Analyze group mappings:**
```bash
uv run analyze-mappings                # Analyze position-to-group mappings
```

**Create security groups:**
```bash
uv run create-security-groups          # Create security groups from config
uv run create-security-groups --dry-run
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

### CI (ci.yml)
Runs on push/PR to main:
- Lint with ruff
- Run tests with pytest

### Entra Sync (entra-sync.yml)
Runs weekdays at noon Pacific:
- Syncs Aladtec members to Entra ID
- Uploads backup artifacts (30-day retention)
- Can be triggered manually with dry-run option

**Required secrets:**
- `ALADTEC_URL`, `ALADTEC_USERNAME`, `ALADTEC_PASSWORD`
- `MS_GRAPH_TENANT_ID`, `MS_GRAPH_CLIENT_ID`, `MS_GRAPH_CLIENT_SECRET`

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
│   ├── models.py      # Member data model
│   └── scraper.py     # Web scraper for CSV export
├── core/              # Shared utilities
│   ├── backup.py      # Backup utilities
│   ├── config.py      # Configuration loading
│   └── msgraph_client.py  # MS Graph client
├── entra/             # Entra ID integration
│   ├── aladtec_import.py  # Aladtec to Entra sync logic
│   ├── groups.py      # Group management
│   └── users.py       # User management
└── scripts/           # CLI entry points
    ├── aladtec_audit.py
    ├── aladtec_import.py
    ├── aladtec_list.py
    ├── analyze_mappings.py
    └── create_security_groups.py
```
