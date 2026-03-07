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
- **azure-storage-blob** for Azure Blob Storage (incident attachments)
- **pytest** + **pytest-asyncio** for testing
- **pytest-playwright** for browser e2e tests
- **polyfactory** for Pydantic model test data generation
- **ruff** for linting/formatting
- **ty** for type checking

## Stateless Containers — CRITICAL Architecture Rule

The ops server runs on Azure Container Apps with **0-many replicas** that restart at any time (deploys, scaling, platform maintenance). Every replica must function identically from a cold start.

**In-memory module-level state is ephemeral.** It will be lost on restart and is NOT shared across replicas.

| OK | NOT OK |
|---|---|
| Short-lived TTL caches (seconds) that reduce redundant API calls. Rebuilt automatically on the next request. | Tracking state transitions between requests (e.g., "call was open, now it's gone → archive it"). |
| Locks to prevent concurrent fetches within one process. | Accumulating data over time in dicts/lists that grow across requests. |
| Static config loaded once at startup. | Any data that must survive a restart or be visible to other replicas. |

**If it needs to survive a restart, it goes to Cosmos DB.** No exceptions. The `dispatch-sync` background task already stores completed calls, schedules are cached in Cosmos, and incidents are in Cosmos. Query those stores instead of trying to reconstruct state in memory.

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
All extension attribute slot assignments are defined in `src/sjifire/core/extension_attrs.py`.

**Entra ID extensionAttributes** (Graph API, written by entra-user-sync):
- `extensionAttribute1`: Rank (Captain, Lieutenant, Chief, etc.)
- `extensionAttribute2`: EVIP expiration date
- `extensionAttribute3`: Positions (comma-delimited scheduling positions)
- `extensionAttribute4`: Schedules (comma-delimited schedule visibility from Aladtec)

**Exchange CustomAttributes** (PowerShell Set-Mailbox, written by signature-sync):
- `CustomAttribute1-5`: RESERVED (synced from Entra, do not overwrite)
- `CustomAttribute6`: Signature title HTML (with `<br>` suffix, or empty)
- `CustomAttribute7`: Signature phone line
- `CustomAttribute8`: Signature title plain text

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
├── ops/                   # Operations server (dashboard, reports, MCP tools)
│   ├── server.py          # FastMCP app, auth config, tool registration
│   ├── auth.py            # Entra JWT validation, EasyAuth header parsing, UserContext, RBAC
│   ├── oauth_provider.py  # OAuth AS proxy: Claude.ai ↔ Entra ID
│   ├── token_store.py     # Two-layer OAuth token store (TTLCache + Cosmos DB)
│   ├── dashboard.py       # Operations dashboard (client-side rendered) + session bootstrap
│   ├── prompts.py         # MCP prompts and resources (project instructions, NERIS values)
│   ├── chat/
│   │   ├── budget.py      # Per-user daily chat budget (Cosmos DB)
│   │   ├── centrifugo.py  # WebSocket proxy, auth callbacks, publish helper
│   │   ├── engine.py      # Claude chat engine (publishes events to Centrifugo)
│   │   ├── models.py      # ConversationMessage, ConversationDocument (Pydantic)
│   │   ├── routes.py      # HTTP route handlers for chat UI
│   │   ├── store.py       # Conversation persistence (Cosmos DB)
│   │   ├── turn_lock.py   # Distributed turn lock (Cosmos DB conditional writes)
│   │   └── tools.py       # Chat tool schemas and execution
│   ├── dispatch/          # iSpyFire dispatch call lookup + archival
│   │   ├── models.py      # DispatchCallDocument (Pydantic)
│   │   ├── store.py       # Cosmos DB CRUD with in-memory fallback
│   │   └── tools.py       # MCP tools for dispatch calls
│   ├── attachments/       # Incident report file attachments (Azure Blob Storage)
│   │   ├── models.py      # AttachmentMeta (Pydantic), blob path builder
│   │   ├── store.py       # Azure Blob Storage client with in-memory fallback
│   │   ├── tools.py       # MCP tools: upload, list, get, delete
│   │   └── routes.py      # HTTP routes for browser upload/download
│   ├── events/            # Training/event records (Cosmos DB + Outlook calendar)
│   │   ├── models.py      # EventRecord, EventAttachmentMeta (Pydantic)
│   │   ├── store.py       # CosmosStore subclass with in-memory fallback
│   │   ├── routes.py      # HTTP routes: CRUD, file upload/download, attendance parsing
│   │   ├── calendar.py    # Outlook calendar integration (create/sync events)
│   │   └── parser.py      # Claude vision attendance sheet parser + roster matching
│   ├── incidents/         # Incident reporting (Cosmos DB + NERIS)
│   │   ├── models.py      # IncidentDocument, CrewAssignment (Pydantic)
│   │   ├── neris_models.py # Pydantic input models for NERIS API records
│   │   ├── neris.py       # NERIS import, diff, patch, and field mapping
│   │   ├── store.py       # Cosmos DB CRUD with in-memory fallback
│   │   └── tools.py       # MCP tools with role-based access control
│   ├── neris/tools.py     # NERIS value set lookup tools
│   ├── personnel/tools.py # Graph API personnel lookup
│   ├── schedule/          # On-duty crew lookup with Cosmos cache
│   │   ├── models.py      # DayScheduleCache (Pydantic)
│   │   ├── store.py       # Cosmos DB cache with in-memory fallback
│   │   └── tools.py       # MCP tool with Aladtec fallback refresh
│   └── tasks/             # Background tasks (Container Apps Job, every 30 min)
│       ├── registry.py    # TaskResult, @register(auto=True/False), run_task, run_all
│       ├── dispatch_sync.py # Dispatch call sync + enrichment (3 tasks, 1 manual)
│       ├── event_archive.py # Archive calendar events before Outlook 180-day expiry
│       ├── ispyfire_sync.py # iSpyFire user sync from Entra
│       ├── neris_sync.py  # NERIS report sync (incremental via checkpoint)
│       ├── schedule_refresh.py # Crew cache refresh from Outlook calendar
│       └── runner.py      # CLI: uv run ops-tasks (-h for help)
└── scripts/               # CLI entry points
```

### Ops Server (Remote, for Claude.ai)

Operations platform at `https://ops.sjifire.org` providing fire district tools, dashboard, and incident reporting. Also serves MCP tools at `/mcp` for Claude.ai. Deployed on Azure Container Apps.

**Auth flow**: Claude.ai → Ops Server (OAuth AS) → Entra ID. The server implements `OAuthAuthorizationServerProvider` from the MCP SDK to bridge Claude.ai's Dynamic Client Registration with Entra ID. See `oauth_provider.py`.

**Access control**:
- Any `@sjifire.org` Entra user can connect (sign-in audience: `AzureADMyOrg`)
- Editor group (`Incident Report Editors`) gates: submit incidents, view all incidents. Membership is checked live via Graph API on every request (no cache — works across multiple container replicas)
- All other tools (dispatch, schedule, personnel) are open to any authenticated user

**MCP tools registered** (27 tools):
- `start_session` (text summary + browser dashboard URL + session bootstrap)
- `refresh_dashboard` (refreshes data, returns updated summary + new URL)
- `get_dashboard` (raw data: on-duty crew, recent calls, report status)
- `create_incident`, `get_incident`, `list_incidents`, `update_incident`, `reset_incident`, `reopen_incident`, `import_from_neris`, `finalize_incident`
- `submit_to_neris`, `update_neris_incident` (push local data to NERIS, diff and patch)
- `upload_attachment`, `list_attachments`, `get_attachment`, `delete_attachment`
- `list_neris_incidents`, `get_neris_incident` (NERIS federal reporting records)
- `get_personnel`
- `get_on_duty_crew` (hides admin by default; `include_admin=True` to show all)
- `list_dispatch_calls`, `get_dispatch_call`, `get_open_dispatch_calls`, `search_dispatch_calls`
- `list_neris_value_sets`, `get_neris_values`

**Dashboard**: `start_session` returns a markdown summary (fast text display) plus a link to `/dashboard` for the full visual dashboard. `refresh_dashboard` returns fresh data without the instructions payload. The browser dashboard at `/dashboard` is authenticated via Azure Container Apps EasyAuth (Entra ID SSO) and auto-refreshes every hour. The visual dashboard is rendered client-side from `src/sjifire/ops/templates/dashboard.html` via Alpine.js with data fetched from `/dashboard/data`.

**MCP prompts**: `operations_dashboard`, `incident_reporting`, `shift_briefing` — selectable workflows in Claude.ai.

**MCP resources**: `sjifire://project-instructions` (from `docs/neris/incident-report-instructions.md`), `sjifire://neris-values` (from `docs/neris/neris-value-sets-reference.md`, auto-generated via `uv run generate-neris-reference`).

**Session instructions**: `docs/mcp-start-session.md` — loaded by `start_session` tool, tells Claude how to present the dashboard and what actions to offer.

**Infrastructure**: Container Apps (Consumption plan, two containers per replica), Cosmos DB (Serverless NoSQL), Azure Blob Storage (incident attachments), ACR, Key Vault references for secrets. Custom domain with managed TLS. Blob storage provisioned via `./scripts/setup-azure-ops.sh --phase 10`. Deployment defined in `containerapp.yaml`.

**Centrifugo sidecar**: Real-time chat messaging via Centrifugo (Go-based) running as an ACA sidecar container alongside the FastAPI app. Client-side uses `centrifuge-js` over WebSocket.
- Channel naming: `chat:incident:{id}` (editor role required), `chat:general:{email}` (matching user)
- Architecture: Browser → ACA ingress (port 8000) → FastAPI WS proxy `/connection/websocket` → Centrifugo (localhost:8001). FastAPI publishes events via Centrifugo internal API (localhost:9001).
- Auth: Centrifugo proxy mode — calls back to FastAPI `/centrifugo/connect` and `/centrifugo/subscribe` to validate EasyAuth cookies. Connect proxy returns `conn_info` (name, email) for presence.
- Presence: Enabled on `chat` namespace (`presence: true`, `join_leave: true`, `force_push_join_leave: true`). Clients query presence on subscribe and receive join/leave events for real-time user awareness.
- Recovery: Centrifugo history buffer (100 msgs, 5min TTL) with `force_recovery` — clients auto-recover missed messages on reconnect. On re-subscribe (e.g., after container update), clients immediately poll Cosmos DB for missed messages (2s safety timer as fallback).
- Session affinity: `stickySessions.affinity: sticky` ensures a browser session always routes to the same replica. Required because each sidecar Centrifugo instance is isolated — events published on one replica are invisible to clients on another.
- Config: Pure env vars (`CENTRIFUGO_*`) on sidecar container, no config file. API key in Key Vault.

**Multi-replica considerations**: Each ACA replica runs its own Centrifugo sidecar. Without a shared broker, Centrifugo instances are isolated — presence and events are per-replica. Session affinity solves this for single-user workflows (POST and WebSocket always hit the same replica). For true multi-user cross-replica broadcasting, a shared broker (Redis or NATS) would be needed. Current state:
- **Turn lock**: Works across replicas (uses Cosmos DB, not Centrifugo).
- **Presence**: Per-replica only. Users on different replicas don't see each other. Acceptable for current scale.
- **Event broadcasting**: Per-replica. Multi-user editing works when users are on the same replica (session affinity helps, but not guaranteed across different browsers).
- **When to add Redis**: If multi-user editing becomes a core workflow and users on different replicas need to see each other's events in real-time, add Redis as Centrifugo's broker (`CENTRIFUGO_BROKER=redis`, `CENTRIFUGO_REDIS_ADDRESS`). Redis can run as an Azure Cache for Redis instance (Basic C0 is ~$13/month). This gives cross-replica pub/sub, shared presence, and shared history recovery. NATS is a lighter alternative (pub/sub only, no shared history/presence) but still requires a shared instance.

**Multi-user shared editing**: Multiple editors can view and contribute to the same incident report simultaneously.
- **Presence awareness**: Centrifugo presence shows who else is viewing the same report (avatar bar in header). Join/leave events update in real-time.
- **Distributed turn lock**: Prevents concurrent Claude API calls for the same incident. Uses Cosmos DB conditional writes (`conversations` container, `id="turn-lock"` with 120s TTL auto-expiry). Works across multiple replicas. See `turn_lock.py`.
- **Turn flow**: User sends message → route acquires lock → if locked by another user, returns 409 with holder info → client shows banner and auto-retries after `done` event → engine releases lock in `finally` block (all exit paths covered: turn limit, context fetch error, budget check, streaming errors).
- **Event types for multi-user**: `turn_start` (who started), `user_message` (broadcast user messages to other subscribers), `done`/`error` include `user_email`/`user_name` for attribution.
- **Client behavior**: Messages blocked by 409 are queued and auto-retried when the active turn completes. Other users see the conversation in real-time (all events broadcast to all subscribers).

**Background tasks**: Container Apps Job (`sjifire-ops-tasks`) runs `uv run ops-tasks` every 30 minutes. Runs all `auto=True` tasks: dispatch-sync, dispatch-enrich, event-archive, ispyfire-sync, neris-sync, schedule-refresh. Tasks registered with `auto=False` (e.g., dispatch-reenrich) only run when explicitly requested by name. New tasks are added via `@register("name")` in `ops/tasks/`. The neris-sync task uses a high-water mark checkpoint for incremental fetches and auto-transitions local submitted incidents to approved when NERIS approves them.

**Cosmos DB backup**: Continuous 30-day PITR (any-second point-in-time restore). For ad-hoc JSON exports beyond 30 days, use `uv run backup-cosmos`. Infrastructure provisioned via `./scripts/setup-azure-ops.sh --phase 2`.

**Deployment**:
- Dev: `./scripts/deploy-ops.sh` (builds via ACR, deploys, configures EasyAuth, health check, ACR purge)
- Prod: `.github/workflows/ops-deploy.yml` (on push to main — calls `deploy-ops.sh` with `TAG=${{ github.sha }}`)

**Key env vars** (set on Container App, secrets via Key Vault references):
- `ENTRA_MCP_API_CLIENT_ID`, `ENTRA_MCP_API_CLIENT_SECRET`, `ENTRA_REPORT_EDITORS_GROUP_ID`
- `COSMOS_ENDPOINT`, `MS_GRAPH_*`, `ALADTEC_*`, `ISPYFIRE_*`, `MCP_SERVER_URL`
- `AZURE_STORAGE_ACCOUNT_URL`, `AZURE_STORAGE_ACCOUNT_KEY` (Blob Storage for incident attachments)
- `CENTRIFUGO_API_KEY` (shared secret for FastAPI → Centrifugo internal API)

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
uv run pytest                    # Run all unit tests (e2e excluded by default)
uv run pytest -v                 # Verbose
uv run pytest --cov=sjifire      # With coverage
uv run pytest -m e2e             # Run e2e browser tests only
uv run pytest -m ""              # Run everything (unit + e2e)
```

Tests use pytest-asyncio for async code. Mocking is done with respx for HTTP calls.

### E2E Tests (Playwright)

Browser-based tests in `tests/e2e/` exercise the dashboard via a real Chromium instance. They start the ops server as a subprocess in dev mode (no auth, in-memory stores) and seed fixture data via `POST /test/seed`.

```bash
# First-time setup
uv run playwright install chromium

# Run e2e tests
uv run pytest -m e2e --browser chromium -v
```

E2E tests are excluded from default `pytest` runs via `addopts = "-m 'not e2e'"`. The CI workflow runs them in a parallel `e2e` job.

Key files:
- `tests/e2e/conftest.py` — Server subprocess, browser fixtures, seed fixture
- `tests/e2e/seed_data.py` — Dispatch calls + schedule data for seeding
- `tests/e2e/test_dashboard_smoke.py` — Page load, Alpine.js init, tab navigation
- `tests/e2e/test_dashboard_data.py` — Stat cards, calls table, crew grid

### Polyfactory Model Factories

`tests/factories.py` provides `ModelFactory` subclasses for generating test data:

```python
from tests.factories import DispatchCallDocumentFactory, IncidentDocumentFactory

doc = DispatchCallDocumentFactory.build(nature="Structure Fire", type="FIRE")
docs = IncidentDocumentFactory.batch(5, status="draft")
```

Available factories: `DispatchCallDocumentFactory`, `IncidentDocumentFactory`, `DayScheduleCacheFactory`, `UnitAssignmentFactory`, `PersonnelAssignmentFactory`, and sub-model factories for `DispatchAnalysis`, `UnitTiming`, `CrewOnDuty`, `ScheduleEntryCache`, `DispatchNote`, `EditEntry`.

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

### Run background tasks (NERIS cache, etc.)
```bash
uv run ops-tasks              # Run all scheduled (auto) tasks
uv run ops-tasks neris-sync   # Run specific task
uv run ops-tasks --list       # List available tasks (manual tasks shown with suffix)
uv run ops-tasks dispatch-reenrich  # Run manual-only task explicitly
```

### Cosmos DB backup (ad-hoc JSON export)
```bash
uv run backup-cosmos                    # Both collections
uv run backup-cosmos --incidents-only
uv run backup-cosmos --dispatch-only
uv run backup-cosmos --output /path/
```

### Email signature sync
```bash
uv run signature-sync --dry-run                    # Preview changes
uv run signature-sync                              # Sync all employees + footer rule
uv run signature-sync --email user@sjifire.org --preview  # Preview one user's signature
uv run signature-sync --remove                     # Remove all signatures + footer rule
```

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
- `ENTRA-MCP-API-CLIENT-ID`, `ENTRA-MCP-API-CLIENT-SECRET`, `ENTRA-REPORT-EDITORS-GROUP-ID`
- `COSMOS-ENDPOINT`, `COSMOS-KEY`, `ACR-LOGIN-SERVER`, `ACR-USERNAME`, `ACR-PASSWORD`
- `AZURE-STORAGE-ACCOUNT-URL`, `AZURE-STORAGE-ACCOUNT-KEY` (Blob Storage for incident attachments)

### OIDC app registration
- App: `utilities-sync` (client ID in workflow files)
- Federated credential: `repo:sjifire/utilities:environment:production`

## GitHub Actions

- `ci.yml`: Lint + test + e2e on PR/push (e2e job runs Playwright chromium in parallel)
- `entra-sync.yml`: Weekday sync at noon Pacific (user sync + group sync + signature sync), uploads backup artifacts
- `ispyfire-sync.yml`: Daily iSpyFire state backup (dry-run sync + artifact upload). Actual sync runs every 30 min via Container Apps Job (`ops-tasks`)
- `calendar-sync.yml`: Syncs duty + personal calendars (3x daily current month, 1x daily future months)
- `ops-deploy.yml`: Deploy ops server on push to main (paths: `src/sjifire/ops/**`, `Dockerfile`, `pyproject.toml`)

All workflows authenticate via OIDC and fetch secrets from Key Vault (no GitHub secrets required).
