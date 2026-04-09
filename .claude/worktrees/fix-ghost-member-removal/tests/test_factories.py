"""Tests verifying polyfactory model factories produce valid instances."""

from datetime import UTC, datetime

from tests.factories import (
    CrewOnDutyFactory,
    DayScheduleCacheFactory,
    DispatchAnalysisFactory,
    DispatchCallDocumentFactory,
    DispatchNoteFactory,
    EditEntryFactory,
    IncidentDocumentFactory,
    PersonnelAssignmentFactory,
    ScheduleEntryFactory,
    UnitAssignmentFactory,
    UnitTimingFactory,
)


class TestDispatchFactories:
    def test_build_dispatch_document(self):
        doc = DispatchCallDocumentFactory.build()
        assert doc.nature == "Medical Aid"
        assert doc.agency_code == "SJF"
        assert doc.year == "2026"

    def test_build_with_overrides(self):
        doc = DispatchCallDocumentFactory.build(nature="Structure Fire", type="FIRE")
        assert doc.nature == "Structure Fire"
        assert doc.type == "FIRE"

    def test_batch_creates_unique_ids(self):
        docs = DispatchCallDocumentFactory.batch(5)
        ids = {d.id for d in docs}
        assert len(ids) == 5

    def test_to_cosmos_roundtrip(self):
        doc = DispatchCallDocumentFactory.build()
        cosmos_dict = doc.to_cosmos()
        from sjifire.ops.dispatch.models import DispatchCallDocument

        restored = DispatchCallDocument.from_cosmos(cosmos_dict)
        assert restored.nature == doc.nature
        assert restored.id == doc.id

    def test_unit_timing_factory(self):
        timing = UnitTimingFactory.build(unit="M31", arrived="14:06:00")
        assert timing.unit == "M31"
        assert timing.arrived == "14:06:00"

    def test_crew_on_duty_factory(self):
        crew = CrewOnDutyFactory.build(name="Capt Rodriguez")
        assert crew.name == "Capt Rodriguez"
        assert crew.section == "S31"

    def test_dispatch_analysis_factory(self):
        analysis = DispatchAnalysisFactory.build()
        assert "structure fire" in analysis.summary.lower()


class TestIncidentFactories:
    def test_build_incident_document(self):
        doc = IncidentDocumentFactory.build()
        assert doc.incident_number == "26-000944"
        assert doc.status == "draft"
        assert doc.station == "S31"
        assert doc.year == "2026"  # derived by model validator

    def test_build_with_status_override(self):
        doc = IncidentDocumentFactory.build(status="submitted")
        assert doc.status == "submitted"

    def test_build_with_nested_units(self):
        doc = IncidentDocumentFactory.build(
            units=[
                UnitAssignmentFactory.build(
                    unit_id="E31",
                    personnel=[
                        PersonnelAssignmentFactory.build(name="Capt Rodriguez", rank="Captain"),
                        PersonnelAssignmentFactory.build(name="FF Garcia"),
                    ],
                ),
                UnitAssignmentFactory.build(unit_id="M31"),
            ],
        )
        assert len(doc.units) == 2
        assert doc.units[0].unit_id == "E31"
        assert len(doc.units[0].personnel) == 2
        assert doc.units[0].personnel[0].rank == "Captain"

    def test_personnel_count(self):
        doc = IncidentDocumentFactory.build(
            units=[
                UnitAssignmentFactory.build(
                    personnel=PersonnelAssignmentFactory.batch(3),
                ),
            ],
        )
        assert doc.personnel_count() == 3

    def test_to_cosmos_roundtrip(self):
        doc = IncidentDocumentFactory.build()
        cosmos_dict = doc.to_cosmos()
        from sjifire.ops.incidents.models import IncidentDocument

        restored = IncidentDocument.from_cosmos(cosmos_dict)
        assert restored.incident_number == doc.incident_number
        assert restored.id == doc.id

    def test_dispatch_note_factory(self):
        note = DispatchNoteFactory.build(text="E31 on scene")
        assert note.text == "E31 on scene"

    def test_edit_entry_factory(self):
        entry = EditEntryFactory.build()
        assert entry.editor_email == "chief@sjifire.org"


class TestScheduleFactories:
    def test_build_schedule_entry(self):
        entry = ScheduleEntryFactory.build()
        assert entry.name == "FF Garcia"
        assert entry.platoon == "A"

    def test_build_day_schedule(self):
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        sched = DayScheduleCacheFactory.build(entries=ScheduleEntryFactory.batch(4))
        assert sched.date == today
        assert sched.id == today
        assert len(sched.entries) == 4
        assert sched.platoon == "A"

    def test_schedule_not_stale(self):
        sched = DayScheduleCacheFactory.build(fetched_at=datetime.now(UTC))
        assert not sched.is_stale()
