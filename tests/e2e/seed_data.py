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
            {
                "name": "Lt Kim",
                "position": "Lieutenant",
                "section": "S31",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "B",
            },
            {
                "name": "FF Davis",
                "position": "Firefighter",
                "section": "S31",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "B",
            },
            {
                "name": "AO Martinez",
                "position": "Apparatus Operator",
                "section": "S31",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "B",
            },
            {
                "name": "FF Brown",
                "position": "Firefighter",
                "section": "S32",
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
            {
                "name": "Lt Kim",
                "position": "Lieutenant",
                "section": "S31",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "B",
            },
            {
                "name": "FF Davis",
                "position": "Firefighter",
                "section": "S31",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "B",
            },
            {
                "name": "AO Martinez",
                "position": "Apparatus Operator",
                "section": "S31",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "B",
            },
            {
                "name": "FF Brown",
                "position": "Firefighter",
                "section": "S32",
                "start_time": "08:00",
                "end_time": "08:00",
                "platoon": "B",
            },
        ],
        "fetched_at": _now.isoformat(),
    },
]


# ---------------------------------------------------------------------------
# Incidents (Cosmos-serialized IncidentDocument dicts)
# ---------------------------------------------------------------------------

INCIDENTS = [
    {
        "id": "inc-draft-001",
        "year": _now.strftime("%Y"),
        "status": "draft",
        "incident_number": f"{_now.strftime('%y')}-001001",
        "incident_datetime": _hours_ago(3),
        "incident_type": "111 - Building fire",
        "address": "123 Main St, Friday Harbor",
        "city": "Friday Harbor",
        "state": "WA",
        "zip_code": "98250",
        "county": "San Juan",
        "station": "S31",
        "narrative": "Engine 31 arrived on scene to find smoke showing from the roof.",
        "units": [
            {
                "unit_id": "E31",
                "response_mode": "EMERGENT",
                "personnel": [
                    {"name": "Capt Rodriguez", "email": "rodriguez@sjifire.org", "rank": "Captain"},
                ],
            },
        ],
        "timestamps": {"dispatch": _hours_ago(3), "first_arriving": _hours_ago(3)},
        "action_taken": "ACTION",
        "action_codes": ["31 - Extinguishment by fire service personnel"],
        "created_by": "dev@localhost",
        "created_at": _hours_ago(2),
    },
    {
        "id": "inc-submitted-002",
        "year": _now.strftime("%Y"),
        "status": "submitted",
        "incident_number": f"{_now.strftime('%y')}-001002",
        "incident_datetime": _hours_ago(6),
        "incident_type": "321 - EMS call, excluding vehicle accident with injury",
        "address": "456 Spring St, Friday Harbor",
        "city": "Friday Harbor",
        "state": "WA",
        "zip_code": "98250",
        "county": "San Juan",
        "station": "S31",
        "narrative": "Responded to a cardiac event. Patient transported to hospital.",
        "units": [
            {
                "unit_id": "M31",
                "response_mode": "EMERGENT",
                "personnel": [
                    {"name": "FF Jones", "email": "jones@sjifire.org", "rank": "Firefighter"},
                ],
            },
        ],
        "timestamps": {"dispatch": _hours_ago(6)},
        "action_taken": "ACTION",
        "action_codes": ["33 - Provide basic life support (BLS)"],
        "created_by": "dev@localhost",
        "created_at": _hours_ago(5),
        "neris_incident_id": "FD53055879|26001002|1770500761",
    },
]


def seed_payload() -> dict:
    """Return the full payload for POST /test/seed."""
    return {
        "dispatch_calls": DISPATCH_CALLS,
        "schedule": SCHEDULE,
    }


def editor_seed_payload() -> dict:
    """Return payload for POST /test/seed with editor mode + incidents."""
    return {
        "incidents": INCIDENTS,
        "is_editor": True,
    }
