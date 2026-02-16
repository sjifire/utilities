"""Tests for incident data models."""

from datetime import UTC, datetime

from sjifire.ops.incidents.models import (
    IncidentDocument,
    PersonnelAssignment,
    UnitAssignment,
)


class TestPersonnelAssignment:
    def test_minimal(self):
        person = PersonnelAssignment(name="John Smith")
        assert person.name == "John Smith"
        assert person.email is None
        assert person.rank == ""
        assert person.position == ""

    def test_full(self):
        person = PersonnelAssignment(
            name="John Smith",
            email="john@sjifire.org",
            rank="Lieutenant",
            position="Engine Boss",
        )
        assert person.email == "john@sjifire.org"
        assert person.rank == "Lieutenant"
        assert person.position == "Engine Boss"

    def test_rank_is_snapshot(self):
        """Rank captures what the person was at incident time, not current."""
        person = PersonnelAssignment(name="Smith", email="smith@sjifire.org", rank="Lieutenant")
        assert person.rank == "Lieutenant"
        # This value doesn't change even if the person is promoted later --
        # it's a snapshot of their rank when the incident occurred


class TestUnitAssignment:
    def test_minimal(self):
        unit = UnitAssignment(unit_id="E31")
        assert unit.unit_id == "E31"
        assert unit.response_mode == ""
        assert unit.personnel == []
        assert unit.dispatch == ""
        assert unit.on_scene == ""

    def test_with_personnel(self):
        unit = UnitAssignment(
            unit_id="E31",
            personnel=[PersonnelAssignment(name="John", email="john@sjifire.org", position="FF")],
        )
        assert len(unit.personnel) == 1
        assert unit.personnel[0].name == "John"


class TestIncidentDocument:
    def _make_doc(self, **overrides):
        defaults = {
            "incident_number": "26-000944",
            "incident_datetime": datetime(2026, 2, 12, tzinfo=UTC),
            "created_by": "chief@sjifire.org",
        }
        defaults.update(overrides)
        return IncidentDocument(**defaults)

    def test_defaults(self):
        doc = self._make_doc()
        assert doc.status == "draft"
        assert doc.city == "Friday Harbor"
        assert doc.state == "WA"
        assert doc.units == []
        assert doc.internal_notes == ""
        assert doc.neris_incident_id is None
        assert doc.id  # Should have auto-generated UUID

    def test_auto_generated_id(self):
        doc1 = self._make_doc()
        doc2 = self._make_doc()
        assert doc1.id != doc2.id

    def test_to_cosmos_roundtrip(self):
        doc = self._make_doc(
            units=[
                UnitAssignment(
                    unit_id="E31",
                    personnel=[
                        PersonnelAssignment(
                            name="Jane Doe", email="jane@sjifire.org", position="FF"
                        )
                    ],
                )
            ],
            narrative="Fire contained",
            incident_type="111",
            address="100 Spring St",
            extras={"station": "S31"},
        )
        cosmos_dict = doc.to_cosmos()
        assert isinstance(cosmos_dict, dict)
        assert cosmos_dict["extras"]["station"] == "S31"
        assert cosmos_dict["units"][0]["personnel"][0]["name"] == "Jane Doe"

        restored = IncidentDocument.from_cosmos(cosmos_dict)
        assert restored.extras.get("station") == doc.extras.get("station")
        assert restored.incident_number == doc.incident_number
        assert restored.units[0].personnel[0].email == "jane@sjifire.org"

    def test_personnel_emails(self):
        doc = self._make_doc(
            units=[
                UnitAssignment(
                    unit_id="E31",
                    personnel=[
                        PersonnelAssignment(name="John", email="JOHN@sjifire.org"),
                        PersonnelAssignment(name="Jane", email="jane@sjifire.org"),
                        PersonnelAssignment(name="Unknown"),  # No email
                    ],
                )
            ]
        )
        emails = doc.personnel_emails()
        assert "john@sjifire.org" in emails
        assert "jane@sjifire.org" in emails
        assert len(emails) == 2

    def test_personnel_emails_empty(self):
        doc = self._make_doc()
        assert doc.personnel_emails() == set()

    def test_completeness_empty(self):
        doc = self._make_doc()
        result = doc.completeness()
        assert result["filled"] == 0
        assert result["total"] == 7
        assert not any(result["sections"].values())

    def test_completeness_partial(self):
        doc = self._make_doc(
            incident_type="111",
            address="100 Spring St",
            units=[
                UnitAssignment(
                    unit_id="E31",
                    personnel=[PersonnelAssignment(name="John", email="john@sjifire.org")],
                )
            ],
        )
        result = doc.completeness()
        assert result["filled"] == 4
        assert result["total"] == 7
        assert result["sections"]["incident_type"] is True
        assert result["sections"]["units"] is True
        assert result["sections"]["address"] is True
        assert result["sections"]["personnel"] is True
        assert result["sections"]["narrative"] is False
        assert result["sections"]["actions_taken"] is False
        assert result["sections"]["timestamps"] is False

    def test_completeness_full(self):
        doc = self._make_doc(
            incident_type="111",
            address="100 Spring St",
            units=[
                UnitAssignment(
                    unit_id="E31",
                    personnel=[PersonnelAssignment(name="John")],
                )
            ],
            narrative="Contained",
            action_taken="ACTION",
            action_codes=["EMERGENCY_MEDICAL_CARE||PATIENT_ASSESSMENT"],
            timestamps={"dispatch": "2026-02-12T10:00:00"},
        )
        result = doc.completeness()
        assert result["filled"] == 7
        assert result["total"] == 7
        assert all(result["sections"].values())

    def test_completeness_narrative_only(self):
        doc = self._make_doc(narrative="Contained")
        result = doc.completeness()
        assert result["sections"]["narrative"] is True

    def test_completeness_empty_narrative(self):
        doc = self._make_doc(narrative="")
        result = doc.completeness()
        assert result["sections"]["narrative"] is False

    # ── NOACTION / ACTION fields ──

    def test_new_fields_default_none_and_empty(self):
        doc = self._make_doc()
        assert doc.action_taken is None
        assert doc.noaction_reason is None
        assert doc.action_codes == []

    def test_completeness_noaction_counts_as_complete(self):
        doc = self._make_doc(action_taken="NOACTION", noaction_reason="CANCELLED")
        result = doc.completeness()
        assert result["sections"]["actions_taken"] is True

    def test_completeness_action_with_codes_counts_as_complete(self):
        doc = self._make_doc(
            action_taken="ACTION",
            action_codes=["EMERGENCY_MEDICAL_CARE||PATIENT_ASSESSMENT"],
        )
        result = doc.completeness()
        assert result["sections"]["actions_taken"] is True

    def test_completeness_action_without_codes_is_incomplete(self):
        doc = self._make_doc(action_taken="ACTION")
        result = doc.completeness()
        assert result["sections"]["actions_taken"] is False

    def test_cosmos_roundtrip_with_action_fields(self):
        doc = self._make_doc(
            action_taken="NOACTION",
            noaction_reason="STAGED_STANDBY",
        )
        cosmos_dict = doc.to_cosmos()
        restored = IncidentDocument.from_cosmos(cosmos_dict)
        assert restored.action_taken == "NOACTION"
        assert restored.noaction_reason == "STAGED_STANDBY"
        assert restored.action_codes == []

    def test_cosmos_roundtrip_without_action_fields(self):
        """Old documents without action fields load with defaults."""
        doc = self._make_doc()
        cosmos_dict = doc.to_cosmos()
        # Simulate old doc missing action fields
        del cosmos_dict["action_taken"]
        del cosmos_dict["noaction_reason"]
        del cosmos_dict["action_codes"]
        restored = IncidentDocument.from_cosmos(cosmos_dict)
        assert restored.action_taken is None
        assert restored.noaction_reason is None
        assert restored.action_codes == []

    def test_completeness_survives_cosmos_roundtrip(self):
        doc = self._make_doc(
            incident_type="111",
            address="100 Spring St",
            units=[
                UnitAssignment(
                    unit_id="E31",
                    personnel=[PersonnelAssignment(name="John")],
                )
            ],
        )
        original = doc.completeness()
        restored = IncidentDocument.from_cosmos(doc.to_cosmos())
        assert restored.completeness() == original
