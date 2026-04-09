"""Test fixture data for seeding the ops server in-memory stores.

These dicts are Cosmos-serialized format (matching ``to_cosmos()`` output)
so they can be written directly into the class-level ``_memory`` dicts.
"""

from datetime import timedelta

from sjifire.core.config import local_now

# Use the server's local timezone for dates so seeded schedule data aligns
# with what get_on_duty_crew() requests.  The server uses local_now() (Pacific),
# not UTC — when UTC is a day ahead of Pacific (after 5 PM), UTC dates won't
# match the server's needed dates.
_now = local_now()
_today = _now.strftime("%Y-%m-%d")
_yesterday = (_now - timedelta(days=1)).strftime("%Y-%m-%d")
_tomorrow = (_now + timedelta(days=1)).strftime("%Y-%m-%d")


def _hours_ago(hours: int) -> str:
    return (_now - timedelta(hours=hours)).isoformat()


# ---------------------------------------------------------------------------
# Dispatch calls (Cosmos-serialized DispatchCallDocument dicts)
# ---------------------------------------------------------------------------

DISPATCH_CALLS = [
    {
        "id": "aaa-111",
        "year": _now.strftime("%Y"),
        "long_term_call_id": f"{_now.strftime('%y')}-001001",
        "nature": "Structure Fire",
        "address": "123 Main St, Friday Harbor",
        "agency_code": "SJF",
        "type": "FIRE",
        "is_completed": True,
        "time_reported": _hours_ago(3),
        "cad_comments": "Smoke showing from roof",
        "responding_units": "E31, L31, BC31",
        "analysis": {
            "incident_commander": "BC31",
            "incident_commander_name": "Smith",
            "alarm_time": "14:00:00",
            "first_enroute": "14:01:30",
            "summary": "Single-family residential structure fire, contained to kitchen.",
            "short_dsc": "Kitchen fire, contained",
            "outcome": "Fire extinguished",
            "actions_taken": ["Fire suppression", "Ventilation", "Overhaul"],
            "patient_count": 0,
            "escalated": False,
            "unit_times": [
                {
                    "unit": "E31",
                    "paged": "14:00:00",
                    "enroute": "14:01:30",
                    "arrived": "14:06:00",
                    "completed": "15:30:00",
                },
            ],
            "on_duty_crew": [],
            "key_events": [],
        },
        "stored_at": _now.isoformat(),
    },
    {
        "id": "bbb-222",
        "year": _now.strftime("%Y"),
        "long_term_call_id": f"{_now.strftime('%y')}-001002",
        "nature": "ALS Medical",
        "address": "456 Spring St, Friday Harbor",
        "agency_code": "SJF",
        "type": "EMS",
        "is_completed": True,
        "time_reported": _hours_ago(6),
        "cad_comments": "Chest pain, difficulty breathing",
        "responding_units": "M31, E31",
        "analysis": {
            "incident_commander": "M31",
            "incident_commander_name": "Jones",
            "alarm_time": "11:00:00",
            "first_enroute": "11:01:00",
            "summary": "Cardiac event, patient transported to hospital.",
            "short_dsc": "Chest pain, transported",
            "outcome": "Patient transported",
            "actions_taken": ["Patient assessment", "ALS intervention", "Transport"],
            "patient_count": 1,
            "escalated": False,
            "unit_times": [],
            "on_duty_crew": [],
            "key_events": [],
        },
        "stored_at": _now.isoformat(),
    },
    {
        "id": "ccc-333",
        "year": _now.strftime("%Y"),
        "long_term_call_id": f"{_now.strftime('%y')}-001003",
        "nature": "Fire Alarm",
        "address": "789 Guard St, Friday Harbor",
        "agency_code": "SJF",
        "type": "ALARM",
        "is_completed": True,
        "time_reported": _hours_ago(12),
        "cad_comments": "Commercial alarm activation",
        "responding_units": "E31",
        "analysis": {
            "incident_commander": "",
            "incident_commander_name": "",
            "alarm_time": "",
            "first_enroute": "",
            "summary": "False alarm, no fire found.",
            "short_dsc": "False alarm",
            "outcome": "No action needed",
            "actions_taken": ["Investigation"],
            "patient_count": 0,
            "escalated": False,
            "unit_times": [],
            "on_duty_crew": [],
            "key_events": [],
        },
        "stored_at": _now.isoformat(),
    },
]


# ---------------------------------------------------------------------------
# Schedule cache (Cosmos-serialized DayScheduleCache dicts)
# ---------------------------------------------------------------------------

SCHEDULE = [
    {
        "id": _today,
        "date": _today,
        "platoon": "A",
        "entries": [
            {
                "name": "Chief Thompson",
                "position": "Battalion Chief",
                "section": "Chief Officer",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "A",
            },
            {
                "name": "Capt Rodriguez",
                "position": "Captain",
                "section": "S31",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "A",
            },
            {
                "name": "Lt Nguyen",
                "position": "Lieutenant",
                "section": "S31",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "A",
            },
            {
                "name": "FF Garcia",
                "position": "Firefighter",
                "section": "S31",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "A",
            },
            {
                "name": "AO Patel",
                "position": "Apparatus Operator",
                "section": "S31",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "A",
            },
            {
                "name": "FF Wilson",
                "position": "Firefighter",
                "section": "S32",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "A",
            },
        ],
        "fetched_at": _now.isoformat(),
    },
    {
        "id": _yesterday,
        "date": _yesterday,
        "platoon": "B",
        "entries": [
            {
                "name": "Chief Anderson",
                "position": "Battalion Chief",
                "section": "Chief Officer",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "B",
            },
            {
                "name": "Capt Lee",
                "position": "Captain",
                "section": "S31",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "B",
            },
        ],
        "fetched_at": (_now - timedelta(days=1)).isoformat(),
    },
    {
        "id": _tomorrow,
        "date": _tomorrow,
        "platoon": "B",
        "entries": [
            {
                "name": "Chief Anderson",
                "position": "Battalion Chief",
                "section": "Chief Officer",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "B",
            },
            {
                "name": "Capt Lee",
                "position": "Captain",
                "section": "S31",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "B",
            },
        ],
        "fetched_at": _now.isoformat(),
    },
]


def seed_payload() -> dict:
    """Return the full payload for POST /test/seed."""
    return {
        "dispatch_calls": DISPATCH_CALLS,
        "schedule": SCHEDULE,
    }
