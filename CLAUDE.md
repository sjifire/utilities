# Claude Code Context

This file provides context for Claude Code and other AI assistants working on this project.

## Project Overview

SJI Fire District utilities for syncing personnel data between Aladtec (scheduling/workforce management), Microsoft Entra ID (identity management), and iSpyFire (incident response/paging).

## Tech Stack

- **Python 3.14** with type hints
- **uv** for package management
- **msgraph-sdk** for Microsoft Graph API
- **httpx** + **beautifulsoup4** for Aladtec web scraping
- **httpx** + **tenacity** for iSpyFire API (with rate limit retry)
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
- `extensionAttribute4`: Schedules (comma-delimited schedule visibility from Aladtec)

### iSpyFire
- Incident response and paging system
- REST API with session-based authentication
- Users have `isActive` and `isLoginActive` flags (both must match)
- `isUtility` flag marks service/apparatus accounts (skip from auto-removal)
- Device logout requires two steps: logout push notifications, then remove devices

### Calendar Sync
Two types of calendar sync from Aladtec schedules to Outlook:

**Duty Calendar Sync** (`duty-calendar-sync`):
- Syncs "On Duty" events to a shared mailbox/group calendar
- Creates all-day events for each filled position per day
- Overwrites all events in the target date range
- `--save-schedule PATH` caches fetched schedule to JSON for reuse

**Personal Calendar Sync** (`personal-calendar-sync`):
- Syncs individual's Aladtec shifts to their **primary** Outlook calendar
- Events tagged with orange "Aladtec" category (auto-created in each user's Outlook)
- Creates events matching shift start/end times (supports partial shifts like 19:00-20:00)
- Skips entries with empty position (e.g., Trades)
- Compares events by key: `{date}|{subject}|{start_time}|{end_time}`
- `--force` updates all events, `--purge` deletes all Aladtec events
- `--load-schedule PATH` uses cached schedule instead of fetching from Aladtec

**Schedule Caching** (reduces Aladtec API calls):
```bash
# Duty sync saves schedule, personal sync reuses it
uv run duty-calendar-sync --mailbox group@sjifire.org --months 4 --save-schedule /tmp/schedule.json
uv run personal-calendar-sync --all --months 4 --load-schedule /tmp/schedule.json
```

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
│   ├── client.py          # HTTP client with login/session management
│   ├── member_scraper.py  # Web scraper for member CSV export
│   ├── models.py          # Member dataclass with rank/display_rank properties
│   └── schedule_scraper.py # Schedule scraper for calendar data
├── core/
│   ├── backup.py          # JSON backup before sync operations
│   ├── config.py          # EntraSyncConfig, credentials from .env
│   ├── constants.py       # Shared constants (OPERATIONAL_POSITIONS, RANK_HIERARCHY)
│   ├── group_strategies.py # GroupStrategy classes for group membership rules
│   ├── msgraph_client.py  # Azure credential setup
│   └── normalize.py       # Name normalization utilities
├── entra/
│   ├── aladtec_import.py  # User sync logic, handles matching/create/update
│   ├── groups.py          # EntraGroupManager for M365 group operations
│   └── users.py           # EntraUserManager for Graph API calls
├── exchange/
│   └── client.py          # PowerShell-based Exchange Online client
├── ispyfire/
│   ├── client.py          # API client with tenacity retry for rate limiting
│   ├── models.py          # ISpyFirePerson dataclass
│   └── sync.py            # Sync logic, comparison, filtering
├── calendar/
│   ├── models.py          # OnDutyEvent, SyncResult dataclasses
│   ├── duty_sync.py       # DutyCalendarSync for shared mailbox (On Duty events)
│   └── personal_sync.py   # PersonalCalendarSync for user calendars
├── mcp/                   # Remote MCP server for Claude.ai
│   ├── server.py          # FastMCP app, auth config, tool registration
│   ├── auth.py            # Entra JWT validation, EasyAuth header parsing, UserContext, RBAC
│   ├── oauth_provider.py  # OAuth AS proxy: Claude.ai ↔ Entra ID
│   ├── token_store.py     # Two-layer OAuth token store (TTLCache + Cosmos DB)
│   ├── dashboard.py       # Operations dashboard (client-side rendered) + session bootstrap
│   ├── prompts.py         # MCP prompts and resources (project instructions, NERIS values)
│   ├── dispatch/          # iSpyFire dispatch call lookup + archival
│   │   ├── models.py      # DispatchCallDocument (Pydantic)
│   │   ├── store.py       # Cosmos DB CRUD with in-memory fallback
│   │   └── tools.py       # MCP tools for dispatch calls
│   ├── incidents/         # Incident reporting (Cosmos DB + NERIS)
│   │   ├── models.py      # IncidentDocument, CrewAssignment (Pydantic)
│   │   ├── store.py       # Cosmos DB CRUD with in-memory fallback
│   │   └── tools.py       # MCP tools with role-based access control
│   ├── neris/tools.py     # NERIS value set lookup tools
│   ├── personnel/tools.py # Graph API personnel lookup
│   └── schedule/          # On-duty crew lookup with Cosmos cache
│       ├── models.py      # DayScheduleCache (Pydantic)
│       ├── store.py       # Cosmos DB cache with in-memory fallback
│       └── tools.py       # MCP tool with auto-refresh from Aladtec
└── scripts/               # CLI entry points
```

### MCP Server (Remote, for Claude.ai)

Remote MCP server at `https://mcp.sjifire.org/mcp` providing fire district tools to Claude.ai users. Deployed on Azure Container Apps.

**Auth flow**: Claude.ai → MCP Server (OAuth AS) → Entra ID. The server implements `OAuthAuthorizationServerProvider` from the MCP SDK to bridge Claude.ai's Dynamic Client Registration with Entra ID. See `oauth_provider.py`.

**Access control**:
- Any `@sjifire.org` Entra user can connect (sign-in audience: `AzureADMyOrg`)
- Officer group (`MCP Incident Officers`) gates: submit incidents, view all incidents
- All other tools (dispatch, schedule, personnel) are open to any authenticated user

**MCP tools registered** (18 tools):
- `start_session` (text summary + browser dashboard URL + session bootstrap)
- `refresh_dashboard` (refreshes data, returns updated summary + new URL)
- `get_dashboard` (raw data: on-duty crew, recent calls, report status)
- `create_incident`, `get_incident`, `list_incidents`, `update_incident`, `submit_incident`
- `list_neris_incidents`, `get_neris_incident` (NERIS federal reporting records)
- `get_personnel`
- `get_on_duty_crew` (hides admin by default; `include_admin=True` to show all)
- `list_dispatch_calls`, `get_dispatch_call`, `get_open_dispatch_calls`, `search_dispatch_calls`
- `list_neris_value_sets`, `get_neris_values`

**Dashboard**: `start_session` returns a markdown summary (fast text display) plus a link to `/dashboard` for the full visual dashboard. `refresh_dashboard` returns fresh data without the instructions payload. The browser dashboard at `/dashboard` is authenticated via Azure Container Apps EasyAuth (Entra ID SSO) and auto-refreshes every hour. The visual dashboard is rendered client-side from `docs/dashboard-template.html` with injected JSON data.

**MCP prompts**: `operations_dashboard`, `incident_reporting`, `shift_briefing` — selectable workflows in Claude.ai.

**MCP resources**: `sjifire://project-instructions` (from `docs/neris/incident-report-instructions.md`), `sjifire://neris-values` (from `docs/neris/neris-value-sets-reference.md`, auto-generated via `uv run generate-neris-reference`).

**Session instructions**: `docs/mcp-start-session.md` — loaded by `start_session` tool, tells Claude how to present the dashboard and what actions to offer.

**Infrastructure**: Container Apps (Consumption plan), Cosmos DB (Serverless NoSQL), ACR, Key Vault references for secrets. Custom domain with managed TLS.

**Deployment**:
- Dev: `./scripts/deploy-mcp.sh` (builds via ACR, deploys, health check with version verification)
- Prod: `.github/workflows/mcp-deploy.yml` (on push to main, paths: `src/sjifire/mcp/**`, `Dockerfile`, etc.)

**Key env vars** (set on Container App, secrets via Key Vault references):
- `ENTRA_MCP_API_CLIENT_ID`, `ENTRA_MCP_API_CLIENT_SECRET`, `ENTRA_MCP_OFFICER_GROUP_ID`
- `COSMOS_ENDPOINT`, `MS_GRAPH_*`, `ALADTEC_*`, `ISPYFIRE_*`, `MCP_SERVER_URL`

### Group Sync Strategy Pattern
Group sync uses a strategy pattern with a `GroupMember` protocol that works with both Aladtec `Member` and `EntraUser` objects. The sync pulls membership data directly from Entra ID (which is synced from Aladtec via user sync).

Each `GroupStrategy` subclass defines:
- `name`: Strategy identifier (e.g., "stations", "ff", "ao")
- `get_members(members)`: Returns dict of group_key -> list of members
- `get_config(group_key)`: Returns GroupConfig (display_name, mail_nickname, description)
- `automation_notice`: Warning text added to group descriptions

Available strategies: `StationStrategy`, `SupportStrategy`, `FirefighterStrategy`, `WildlandFirefighterStrategy`, `ApparatusOperatorStrategy`, `MarineStrategy`, `VolunteerStrategy`, `MobeScheduleStrategy`

**Data flow:** Entra ID users → Strategy determines membership → Sync to M365/Exchange groups

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

**Retry logic:** Member add/remove operations use tenacity to automatically retry transient Azure AD sync errors (up to 3 attempts with exponential backoff). Groups are backed up before any sync operation.

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
uv run ms-group-sync --all --dry-run  # Preview all strategies
uv run ms-group-sync --all            # Apply changes
uv run ms-group-sync --strategy ff    # Sync specific strategy
uv run ms-group-sync --all --new-type m365  # Create new groups as M365 (default: exchange)
```

### Run iSpyFire sync manually
```bash
uv run ispyfire-sync --dry-run           # Preview
uv run ispyfire-sync                     # Apply changes
uv run ispyfire-sync --email user@sjifire.org  # Single user
```

### iSpyFire admin operations
```bash
uv run ispyfire-admin list               # List users
uv run ispyfire-admin activate user@sjifire.org
uv run ispyfire-admin deactivate user@sjifire.org
```

### Duty calendar sync (On Duty events)
```bash
uv run duty-calendar-sync --mailbox all-personnel@sjifire.org --month "Feb 2026" --dry-run
uv run duty-calendar-sync --mailbox all-personnel@sjifire.org --month "Feb 2026"
```

### Personal calendar sync (individual schedules)
```bash
uv run personal-calendar-sync --user user@sjifire.org --month "Feb 2026" --dry-run
uv run personal-calendar-sync --all --months 4              # Sync all users for 4 months
uv run personal-calendar-sync --user user@sjifire.org --purge  # Delete all Aladtec events
```

### Group sync details
The `ms-group-sync` command uses Entra ID as the source of truth for membership data:
- **Data source**: Entra ID users (synced from Aladtec via `entra-user-sync`)
- **M365 groups**: Synced via Graph API (uses user IDs directly)
- **Exchange groups**: Synced via PowerShell (uses email addresses)
- **Conflicts**: Groups in both systems are skipped with a warning
- **New groups**: Created as Exchange mail-enabled security groups by default (no SharePoint sprawl)

Note: Run `entra-user-sync` before `ms-group-sync` to ensure Entra ID has current data.

### Check linting
```bash
uv run ruff check .
uv run ruff format --check .
```

## Configuration Files

- `config/organization.json`: Company name, domain, service email, timezone, skip list
- `config/group_mappings.json`: Position-to-group assignments
- `.env`: Credentials (not committed) - use `./scripts/pull-secrets.sh` to populate

## Azure Key Vault

All secrets are centralized in Azure Key Vault `gh-website-utilities`. GitHub Actions use OIDC to authenticate and fetch secrets at runtime.

### Pull secrets locally
```bash
./scripts/pull-secrets.sh           # Pull all secrets to .env
./scripts/pull-secrets.sh --list    # List available secrets
```

### Key Vault secrets used by this repo
- `ALADTEC-URL`, `ALADTEC-USERNAME`, `ALADTEC-PASSWORD`
- `MS-GRAPH-TENANT-ID`, `MS-GRAPH-CLIENT-ID`, `MS-GRAPH-CLIENT-SECRET`
- `ISPYFIRE-URL`, `ISPYFIRE-USERNAME`, `ISPYFIRE-PASSWORD`
- `ENTRA-MCP-API-CLIENT-ID`, `ENTRA-MCP-API-CLIENT-SECRET`, `ENTRA-MCP-OFFICER-GROUP-ID`
- `COSMOS-ENDPOINT`, `COSMOS-KEY`, `ACR-LOGIN-SERVER`, `ACR-USERNAME`, `ACR-PASSWORD`

### OIDC app registration
- App: `utilities-sync` (client ID in workflow files)
- Federated credential: `repo:sjifire/utilities:environment:production`

## GitHub Actions

- `ci.yml`: Lint + test on PR/push
- `entra-sync.yml`: Weekday sync at noon Pacific (user sync + group sync), uploads backup artifacts
- `ispyfire-sync.yml`: Sync every 30 minutes (Entra to iSpyFire), uploads backup artifacts
- `calendar-sync.yml`: Syncs duty + personal calendars (3x daily current month, 1x daily future months)
- `mcp-deploy.yml`: Deploy MCP server on push to main (paths: `src/sjifire/mcp/**`, `Dockerfile`, `pyproject.toml`)

All workflows authenticate via OIDC and fetch secrets from Key Vault (no GitHub secrets required).
