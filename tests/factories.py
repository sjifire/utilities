"""Polyfactory model factories for generating test data.

Usage::

    from tests.factories import DispatchCallDocumentFactory, IncidentDocumentFactory

    # Single instance with defaults
    doc = DispatchCallDocumentFactory.build()

    # Override specific fields
    doc = IncidentDocumentFactory.build(status="submitted", station="S31")

    # Batch create
    docs = DispatchCallDocumentFactory.batch(5, agency_code="SJF")

    # Cosmos-serialized dict (for seeding in-memory stores)
    data = DispatchCallDocumentFactory.build().to_cosmos()
"""

from datetime import UTC, datetime

from polyfactory import Use
from polyfactory.factories.pydantic_factory import ModelFactory

from sjifire.ops.dispatch.models import (
    CrewOnDuty,
    DispatchAnalysis,
    DispatchCallDocument,
    UnitTiming,
)
from sjifire.ops.incidents.models import (
    DispatchNote,
    EditEntry,
    IncidentDocument,
    PersonnelAssignment,
    UnitAssignment,
)
from sjifire.ops.schedule.models import DayScheduleCache, ScheduleEntryCache

# ---------------------------------------------------------------------------
# Dispatch models
# ---------------------------------------------------------------------------


class UnitTimingFactory(ModelFactory):
    """Factory for dispatch unit timing records."""

    __model__ = UnitTiming

    unit = "E31"


class CrewOnDutyFactory(ModelFactory):
    """Factory for on-duty crew entries."""

    __model__ = CrewOnDuty

    section = "S31"
    position = "Firefighter"


class DispatchAnalysisFactory(ModelFactory):
    """Factory for dispatch call AI analysis."""

    __model__ = DispatchAnalysis

    summary = "Single-family residential structure fire, contained to kitchen."
    short_dsc = "Kitchen fire, contained"
    outcome = "Fire extinguished"
    patient_count = 0
    escalated = False


class DispatchCallDocumentFactory(ModelFactory):
    """Factory for Cosmos DB dispatch call documents.

    Produces realistic fire department dispatch data. Override any field::

        doc = DispatchCallDocumentFactory.build(nature="ALS Medical", type="EMS")
    """

    __model__ = DispatchCallDocument

    year = "2026"
    long_term_call_id = "26-001678"
    nature = "Medical Aid"
    address = "200 Spring St, Friday Harbor"
    agency_code = "SJF"
    type = "EMS"
    zone_code = "Z1"
    is_completed = True
    cad_comments = "Patient fall, conscious and breathing"
    responding_units = "E31, M31"
    city = "Friday Harbor"
    state = "WA"
    zip_code = "98250"
    responder_details = []


# ---------------------------------------------------------------------------
# Incident models
# ---------------------------------------------------------------------------


class PersonnelAssignmentFactory(ModelFactory):
    """Factory for incident personnel assignments."""

    __model__ = PersonnelAssignment

    name = "FF Garcia"
    email = "garcia@sjifire.org"
    rank = "Firefighter"
    position = "Firefighter"
    role = ""


class UnitAssignmentFactory(ModelFactory):
    """Factory for incident unit assignments."""

    __model__ = UnitAssignment

    unit_id = "E31"
    response_mode = "EMERGENT"


class DispatchNoteFactory(ModelFactory):
    """Factory for dispatch radio log notes."""

    __model__ = DispatchNote

    unit = "E31"
    text = "E31 en route"


class EditEntryFactory(ModelFactory):
    """Factory for incident edit history entries."""

    __model__ = EditEntry

    editor_email = "chief@sjifire.org"
    editor_name = "Chief Thompson"


class IncidentDocumentFactory(ModelFactory):
    """Factory for Cosmos DB incident documents.

    Produces a valid draft incident with minimal required fields.
    Override for specific test scenarios::

        doc = IncidentDocumentFactory.build(
            status="submitted",
            station="S31",
            units=[UnitAssignmentFactory.build(unit_id="E31")],
        )
    """

    __model__ = IncidentDocument

    incident_number = "26-000944"
    incident_datetime = datetime(2026, 2, 12, 14, 30, tzinfo=UTC)
    created_by = "chief@sjifire.org"
    status = "draft"
    address = "123 Main St"
    city = "Friday Harbor"
    state = "WA"
    station = "S31"

    # Keep complex nested fields empty by default — tests add what they need
    units = []
    attachments = []
    edit_history = []
    dispatch_notes = []
    extras = {}


# ---------------------------------------------------------------------------
# Schedule models
# ---------------------------------------------------------------------------


class ScheduleEntryFactory(ModelFactory):
    """Factory for schedule cache entries."""

    __model__ = ScheduleEntryCache

    name = "FF Garcia"
    position = "Firefighter"
    section = "S31"
    start_time = "08:00"
    end_time = "08:00"
    platoon = "A"


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class DayScheduleCacheFactory(ModelFactory):
    """Factory for cached day schedule documents.

    Uses today's date by default so the cache is never stale::

        sched = DayScheduleCacheFactory.build(
            entries=ScheduleEntryFactory.batch(4),
        )
    """

    __model__ = DayScheduleCache

    id = Use(fn=_today)
    date = Use(fn=_today)
    platoon = "A"
