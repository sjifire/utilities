#!/usr/bin/env python3
"""Tail production logs, optionally filtered by incident or dispatch ID.

Uses Azure Container Apps live log stream for general tailing, and
Azure Log Analytics (KQL) for filtered searches by ID.

Usage:
    uv run ops-logs                              # tail last 20 + follow
    uv run ops-logs -t 100                       # tail last 100 + follow
    uv run ops-logs 26-002210                    # search by dispatch ID
    uv run ops-logs 26-002210 --hours 48         # search further back
    uv run ops-logs 26-002210 --tail 100         # search + show last 100 + follow
"""

import argparse
import json
import subprocess
import sys
import time

RESOURCE_GROUP = "rg-sjifire-mcp"
WORKSPACE_ID = "39281fbe-70d3-4327-a569-19369c652a3c"
CONTAINER_APP = "sjifire-mcp"
CONTAINER_NAME = "sjifire-mcp"


def _run_query(kql: str) -> list[dict]:
    """Execute a KQL query against Log Analytics and return results."""
    result = subprocess.run(
        [
            "az",
            "monitor",
            "log-analytics",
            "query",
            "--workspace",
            WORKSPACE_ID,
            "--analytics-query",
            kql,
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = "\n".join(
            line for line in result.stderr.splitlines() if not line.startswith("WARNING:")
        ).strip()
        if stderr:
            print(f"Error: {stderr}", file=sys.stderr)
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


def _build_query(search_term: str, hours: int, *, tail: int = 0) -> str:
    """Build KQL query to search logs for a term."""
    safe_term = search_term.replace("'", "\\'")
    kql = (
        f"ContainerAppConsoleLogs_CL\n"
        f"| where ContainerAppName_s == '{CONTAINER_APP}'\n"
        f"| where ContainerName_s == '{CONTAINER_NAME}'\n"
        f"| where TimeGenerated > ago({hours}h)\n"
        f"| where Log_s contains '{safe_term}'\n"
        f"| project TimeGenerated, Log_s\n"
        f"| order by TimeGenerated asc"
    )
    if tail:
        # Take last N rows: sort desc, take N, re-sort asc
        kql = (
            f"ContainerAppConsoleLogs_CL\n"
            f"| where ContainerAppName_s == '{CONTAINER_APP}'\n"
            f"| where ContainerName_s == '{CONTAINER_NAME}'\n"
            f"| where TimeGenerated > ago({hours}h)\n"
            f"| where Log_s contains '{safe_term}'\n"
            f"| project TimeGenerated, Log_s\n"
            f"| order by TimeGenerated desc\n"
            f"| take {tail}\n"
            f"| order by TimeGenerated asc"
        )
    return kql


def _format_log(entry: dict) -> str:
    """Format a log entry for display."""
    ts = entry.get("TimeGenerated", "")
    if "." in ts:
        ts = ts[: ts.index(".")] + "Z"
    msg = entry.get("Log_s", "").rstrip()
    return f"{ts}  {msg}"


def _print_results(results: list[dict], search_term: str) -> int:
    """Print formatted log results. Returns count of lines printed."""
    count = 0
    for entry in results:
        if entry.get("TableName") == "PrimaryResult":
            line = _format_log(entry)
            line = line.replace(search_term, f"\033[1;33m{search_term}\033[0m")
            print(line)
            count += 1
    return count


# ---- Live tail (az containerapp logs) ------------------------------------


def _tail_live(tail_lines: int) -> None:
    """Stream live logs from the Container App (tail -f)."""
    cmd = [
        "az",
        "containerapp",
        "logs",
        "show",
        "-n",
        CONTAINER_APP,
        "-g",
        RESOURCE_GROUP,
        "--container",
        CONTAINER_NAME,
        "--type",
        "console",
        "--format",
        "text",
        "--tail",
        str(tail_lines),
        "--follow",
        "true",
    ]

    print(
        f"Tailing \033[1m{CONTAINER_APP}\033[0m (last {tail_lines} lines, Ctrl+C to stop)...",
        file=sys.stderr,
    )

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)


# ---- Search mode (Log Analytics KQL) ------------------------------------


def _search_logs(search_term: str, hours: int, *, tail: int, raw: bool) -> None:
    """Search Log Analytics for logs matching a term.

    Without --tail: dump all matching results and exit.
    With --tail N: show last N matching results, then poll for new ones.
    """
    print(
        f"Searching logs for \033[1m{search_term}\033[0m (last {hours}h)...",
        file=sys.stderr,
    )

    kql = _build_query(search_term, hours, tail=tail)
    results = _run_query(kql)

    if raw:
        print(json.dumps(results, indent=2))
        return

    count = _print_results(results, search_term)

    if not count:
        print(f"No logs found for '{search_term}' in the last {hours} hours.", file=sys.stderr)
        if not tail:
            sys.exit(1)

    if not tail:
        print(f"\n--- {count} log lines ---", file=sys.stderr)
        return

    # Follow mode: poll every 10 seconds for new matching logs
    print(f"\n--- {count} lines shown, following (Ctrl+C to stop) ---", file=sys.stderr)
    seen_timestamps: set[str] = {entry.get("TimeGenerated", "") for entry in results}
    safe_term = search_term.replace("'", "\\'")

    try:
        while True:
            time.sleep(10)
            kql = (
                f"ContainerAppConsoleLogs_CL\n"
                f"| where ContainerAppName_s == '{CONTAINER_APP}'\n"
                f"| where ContainerName_s == '{CONTAINER_NAME}'\n"
                f"| where TimeGenerated > ago(2m)\n"
                f"| where Log_s contains '{safe_term}'\n"
                f"| project TimeGenerated, Log_s\n"
                f"| order by TimeGenerated asc"
            )
            new_results = _run_query(kql)
            for entry in new_results:
                ts = entry.get("TimeGenerated", "")
                if ts and ts not in seen_timestamps:
                    seen_timestamps.add(ts)
                    line = _format_log(entry)
                    line = line.replace(search_term, f"\033[1;33m{search_term}\033[0m")
                    print(line)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)


# ---- CLI -----------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Tail production logs, optionally filtered by incident or dispatch ID",
    )
    parser.add_argument(
        "id",
        nargs="?",
        default=None,
        help="Incident document ID (UUID) or dispatch ID (e.g. 26-002210). "
        "Omit to tail all app logs.",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="How far back to search (default: 24)",
    )
    parser.add_argument(
        "--tail",
        "-t",
        type=int,
        nargs="?",
        const=20,
        default=None,
        help="Show last N lines then follow (default N: 20, max: 300). "
        "Without an ID, this is the default behavior.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Output raw JSON (search mode only)",
    )

    args = parser.parse_args()
    tail = min(args.tail or 20, 300)

    if args.id:
        _search_logs(args.id, args.hours, tail=args.tail or 0, raw=args.raw)
    else:
        _tail_live(tail)
