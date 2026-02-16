"""MCP prompts and resources for SJI Fire & Rescue.

Prompts provide pre-built workflows that appear as selectable options
when a user connects to the MCP server in Claude.ai. Resources expose
reference files (like the dashboard prototype) that Claude can read.

Any authenticated user who connects gets these automatically — no
manual project setup required.
"""

from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Path to docs directory — try source tree first, then /app (Docker).
_SRC_DOCS = Path(__file__).resolve().parents[3] / "docs"
_APP_DOCS = Path("/app/docs")
_DOCS_DIR = _SRC_DOCS if _SRC_DOCS.is_dir() else _APP_DOCS


def register_prompts(mcp: FastMCP) -> None:
    """Register all MCP prompts."""

    @mcp.prompt(
        name="operations_dashboard",
        title="Operations Dashboard",
        description=(
            "Show a live operations dashboard. Fetches today's on-duty crew, "
            "recent dispatch calls, and incident report status, then renders "
            "an interactive dashboard as an HTML artifact."
        ),
    )
    def operations_dashboard() -> str:
        return """\
Call `start_session` to get the operations dashboard. It returns pre-rendered HTML \
in `dashboard_html` — create an HTML artifact with that content (copy verbatim).

**Refreshing:** On "refresh"/"update", call `start_session` again and create a new artifact.

**Starting a report:** When the user clicks "Start Report" or mentions a dispatch ID, \
call `get_dispatch_call` for that ID and begin the incident reporting workflow.
"""

    @mcp.prompt(
        name="incident_reporting",
        title="Start Incident Report",
        description=(
            "Walk through creating an incident report for a dispatch call. "
            "Pulls dispatch data, crew schedule, and NERIS codes to build "
            "a complete report ready for officer review."
        ),
    )
    def incident_reporting() -> str:
        return """\
You are helping an SJI Fire & Rescue officer or firefighter complete a NERIS \
incident report. Follow this workflow:

**Step 1 — Identify the call**
- If the user provides a dispatch ID directly (e.g., from the dashboard "Start Report" button), \
skip to Step 2 — fetch that call with `get_dispatch_call` immediately
- Otherwise, ask which dispatch call to report on, or use \
`get_open_dispatch_calls` to find active calls
- Use `search_dispatch_calls` or `list_dispatch_calls` if the user gives a date range
- Fetch full call details with `get_dispatch_call`

**Step 2 — Gather context**
- Use `get_on_duty_crew` for the call date to identify who was on shift
- Use `get_personnel` to look up specific crew members if needed
- Check `list_incidents` to see if a draft already exists for this call

**Step 3 — Create or update the report**
If no draft exists:
- Use `create_incident` with the dispatch ID, date, station, and crew
- Include crew assignments with name, email, rank, position, and unit

If a draft exists:
- Use `update_incident` to fill in missing fields

**Step 4 — Complete required fields**
Walk through each section:
1. **Incident type** — Use `get_neris_values` with value_set="incident_type" for the NERIS code
2. **Address** — From dispatch data
3. **Crew assignments** — Names, positions, units from the schedule
4. **Timestamps** — dispatch, en_route, on_scene, cleared (from dispatch data)
5. **Narratives** — outcome and actions_taken (help the user write these)
6. **Unit responses** — Which apparatus responded

**Step 5 — Review and submit**
- Set status to "ready_review" when all fields are complete
- Only officers can submit to NERIS via `submit_incident`

**Important notes:**
- Incident numbers follow the format "26-NNNNNN" (year prefix + sequence)
- Station codes: S31 (HQ), S32 (Cape San Juan), S33 (Little Mountain), \
S34 (Sunset Point), S35 (Roche Harbor), S36 (Eagle Crest)
- Common apparatus: E31, L31, R31, BN31, OPS31, CH31, T33, E33, E35, FB31
"""

    @mcp.prompt(
        name="shift_briefing",
        title="Shift Briefing",
        description=(
            "Generate a shift briefing summary with today's crew, "
            "recent calls, and any outstanding incident reports."
        ),
    )
    def shift_briefing() -> str:
        return """\
Generate a shift briefing for the incoming crew at SJI Fire & Rescue.

**Gather data** (call in parallel):
- `get_dashboard` — today's crew, recent calls, report status
- `list_incidents` — any incomplete incident reports

**Format the briefing** with these sections:

1. **Today's Crew** — Who's on duty, positions, shift times
2. **Recent Activity** — Last 5-7 dispatch calls with nature, address, outcome
3. **Outstanding Reports** — Any incident reports still in draft or review
4. **Calls Needing Reports** — Dispatch calls that don't have an incident report yet

Keep it concise and scannable. Use bullet points. Highlight anything \
that needs immediate attention (incomplete reports, high-severity calls).
"""


def register_resources(mcp: FastMCP) -> None:
    """Register all MCP resources."""

    @mcp.resource(
        uri="sjifire://project-instructions",
        name="Project Instructions",
        title="Incident Report Assistant Instructions",
        description=(
            "Workflow guide for incident reporting: how to gather data, "
            "classify incidents, build narratives, and submit to NERIS."
        ),
        mime_type="text/markdown",
    )
    def project_instructions() -> str:
        path = _DOCS_DIR / "neris" / "incident-report-instructions.md"
        if path.exists():
            return path.read_text()
        return "# Project instructions not found."

    @mcp.resource(
        uri="sjifire://neris-values",
        name="NERIS Values Reference",
        title="NERIS Value Sets Quick Reference",
        description=(
            "Common NERIS value sets for incident reporting: incident types, "
            "action/tactics, property use, and more. Load this when starting "
            "an incident report."
        ),
        mime_type="text/markdown",
    )
    def neris_values() -> str:
        path = _DOCS_DIR / "neris" / "neris-value-sets-reference.md"
        if path.exists():
            return path.read_text()
        return "# NERIS values reference not found."
