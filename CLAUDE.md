# Claude Code Context

This file provides context for Claude Code and other AI assistants working on this project.

## Project Overview

SJI Fire District utilities for syncing personnel data between Aladtec (scheduling/workforce management) and Microsoft Entra ID (identity management).

## Tech Stack

- **Python 3.14** with type hints
- **uv** for package management
- **msgraph-sdk** for Microsoft Graph API
- **httpx** + **beautifulsoup4** for Aladtec web scraping
- **pytest** + **pytest-asyncio** for testing
- **ruff** for linting/formatting
- **ty** for type checking

## Key Concepts

### Aladtec
- Workforce scheduling system used by fire departments
- Data accessed via web scraping (no official API)
- Members have positions (scheduling roles like "Firefighter", "EMT")
- Members have Employee Type field (often matches rank)
- Members have Title field (more specific rank like "Battalion Chief")

### Entra ID (Azure AD)
- Microsoft identity platform
- Users have standard fields plus extension attributes (extensionAttribute1-15)
- Extension attributes used for custom fire department fields
- Graph API v1.0 used for all operations

### Field Mappings
- `extensionAttribute1`: Rank (Captain, Lieutenant, Chief, etc.)
- `extensionAttribute2`: EVIP expiration date
- `extensionAttribute3`: Positions (comma-delimited scheduling positions)

### Rank Hierarchy
Ranks are extracted from Title or Employee Type fields:
```
Battalion Chief, Assistant Chief, Fire Chief, Chief,
Captain, Lieutenant, Firefighter, EMT
```

Display names are prefixed with shortened rank (e.g., "Chief John Smith" for "Battalion Chief").

## Architecture

```
src/sjifire/
├── aladtec/
│   ├── models.py      # Member dataclass with rank/display_rank properties
│   └── scraper.py     # Web scraper, handles login, CSV export, position enrichment
├── core/
│   ├── backup.py      # JSON backup before sync operations
│   ├── config.py      # EntraSyncConfig, credentials from .env
│   └── msgraph_client.py  # Azure credential setup
├── entra/
│   ├── aladtec_import.py  # User sync logic, handles matching/create/update
│   ├── group_sync.py  # Group sync strategies and GroupSyncManager
│   ├── groups.py      # EntraGroupManager for M365 group operations
│   └── users.py       # EntraUserManager for Graph API calls
├── exchange/
│   ├── client.py      # PowerShell-based Exchange Online client
│   └── group_sync.py  # Mail-enabled security group sync strategies
└── scripts/           # CLI entry points
```

### Group Sync Strategy Pattern
Group sync uses a strategy pattern. Each `GroupSyncStrategy` subclass defines:
- `name`: Strategy identifier (e.g., "stations", "ff", "ao")
- `get_groups_to_sync(members)`: Returns dict of group_key -> list of members
- `get_group_config(group_key)`: Returns (display_name, mail_nickname, description)
- `automation_notice`: Warning text added to group descriptions

Available strategies: `StationGroupStrategy`, `SupportGroupStrategy`, `FirefighterGroupStrategy`, `WildlandFirefighterGroupStrategy`, `ApparatusOperatorGroupStrategy`, `MarineGroupStrategy`, `VolunteerGroupStrategy`

### Exchange Online (Mail-Enabled Security Groups)
For email distribution without SharePoint sprawl, use mail-enabled security groups instead of M365 groups. These are managed via Exchange Online PowerShell (not Graph API).

**Prerequisites:**
- PowerShell 7+ (`pwsh`)
- ExchangeOnlineManagement module
- Certificate-based app-only authentication

**Environment variables:**
- `EXCHANGE_ORGANIZATION`: Domain (default: sjifire.org)
- `EXCHANGE_CERTIFICATE_THUMBPRINT`: Windows certificate thumbprint
- `EXCHANGE_CERTIFICATE_PATH` + `EXCHANGE_CERTIFICATE_PASSWORD`: Cross-platform .pfx file

The `exchange/` module mirrors the `entra/group_sync.py` strategies but creates mail-enabled security groups via PowerShell subprocess.

## Important Patterns

### 403 Retry Logic
Admin users in Entra ID may return 403 errors when updating phone/email fields. The `update_user` method in `users.py` catches these and retries without the problematic fields (mobilePhone, businessPhones, otherMails).

### Position vs Positions
- `member.position`: Employee Type field (single value, often rank-related)
- `member.positions`: Scheduling positions from member detail page (list)

### Matching Users
Users are matched between systems by:
1. Email address
2. Generated UPN (firstname.lastname@domain)
3. Display name

### Dry Run Mode
All sync operations support `--dry-run` to preview changes without applying them.

## Testing

```bash
uv run pytest                    # Run all tests
uv run pytest -v                 # Verbose
uv run pytest --cov=sjifire      # With coverage
```

Tests use pytest-asyncio for async code. Mocking is done with respx for HTTP calls.

## Common Tasks

### Run user sync manually
```bash
uv run entra-user-sync --dry-run  # Preview
uv run entra-user-sync            # Apply changes
```

### Sync single user
```bash
uv run entra-user-sync --individual user@sjifire.org
```

### Run group sync manually
```bash
uv run entra-group-sync --all --dry-run  # Preview all strategies
uv run entra-group-sync --all            # Apply changes
uv run entra-group-sync --strategy ff    # Sync specific strategy
```

### Run mail group sync (Exchange Online)
```bash
uv run mail-group-sync --all --dry-run  # Preview (requires PowerShell + cert setup)
uv run mail-group-sync --all            # Apply changes
```

### Check linting
```bash
uv run ruff check .
uv run ruff format --check .
```

## Configuration Files

- `config/entra_sync.json`: Company name, domain, skip list
- `config/group_mappings.json`: Position-to-group assignments
- `.env`: Credentials (not committed)

## GitHub Actions

- `ci.yml`: Lint + test on PR/push
- `entra-sync.yml`: Weekday sync at noon Pacific (user sync + group sync), uploads backup artifacts
