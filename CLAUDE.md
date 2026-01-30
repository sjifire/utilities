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
│   ├── aladtec_import.py  # Main sync logic, handles matching/create/update
│   └── users.py       # EntraUserManager for Graph API calls
└── scripts/           # CLI entry points
```

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

### Run sync manually
```bash
uv run aladtec-import --dry-run  # Preview
uv run aladtec-import            # Apply changes
```

### Sync single user
```bash
uv run aladtec-import --individual user@sjifire.org
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
- `entra-sync.yml`: Daily sync at 5 AM Pacific, uploads backup artifacts
