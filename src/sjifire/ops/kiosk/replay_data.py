"""Fixture-replay data generator for kiosk display.

Replays the fire-alarm fixture (``26-002358``) as a scripted ~2-minute
demo cycle.  Events appear progressively, then the call clears and the
cycle loops.

Activate by adding ``?test_mode=2`` to the kiosk URL.

Timeline (repeating every 120 seconds):

  T+0     Idle — no call (5s pause)
  T+5     Call header: dispatch ID + address + geo (map loads)
  T+8     Nature arrives: "Fire-Alarm"
  T+12    SJF3 paged
  T+16    BN31, E31 enroute
  T+20    M12 enroute
  T+26    E31 arrived on scene
  T+30    BN31, M12 arrived
  T+35    EMS13, E33 enroute; SJEM paged
  T+40    EMS13 arrived; BN31 note
  T+45    L31, EMS11, DIV37 enroute
  T+50    EMS11 cleared; L31 at station
  T+55    E33, L31 arrived; BN31 note (has command)
  T+62    M12 enroute to hospital
  T+70    M12 arrived at hospital
  T+78    BN31 notes (overhaul complete, command terminated)
  T+85    All units RTQ (batch clear)
  T+92    M12 complete
  T+100   Call clears, cycle restarts
"""

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Cycle length in seconds before the scenario restarts
CYCLE_SECONDS = 120

# Module-level caches
_fixture_detail: dict | None = None
_fixture_events: list[dict] | None = None

# Fixture file — co-located so it ships inside the Docker image
_FIXTURE_FILE = Path(__file__).resolve().parent / "fixtures" / "fire-alarm.json"


def get_replay_kiosk_data() -> dict:
    """Generate progressive replay data for the kiosk display.

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
# Fixture loading
# ---------------------------------------------------------------------------


def _load_fixture_detail() -> dict:
    """Load and cache the fire-alarm fixture JSON."""
    global _fixture_detail
    if _fixture_detail is not None:
        return _fixture_detail
    _fixture_detail = json.loads(_FIXTURE_FILE.read_text())
    return _fixture_detail


def _load_fixture_events() -> list[dict]:
    """Load fixture responder events in chronological order."""
    global _fixture_events
    if _fixture_events is not None:
        return _fixture_events
    detail = _load_fixture_detail()
    # JoinedRespondersDetail is newest-first; reverse for chronological
    _fixture_events = list(reversed(detail["JoinedRespondersDetail"]))
    return _fixture_events


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def _cycle_base(t: float) -> datetime:
    """Dynamic base timestamp for the current cycle.

    Returns ``now - (t - 5)`` so that ``time_reported`` is always
    "a few seconds ago" when the call first appears (T+5) and drifts
    naturally as the cycle progresses.  Resets every cycle.
    """
    return datetime.now(UTC) - timedelta(seconds=max(t - 5, 0))


def _ts(base: datetime, offset_seconds: int) -> str:
    """ISO timestamp relative to a base time."""
    return (base + timedelta(seconds=offset_seconds)).isoformat()


# ---------------------------------------------------------------------------
# Scripted event schedule
# ---------------------------------------------------------------------------

# Each entry: (checkpoint_seconds, fixture_chrono_index)
# The fixture's JoinedRespondersDetail (reversed) gives chronological order.
# We place all 41 real events at hand-tuned checkpoint times for good pacing.
_EVENT_SCHEDULE: list[tuple[int, int]] = [
    # T+12: Initial page
    (12, 0),  # SJF3 PAGED
    # T+16: First units enroute
    (16, 2),  # BN31 ENRT
    (16, 3),  # E31 ENRT
    # T+20: Medic enroute
    (20, 4),  # M12 ENRT
    # T+26: First arrival
    (26, 5),  # E31 ARRVD (nothing showing, investigating)
    # T+30: More arrivals
    (30, 6),  # BN31 ARRVD
    (30, 7),  # M12 ARRVD
    # T+35: EMS dispatch + mutual aid
    (35, 1),  # SJEM PAGED (first page)
    (35, 8),  # M12 NOTE (all agency page)
    (35, 9),  # M12 NOTE (all agency page)
    (35, 10),  # SJEM PAGED (second)
    (35, 11),  # EMS13 ENRT
    (35, 12),  # E33 ENRT
    # T+40: Scene assessment
    (40, 13),  # BN31 NOTE (smoke coming out, proceeding..)
    (40, 14),  # EMS13 ARRVD
    # T+45: Additional resources
    (45, 15),  # L31 ARSTN
    (45, 16),  # EMS11 ENRT
    (45, 17),  # EMS11 ENRST
    (45, 18),  # BN31 NOTE (cont priority, no source of smoke)
    (45, 19),  # DIV37 ENRT
    # T+50: EMS reassignments
    (50, 20),  # SJEM CMPLT
    (50, 21),  # EMS11 ARSTN
    (50, 22),  # EMS11 CMPLT
    # T+55: Command established, more arrivals
    (55, 23),  # L31 ENRT (with 2)
    (55, 24),  # BN31 NOTE (has command)
    (55, 25),  # EMS13 CMPLT
    (55, 26),  # E33 ARRVD
    (55, 27),  # E33 ARRVD
    (55, 28),  # L31 ARRVD
    # T+62: Source found, overhaul + hospital transport
    (62, 29),  # BN31 NOTE (elec fire, found source, overhaul)
    (62, 30),  # M12 ENRTH
    # T+70: Hospital arrival
    (70, 31),  # M12 ARVDH
    # T+78: Wrapping up
    (78, 32),  # BN31 NOTE (overhaul cmplt)
    (78, 33),  # BN31 NOTE (turned over to owner, cmd term)
    # T+85: All units returning
    (85, 34),  # SJF3 RTQ
    (85, 35),  # E31 RTQ
    (85, 36),  # E33 RTQ
    (85, 37),  # BN31 RTQ
    (85, 38),  # L31 RTQ
    (85, 39),  # DIV37 RTQ
    # T+92: Final completion
    (92, 40),  # M12 CMPLT
]


# ---------------------------------------------------------------------------
# Call builder
# ---------------------------------------------------------------------------


def _build_calls(t: float) -> list[dict]:
    """Build the list of active calls for elapsed seconds ``t``."""
    if 5 <= t < 100:
        return [_fire_alarm_call(t)]
    return []


def _fire_alarm_call(t: float) -> dict:
    """Fire-alarm call — replayed from fixture data.

    Starts at T+5 with address + geo (map loads immediately).
    Nature, responders fill in over time from fixture data.
    """
    detail = _load_fixture_detail()
    events = _load_fixture_events()
    base = _cycle_base(t)

    # Parse geo location from fixture
    geo = detail.get("iSpyGeoLocation", "")
    lat, lng = None, None
    if "," in geo:
        parts = geo.split(",")
        lat, lng = float(parts[0]), float(parts[1])

    city_info = detail.get("CityInfo", {})

    call: dict = {
        "dispatch_id": detail["LongTermCallID"],
        "long_term_call_id": detail["LongTermCallID"],
        "nature": "",
        "address": detail.get("RespondToAddress", ""),
        "city": city_info.get("City", ""),
        "state": city_info.get("StateAbbreviation", ""),
        "zip_code": city_info.get("ZIPCode", ""),
        "agency_code": detail.get("AgencyCode", "SJF3"),
        "type": "",
        "zone_code": "",
        "time_reported": base.isoformat(),
        "is_completed": False,
        "cad_comments": "",
        "responding_units": "",
        "responder_details": [],
        "geo_location": geo,
        "latitude": lat,
        "longitude": lng,
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

    # T+8: Nature arrives
    if t >= 8:
        call["nature"] = detail.get("Nature", "Fire-Alarm")
        call["type"] = "FIRE"
        call["zone_code"] = detail.get("ZoneCode", "SJF")
        call["severity"] = "high"
        call["icon"] = "\U0001f525"

    # T+10: Initial CAD comments from fixture
    if t >= 10:
        call["cad_comments"] = detail.get("JoinedComments", "")

    # ── Responder timeline (all 41 events from fixture) ──────
    responders: list[dict] = []
    units_seen: list[str] = []

    for sched_idx, (checkpoint_t, fixture_idx) in enumerate(_EVENT_SCHEDULE):
        if t < checkpoint_t:
            break  # Schedule is sorted; no later events can match

        evt = events[fixture_idx]
        unit = evt["UnitNumber"]
        status = evt["StatusDisplayCode"]
        # Sequential offsets (2s apart) keep log timestamps in order
        ts_offset = 7 + sched_idx * 2

        entry: dict = {
            "unit_call_sign": unit,
            "status": status,
            "time_of_status_change": _ts(base, ts_offset),
        }
        if status == "NOTE" and evt.get("RadioLog"):
            entry["radio_log"] = evt["RadioLog"]

        responders.append(entry)
        if unit not in units_seen:
            units_seen.append(unit)

    call["responder_details"] = responders
    call["responding_units"] = ", ".join(units_seen)

    # Analysis progressive updates
    if t >= 12:
        call["analysis"]["alarm_time"] = _ts(base, 7)  # SJF3 PAGED offset
    if t >= 16:
        call["analysis"]["first_enroute"] = _ts(base, 9)  # BN31 ENRT offset
    if t >= 55:
        call["analysis"]["incident_commander"] = "BN31"

    return call


# ---------------------------------------------------------------------------
# Fallback crew (overridden by real schedule data in server.py)
# ---------------------------------------------------------------------------


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
