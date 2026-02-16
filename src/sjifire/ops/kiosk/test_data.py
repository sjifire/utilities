"""Test data generator for kiosk display.

Produces a realistic, progressive scenario that cycles every 3 minutes.
Activate by adding ``?test_mode=true`` to the kiosk URL.

Modeled after the real 589 Old Farm Rd structure fire (26-002210).

Timeline (repeating every 180 seconds):

  T+0     All Clear — brief idle (3s)
  T+3     Incoming call (dispatch ID only — no nature or address)
  T+12    Address + geo arrives: "589 Old Farm Road" (map loads immediately)
  T+18    Nature arrives: "Fire-Structure"
  T+25    First CAD comment + site history
  T+32    SJF3 paged
  T+38    BN31 + E31 + OPS31 enroute
  T+48    BN31 note: all units proceed non-emergent
  T+58    E31 + BN31 in the area
  T+65    BN31 on scene, establishes command
  T+72    OPS31 on scene
  T+80    L31 enroute
  T+90    T33 enroute
  T+100   E31 arrived on scene
  T+110   BN31 note: fire is out, overhaul mode
  T+120   L31 on scene
  T+135   Overhaul complete, command terminated, units returning
  T+170   Call clears
  T+173   Cycle restarts (brief idle, then new call at T+3)
"""

import time
from datetime import UTC, datetime, timedelta

# Cycle length in seconds before the scenario restarts
CYCLE_SECONDS = 180


def reset_test_clock() -> None:
    """No-op — kept for test compatibility. Wall-clock based, no state to reset."""


def get_test_kiosk_data() -> dict:
    """Generate progressive test data for the kiosk display.

    Uses wall-clock time (``time.time() % CYCLE_SECONDS``) so that all
    workers, all replicas, and all browsers see the exact same timeline
    at the same moment.  Completely stateless — no shared memory needed.
    """
    elapsed = time.time() % CYCLE_SECONDS

    calls = _build_calls(elapsed)
    crew = _build_crew()
    sections = _build_sections(crew)
    upcoming_crew = _build_upcoming_crew()
    upcoming_sections = _build_sections(upcoming_crew)

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "calls": calls,
        "crew": crew,
        "sections": sections,
        "platoon": "B Platoon",
        "upcoming_crew": upcoming_crew,
        "upcoming_sections": upcoming_sections,
        "upcoming_platoon": "A Platoon",
    }


# ---------------------------------------------------------------------------
# Scenario data
# ---------------------------------------------------------------------------

# Base timestamp — call was "reported" at the start of the cycle
_CALL_BASE = datetime.now(UTC).replace(second=0, microsecond=0)


def _ts(base: datetime, offset_seconds: int) -> str:
    """ISO timestamp relative to a base time."""
    return (base + timedelta(seconds=offset_seconds)).isoformat()


def _build_calls(t: float) -> list[dict]:
    """Build the list of active calls for elapsed seconds ``t``."""
    calls: list[dict] = []

    # ── T+0 to T+5: All Clear (idle) ──────────────────────────

    # ── Structure Fire (T+3 to T+170) ─────────────────────────
    if 3 <= t < 170:
        c = _structure_fire_call(t)
        calls.append(c)

    return calls


def _structure_fire_call(t: float) -> dict:
    """Structure fire call — modeled after real 26-002210.

    Starts at T+5 with just a dispatch ID (incoming call, nothing else).
    Address+geo, nature, responders fill in over time.
    """
    base = _CALL_BASE
    call: dict = {
        "dispatch_id": "26-001999",
        "long_term_call_id": "26-001999",
        "nature": "",
        "address": "",
        "city": "",
        "state": "",
        "zip_code": "",
        "agency_code": "SJF3",
        "type": "",
        "zone_code": "",
        "time_reported": base.isoformat(),
        "is_completed": False,
        "cad_comments": "",
        "responding_units": "",
        "responder_details": [],
        "geo_location": "",
        "latitude": None,
        "longitude": None,
        "severity": "low",
        "icon": "\U0001f4df",
        "site_history": [],
        "analysis": {
            "incident_commander": "",
            "incident_commander_name": "",
            "alarm_time": "",
            "first_enroute": "",
            "unit_times": [],
            "on_duty_crew": [],
            "summary": "",
            "actions_taken": [],
            "patient_count": 0,
            "escalated": False,
            "outcome": "",
            "short_dsc": "",
            "key_events": [],
        },
    }

    # T+12: Address + geo arrives (map loads immediately)
    if t >= 12:
        call["address"] = "589 Old Farm Road"
        call["city"] = "Friday Harbor"
        call["state"] = "WA"
        call["zip_code"] = "98250"
        call["geo_location"] = "48.46401,-123.03788"
        call["latitude"] = 48.46401
        call["longitude"] = -123.03788

    # T+18: Nature arrives
    if t >= 18:
        call["nature"] = "Fire-Structure"
        call["type"] = "FIRE"
        call["zone_code"] = "SJF"
        call["severity"] = "high"
        call["icon"] = "\U0001f525"

    # T+25: First CAD comment + site history
    if t >= 25:
        call["cad_comments"] = (
            "wood stove has hole in it, flames coming out of it. Nothing else on fire.\n"
            "Hit it with fire extinguisher and it wouldn't go out.\n"
            "RP, husband and pets safely outside."
        )
        call["site_history"] = [
            {
                "dispatch_id": "25-001203",
                "nature": "Chimney Fire",
                "date": (base - timedelta(days=340)).isoformat(),
            },
            {
                "dispatch_id": "24-000812",
                "nature": "Fire Alarm",
                "date": (base - timedelta(days=620)).isoformat(),
            },
        ]

    # ── Responder timeline (modeled after real 26-002210) ──────
    responders: list[dict] = []
    units_list: list[str] = []

    # T+32: SJF3 paged
    if t >= 32:
        responders.append(
            {
                "unit_call_sign": "SJF3",
                "status": "Paged",
                "time_of_status_change": _ts(base, 90),
            }
        )
        units_list.append("SJF3")
        call["analysis"]["alarm_time"] = _ts(base, 90)

    # T+38: BN31 + E31 + OPS31 enroute
    if t >= 38:
        for unit in ("BN31", "E31", "OPS31"):
            responders.append(
                {
                    "unit_call_sign": unit,
                    "status": "Enroute",
                    "time_of_status_change": _ts(base, 210 + (5 if unit == "E31" else 0)),
                }
            )
            if unit not in units_list:
                units_list.append(unit)
        call["analysis"]["first_enroute"] = _ts(base, 210)

    # T+48: BN31 note — all units proceed non-emergent
    if t >= 48:
        call["cad_comments"] = (
            "wood stove has hole in it, flames coming out of it. Nothing else on fire.\n"
            "Hit it with fire extinguisher and it wouldn't go out.\n"
            "RP, husband and pets safely outside.\n"
            "All responding units can proceed non-emergent at this time"
        )

    # T+58: BN31 + E31 in the area
    if t >= 58:
        responders.extend(
            {
                "unit_call_sign": unit,
                "status": "In Area",
                "time_of_status_change": _ts(base, 660),
            }
            for unit in ("BN31", "E31")
        )

    # T+65: BN31 on scene — establishes command
    if t >= 65:
        responders.append(
            {
                "unit_call_sign": "BN31",
                "status": "On Scene",
                "time_of_status_change": _ts(base, 720),
            }
        )
        call["cad_comments"] = (
            "wood stove has hole in it, flames coming out of it. Nothing else on fire.\n"
            "Hit it with fire extinguisher and it wouldn't go out.\n"
            "RP, husband and pets safely outside.\n"
            "All responding units can proceed non-emergent at this time\n"
            "2 story res nothing showing investigating, est Old Farm Rd Command"
        )
        call["analysis"]["incident_commander"] = "BN31"
        call["analysis"]["incident_commander_name"] = "Jordan Pollack"

    # T+72: OPS31 on scene
    if t >= 72:
        responders.append(
            {
                "unit_call_sign": "OPS31",
                "status": "On Scene",
                "time_of_status_change": _ts(base, 750),
            }
        )

    # T+80: L31 enroute
    if t >= 80:
        responders.append(
            {
                "unit_call_sign": "L31",
                "status": "Enroute",
                "time_of_status_change": _ts(base, 840),
            }
        )
        units_list.append("L31")

    # T+90: T33 enroute
    if t >= 90:
        responders.append(
            {
                "unit_call_sign": "T33",
                "status": "Enroute",
                "time_of_status_change": _ts(base, 900),
            }
        )
        units_list.append("T33")

    # T+100: E31 arrived on scene
    if t >= 100:
        responders.append(
            {
                "unit_call_sign": "E31",
                "status": "On Scene",
                "time_of_status_change": _ts(base, 1440),
            }
        )

    # T+110: BN31 note — fire is out, overhaul mode
    if t >= 110:
        responders.append(
            {
                "unit_call_sign": "BN31",
                "status": "Note",
                "time_of_status_change": _ts(base, 1680),
                "radio_log": (
                    "Fire is out, woodstove is in overhaul mode, cancel additional resources"
                ),
            }
        )
        call["cad_comments"] = (
            "wood stove has hole in it, flames coming out of it. Nothing else on fire.\n"
            "Hit it with fire extinguisher and it wouldn't go out.\n"
            "RP, husband and pets safely outside.\n"
            "All responding units can proceed non-emergent at this time\n"
            "2 story res nothing showing investigating, est Old Farm Rd Command\n"
            "Fire is out, woodstove is in overhaul mode, cancel additional resources"
        )
        call["analysis"]["outcome"] = "fire extinguished by homeowner"

    # T+120: L31 on scene
    if t >= 120:
        responders.append(
            {
                "unit_call_sign": "L31",
                "status": "On Scene",
                "time_of_status_change": _ts(base, 1710),
            }
        )

    # T+135: Overhaul complete, command terminated
    if t >= 135:
        call["cad_comments"] = (
            "wood stove has hole in it, flames coming out of it. Nothing else on fire.\n"
            "Hit it with fire extinguisher and it wouldn't go out.\n"
            "RP, husband and pets safely outside.\n"
            "All responding units can proceed non-emergent at this time\n"
            "2 story res nothing showing investigating, est Old Farm Rd Command\n"
            "Fire is out, woodstove is in overhaul mode, cancel additional resources\n"
            "Overhaul complete, property turned over to homeowner, command terminated"
        )
        responders.extend(
            {
                "unit_call_sign": unit,
                "status": "Returning",
                "time_of_status_change": _ts(base, 2160),
            }
            for unit in ("BN31", "E31", "OPS31", "L31", "T33")
        )

    call["responder_details"] = responders
    call["responding_units"] = ", ".join(units_list)

    return call


def _build_crew() -> list[dict]:
    """Build a realistic on-duty crew list."""
    _shift = "07:00-07:00"
    return [
        _crew("Norvin Collins", "Captain", "Chief Officer", 1, _shift),
        _crew("Kyle Dodd", "Lieutenant", "Operations", 2, _shift),
        _crew("Jake Morrison", "Firefighter", "Operations", 3, _shift),
        _crew("Ben Walker", "Firefighter", "Operations", 3, _shift),
        _crew("Chris Jarman", "AO", "Operations", 4, _shift),
        _crew("Tyler Reed", "EMT", "Volunteers", 5, _shift),
    ]


def _build_upcoming_crew() -> list[dict]:
    """Build a realistic upcoming shift crew list."""
    _shift = "07:00-07:00"
    return [
        _crew("Michael Torres", "Captain", "Chief Officer", 1, _shift),
        _crew("Sarah Chen", "Lieutenant", "Operations", 2, _shift),
        _crew("James Hart", "Firefighter", "Operations", 3, _shift),
        _crew("Lisa Park", "Firefighter", "Operations", 3, _shift),
        _crew("Ryan Scott", "AO", "Operations", 4, _shift),
        _crew("Alex Kim", "EMT", "Volunteers", 5, _shift),
    ]


def _crew(name: str, position: str, section: str, sort: int, shift: str) -> dict:
    return {
        "name": name,
        "position": position,
        "section": section,
        "_sort_key": sort,
        "shift": shift,
        "email": "",
        "mobile": "",
    }


def _build_sections(crew: list[dict]) -> list[dict]:
    """Group crew into sections, ordered by sort key."""
    groups: dict[str, list[dict]] = {}
    for c in crew:
        sec = c["section"]
        if sec not in groups:
            groups[sec] = []
        groups[sec].append(c)

    # Sort sections by the minimum _sort_key of their members
    ordered = sorted(groups.items(), key=lambda kv: min(m["_sort_key"] for m in kv[1]))
    return [{"key": k, "label": k, "members": members} for k, members in ordered]
