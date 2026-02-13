# Connecting to SJI Fire MCP Tools in Claude.ai

## Requirements

- Claude.ai Pro, Team, or Enterprise account
- Your @sjifire.org Microsoft account

## Steps

1. Go to [claude.ai](https://claude.ai) and sign in
2. Click your **profile icon** (bottom-left) → **Settings**
3. Go to **Integrations**
4. Click **"Add more integrations"**
5. Enter this URL:
   ```
   https://mcp.sjifire.org/mcp
   ```
6. Click **Connect** — you'll be redirected to Microsoft login
7. Sign in with your **@sjifire.org** account
8. You should see **"SJI Fire District"** appear as a connected integration

## Getting Started

Start a new chat and select one of the built-in **prompts**:

- **Operations Dashboard** — On-duty crew, recent dispatch calls, and report status
- **Incident Reporting** — Guided workflow for writing NERIS-compliant reports
- **Shift Briefing** — Crew overview for a specific date

Or just ask Claude naturally — it will use the right tools automatically.

## Available Tools

Once connected, Claude can:

- **Dashboard** — Operations overview with on-duty crew, recent calls, and reporting status
- **Dispatch** — Look up recent and open dispatch calls, call details, search by date range
- **Schedule** — Check who's on duty (today or any date)
- **Personnel** — Look up personnel names, emails, and contact info
- **Incident Reporting** — Create, edit, and manage incident reports
- **NERIS** — View and search NERIS incidents, look up valid NERIS codes (incident types, actions, locations, etc.)

### Officer Features

Users in the **MCP Incident Officers** Entra group can also:

- View all incidents (not just their own)
- Submit completed reports to NERIS
- View and look up NERIS federal reporting records

## Browser Dashboard

A full visual dashboard is available at:

```
https://mcp.sjifire.org/dashboard
```

Sign in with your @sjifire.org Microsoft account (same SSO as Outlook/Teams). The dashboard shows on-duty crew, recent dispatch calls, reporting status, and upcoming crew — and auto-refreshes every hour.

## Example Commands

- "Show me the dashboard"
- "Who's on duty today?"
- "Show me the dispatch calls from last week"
- "Pull up dispatch call 26-001234"
- "Start a report for 26-001234"
- "List my incident reports"
- "What NERIS codes are available for incident type?"

## Troubleshooting

If tools aren't working, try removing and re-adding the integration. The connection may time out after periods of inactivity.
