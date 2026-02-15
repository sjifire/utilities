"""Tests for incident data models."""

from datetime import date

from sjifire.ops.incidents.models import CrewAssignment, IncidentDocument, Narratives


class TestCrewAssignment:
    def test_minimal(self):
        crew = CrewAssignment(name="John Smith")
        assert crew.name == "John Smith"
        assert crew.email is None
        assert crew.rank == ""
        assert crew.position == ""
        assert crew.unit == ""

    def test_full(self):
        crew = CrewAssignment(
            name="John Smith",
            email="john@sjifire.org",
            rank="Lieutenant",
            position="Engine Boss",
            unit="E31",
        )
        assert crew.email == "john@sjifire.org"
        assert crew.rank == "Lieutenant"
        assert crew.position == "Engine Boss"
        assert crew.unit == "E31"

    def test_rank_is_snapshot(self):
        """Rank captures what the person was at incident time, not current."""
        crew = CrewAssignment(name="Smith", email="smith@sjifire.org", rank="Lieutenant")
        assert crew.rank == "Lieutenant"
        # This value doesn't change even if the person is promoted later --
        # it's a snapshot of their rank when the incident occurred


class TestNarratives:
    def test_defaults_empty(self):
        narr = Narratives()
        assert narr.outcome == ""
        assert narr.actions_taken == ""

    def test_with_values(self):
        narr = Narratives(outcome="Structure fire contained", actions_taken="Deployed 2 lines")
        assert narr.outcome == "Structure fire contained"


class TestIncidentDocument:
    def _make_doc(self, **overrides):
        defaults = {
            "station": "S31",
            "incident_number": "26-000944",
            "incident_date": date(2026, 2, 12),
            "created_by": "chief@sjifire.org",
        }
        defaults.update(overrides)
        return IncidentDocument(**defaults)

    def test_defaults(self):
        doc = self._make_doc()
        assert doc.status == "draft"
        assert doc.city == "Friday Harbor"
        assert doc.state == "WA"
        assert doc.crew == []
        assert doc.internal_notes == ""
        assert doc.neris_incident_id is None
        assert doc.id  # Should have auto-generated UUID

    def test_auto_generated_id(self):
        doc1 = self._make_doc()
        doc2 = self._make_doc()
        assert doc1.id != doc2.id

    def test_to_cosmos_roundtrip(self):
        doc = self._make_doc(
            crew=[
                CrewAssignment(name="Jane Doe", email="jane@sjifire.org", position="FF", unit="E31")
            ],
            narratives=Narratives(outcome="Fire contained"),
            incident_type="111",
            address="100 Spring St",
        )
        cosmos_dict = doc.to_cosmos()
        assert isinstance(cosmos_dict, dict)
        assert cosmos_dict["station"] == "S31"
        assert cosmos_dict["crew"][0]["name"] == "Jane Doe"

        restored = IncidentDocument.from_cosmos(cosmos_dict)
        assert restored.station == doc.station
        assert restored.incident_number == doc.incident_number
        assert restored.crew[0].email == "jane@sjifire.org"

    def test_to_neris_payload_minimal(self):
        doc = self._make_doc()
        payload = doc.to_neris_payload()
        assert payload["incident_number"] == "26-000944"
        assert payload["incident_date"] == "2026-02-12"
        assert "address" not in payload
        assert "narrative" not in payload

    def test_to_neris_payload_full(self):
        doc = self._make_doc(
            incident_type="111",
            address="100 Spring St",
            latitude=48.5343,
            longitude=-123.0178,
            narratives=Narratives(outcome="Contained", actions_taken="Deployed lines"),
            timestamps={"dispatch": "2026-02-12T10:00:00"},
            unit_responses=[{"unit_id": "E31", "response_type": "first_due"}],
        )
        payload = doc.to_neris_payload()
        assert payload["type"]["code"] == "111"
        assert payload["address"]["city"] == "Friday Harbor"
        assert payload["location"]["latitude"] == 48.5343
        assert payload["narrative"]["outcome"] == "Contained"
        assert payload["timestamps"]["dispatch"] == "2026-02-12T10:00:00"
        assert len(payload["apparatus"]) == 1

    def test_to_neris_excludes_internal_notes(self):
        doc = self._make_doc(internal_notes="Private note for dept only")
        payload = doc.to_neris_payload()
        assert "internal_notes" not in str(payload)

    def test_crew_emails(self):
        doc = self._make_doc(
            crew=[
                CrewAssignment(name="John", email="JOHN@sjifire.org"),
                CrewAssignment(name="Jane", email="jane@sjifire.org"),
                CrewAssignment(name="Unknown"),  # No email
            ]
        )
        emails = doc.crew_emails()
        assert "john@sjifire.org" in emails
        assert "jane@sjifire.org" in emails
        assert len(emails) == 2

    def test_crew_emails_empty(self):
        doc = self._make_doc()
        assert doc.crew_emails() == set()

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
            crew=[CrewAssignment(name="John", email="john@sjifire.org")],
        )
        result = doc.completeness()
        assert result["filled"] == 3
        assert result["total"] == 7
        assert result["sections"]["incident_type"] is True
        assert result["sections"]["address"] is True
        assert result["sections"]["crew"] is True
        assert result["sections"]["narrative"] is False
        assert result["sections"]["actions_taken"] is False
        assert result["sections"]["timestamps"] is False

    def test_completeness_full(self):
        doc = self._make_doc(
            incident_type="111",
            address="100 Spring St",
            crew=[CrewAssignment(name="John")],
            unit_responses=[{"unit": "E31"}],
            narratives=Narratives(outcome="Contained", actions_taken="Deployed lines"),
            timestamps={"dispatch": "2026-02-12T10:00:00"},
        )
        result = doc.completeness()
        assert result["filled"] == 7
        assert result["total"] == 7
        assert all(result["sections"].values())

    def test_completeness_outcome_only(self):
        doc = self._make_doc(narratives=Narratives(outcome="Contained"))
        result = doc.completeness()
        assert result["sections"]["narrative"] is True
        assert result["sections"]["actions_taken"] is False

    def test_completeness_actions_taken_only(self):
        doc = self._make_doc(narratives=Narratives(actions_taken="Deployed lines"))
        result = doc.completeness()
        assert result["sections"]["narrative"] is False
        assert result["sections"]["actions_taken"] is True

    def test_completeness_empty_narratives(self):
        doc = self._make_doc(narratives=Narratives(outcome="", actions_taken=""))
        result = doc.completeness()
        assert result["sections"]["narrative"] is False
        assert result["sections"]["actions_taken"] is False

    def test_completeness_survives_cosmos_roundtrip(self):
        doc = self._make_doc(
            incident_type="111",
            address="100 Spring St",
            crew=[CrewAssignment(name="John")],
        )
        original = doc.completeness()
        restored = IncidentDocument.from_cosmos(doc.to_cosmos())
        assert restored.completeness() == original
