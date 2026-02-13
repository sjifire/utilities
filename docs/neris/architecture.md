# NERIS Incident Reporting — Architecture

## Overview

Claude-powered incident reporting via Claude.ai connected to the SJI Fire MCP server.
Authenticated via Entra ID (group-gated). Stores in Cosmos DB, submits to NERIS API.

## Architecture

Claude.ai is the frontend — no custom SPA needed. The MCP server provides
tools that Claude uses to manage incidents through natural conversation.

```
Claude.ai  ←→  MCP Server (OAuth AS)  ←→  Entra ID (user login)
                    ↕                           ↕
               Cosmos DB                   Graph API
             (incidents,                  (personnel,
              schedules,                   groups)
              dispatch,
              oauth tokens)
```

- **Frontend**: Claude.ai with MCP integration (no custom UI)
- **Backend**: MCP server on Azure Container Apps (scales to zero)
- **Database**: Azure Cosmos DB (NoSQL, serverless) — incidents, schedules, dispatch calls, OAuth tokens
- **Auth**: Entra ID → MCP OAuth proxy → Claude.ai (Dynamic Client Registration)
- **RBAC**: Officer group (`MCP Incident Officers`) gates submit and view-all

## MCP Tools (registered on server)

| Tool | Access | Purpose |
|------|--------|---------|
| `create_incident` | Any user | Create draft incident report |
| `get_incident` | Creator/crew/officer | View full incident document |
| `list_incidents` | Scoped | User sees own; officers see all |
| `update_incident` | Creator/officer | Edit fields on non-submitted incidents |
| `submit_incident` | Officer only | Validate and submit to NERIS |
| `get_personnel` | Any user | Look up personnel via Graph API |
| `get_on_duty_crew` | Any user | On-duty crew from Aladtec (Cosmos-cached, admin hidden by default) |
| `list_dispatch_calls` | Any user | Recent dispatch calls from iSpyFire |
| `get_dispatch_call` | Any user | Single call details (iSpyFire or Cosmos archive) |
| `get_open_dispatch_calls` | Any user | Currently open calls |
| `search_dispatch_calls` | Any user | Search by dispatch ID or date range |
| `list_neris_value_sets` | Any user | Browse available NERIS code sets |
| `get_neris_values` | Any user | Look up specific NERIS codes |

## Cosmos DB Document: IncidentDocument

Partition key: `/year` (four-digit string from incident_date)

```json
{
  "id": "uuid",
  "year": "2026",
  "station": "S31",
  "status": "draft|in_progress|ready_review|submitted",
  "incident_number": "26-000944",
  "incident_date": "2026-02-12",
  "incident_type": "111",
  "address": "100 Spring St",
  "city": "Friday Harbor",
  "state": "WA",
  "latitude": 48.534,
  "longitude": -123.017,
  "crew": [{"name": "...", "email": "...", "rank": "...", "position": "...", "unit": "..."}],
  "unit_responses": [],
  "timestamps": {"dispatch": "...", "on_scene": "..."},
  "narratives": {"outcome": "...", "actions_taken": "..."},
  "created_by": "user@sjifire.org",
  "created_at": "2026-02-12T19:00:00Z",
  "updated_at": null,
  "neris_incident_id": null,
  "internal_notes": ""
}
```

## OAuth Token Store

Tokens stored in Cosmos DB (`oauth-tokens` container) for multi-replica resilience.
Two-layer cache: per-replica TTLCache (L1, 2-min) backed by Cosmos (L2).

- Partition key: `/token_type` (access_token, refresh_token, auth_code)
- Per-document `ttl` field for Cosmos auto-expiration
- User identity embedded in each token document (no separate user map)
- Client registrations and pending auth states remain in-memory (short-lived)

## Cosmos DB Containers

| Container | Partition Key | TTL | Purpose |
|-----------|--------------|-----|---------|
| `incidents` | `/year` | — | Incident documents |
| `schedules` | `/date` | — | On-duty crew cache |
| `dispatch-calls` | `/year` | — | Archived dispatch calls |
| `oauth-tokens` | `/token_type` | Per-doc | OAuth tokens (access, refresh, auth codes) |

## Conversation Flow

Claude guides users through incident reporting via natural conversation:

1. **Auto-populate** — pull dispatch data (incident number, address, timestamps, units)
2. **Classify** — suggest NERIS incident type from dispatch notes, user confirms
3. **Fill crew** — pull on-duty crew from Aladtec schedule, user adjusts
4. **Tactical details** — timestamps, response modes, risk reduction
5. **Narratives** — Claude drafts outcome and actions taken, user reviews
6. **Conditional modules** — fire/medical/hazsit specifics
7. **Validate and submit** — officer reviews, submits to NERIS API

## NERIS API

- **Production**: https://api.neris.fsri.org/v1
- **Sandbox**: https://api-test.neris.fsri.org/v1
- **Auth**: OAuth2 client_credentials grant (client_id + client_secret)
- **Python client**: `neris-api-client>=1.5.1`
- **Value sets**: Discoverable via `list_neris_value_sets` / `get_neris_values` tools

## Security

- Entra ID: group-gated access, JWT validation with JWKS
- OAuth tokens: Cosmos DB-backed, auto-expiring (1h access, 24h refresh, 5m auth codes)
- Anthropic API: no training on API data, 30-day safety retention
- Cosmos DB: encrypted at rest
- Claude API key stays server-side only

## Open Questions

- NERIS entity ID for SJI Fire District (need to register)
- NERIS API credentials (need client_id/secret via vendor enrollment)
- Multi-user on same incident: IC starts, officer finishes
- Dispatch source: mix of PDF from CAD, photos, and email
