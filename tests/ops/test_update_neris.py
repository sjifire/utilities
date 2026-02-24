"""Tests for update_neris_incident tool."""

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.ops.auth import UserContext, set_current_user
from sjifire.ops.incidents.models import IncidentDocument, UnitAssignment
from sjifire.ops.incidents.tools import (
    _build_neris_diff,
    _build_neris_patch,
    _timestamps_equal,
    update_neris_incident,
)


# Fixtures
@pytest.fixture(autouse=True)
def _editor_group_env():
    """Set the editor group ID for all tests."""
    import sjifire.ops.auth

    sjifire.ops.auth._EDITOR_GROUP_ID = None
    sjifire.ops.auth._editor_cache.clear()
    sjifire.ops.auth._user_id_cache.clear()
    with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "officer-group"}):
        yield
    sjifire.ops.auth._EDITOR_GROUP_ID = None
    sjifire.ops.auth._editor_cache.clear()
    sjifire.ops.auth._user_id_cache.clear()


@pytest.fixture
def regular_user():
    user = UserContext(
        email="ff@sjifire.org", name="Firefighter", user_id="user-1", groups=frozenset()
    )
    set_current_user(user)
    return user


@pytest.fixture
def officer_user():
    user = UserContext(
        email="chief@sjifire.org",
        name="Chief",
        user_id="user-2",
        groups=frozenset(["officer-group"]),
    )
    set_current_user(user)
    return user


@pytest.fixture
def sample_doc():
    return IncidentDocument(
        id="doc-neris-1",
        incident_number="26-002358",
        incident_datetime=datetime(2026, 2, 20, tzinfo=UTC),
        created_by="chief@sjifire.org",
        neris_incident_id="FD53055879|26SJ0020|1770457554",
        narrative="Our local narrative is more accurate.",
        address="94 Zepher Ln",
        city="Friday Harbor",
        state="WA",
        zip_code="98250",
        timestamps={
            "psap_answer": "2026-02-20T10:30:00Z",
            "incident_clear": "2026-02-20T11:15:00Z",
        },
        units=[
            UnitAssignment(
                unit_id="E31",
                dispatch="2026-02-20T10:31:00Z",
                enroute="2026-02-20T10:32:00Z",
                on_scene="2026-02-20T10:40:00Z",
                cleared="2026-02-20T11:10:00Z",
            ),
        ],
    )


@pytest.fixture
def neris_record():
    """Simulated NERIS record with different data from local."""
    return {
        "incident_status": {"status": "SUBMITTED"},
        "base": {
            "outcome_narrative": "NERIS original narrative",
            "location": {
                "street_address": "94 Zepher Lane",
                "incorporated_municipality": "Friday Harbor",
                "state": "WA",
                "postal_code": "98250",
            },
            "people_present": True,
        },
        "dispatch": {
            "call_create": "2026-02-20T10:29:00Z",
            "incident_clear": "2026-02-20T11:14:00Z",
            "unit_responses": [
                {
                    "reported_unit_id": "E31",
                    "dispatch": "2026-02-20T10:30:00Z",
                    "enroute_to_scene": "2026-02-20T10:32:00Z",
                    "on_scene": "2026-02-20T10:40:00Z",
                    "unit_clear": "2026-02-20T11:10:00Z",
                },
            ],
        },
        "incident_types": [{"type": "MEDICAL||PATIENT_ASSIST"}],
    }


class TestBuildNerisDiff:
    """Tests for _build_neris_diff."""

    def test_detects_narrative_diff(self, sample_doc, neris_record):
        diff = _build_neris_diff(sample_doc, neris_record)
        assert "narrative" in diff
        assert diff["narrative"]["local"] == "Our local narrative is more accurate."
        assert diff["narrative"]["neris"] == "NERIS original narrative"

    def test_detects_timestamp_diff(self, sample_doc, neris_record):
        diff = _build_neris_diff(sample_doc, neris_record)
        assert "timestamps" in diff
        assert "psap_answer" in diff["timestamps"]["local"]

    def test_detects_unit_timestamp_diff(self, sample_doc, neris_record):
        diff = _build_neris_diff(sample_doc, neris_record)
        assert "units" in diff
        assert "E31.dispatch" in diff["units"]["local"]

    def test_no_diff_when_matching(self, neris_record):
        """When local matches NERIS, diff should be empty for those fields."""
        doc = IncidentDocument(
            id="doc-match",
            incident_number="26-002358",
            incident_datetime=datetime(2026, 2, 20, tzinfo=UTC),
            created_by="chief@sjifire.org",
            neris_incident_id="FD53055879|26SJ0020|1770457554",
            narrative="NERIS original narrative",
            city="Friday Harbor",
            state="WA",
            zip_code="98250",
            timestamps={
                "psap_answer": "2026-02-20T10:29:00Z",
                "incident_clear": "2026-02-20T11:14:00Z",
            },
        )
        diff = _build_neris_diff(doc, neris_record)
        assert "narrative" not in diff
        assert "timestamps" not in diff

    def test_detects_address_diff(self, sample_doc, neris_record):
        diff = _build_neris_diff(sample_doc, neris_record)
        # Address differs: "94 Zepher Ln" vs parsed NERIS address
        assert "address" in diff

    def test_first_unit_dispatched_diff(self, sample_doc, neris_record):
        """Detect first_unit_dispatched timestamp differences."""
        sample_doc.timestamps["first_unit_dispatched"] = "2026-02-20T10:31:00Z"
        neris_record["dispatch"]["first_unit_dispatched"] = "2026-02-20T10:30:00Z"
        diff = _build_neris_diff(sample_doc, neris_record)
        assert "timestamps" in diff
        assert "first_unit_dispatched" in diff["timestamps"]["local"]
        assert diff["timestamps"]["local"]["first_unit_dispatched"] == "2026-02-20T10:31:00Z"
        assert diff["timestamps"]["neris"]["first_unit_dispatched"] == "2026-02-20T10:30:00Z"

    def test_automatic_alarm_diff(self, sample_doc, neris_record):
        """Detect automatic_alarm differences."""
        sample_doc.automatic_alarm = True
        neris_record["dispatch"]["automatic_alarm"] = False
        diff = _build_neris_diff(sample_doc, neris_record)
        assert "automatic_alarm" in diff
        assert diff["automatic_alarm"]["local"] is True
        assert diff["automatic_alarm"]["neris"] is False

    def test_unit_staged_diff(self, sample_doc, neris_record):
        """Detect unit staged time differences (maps to NERIS staging)."""
        sample_doc.units[0].staged = "2026-02-20T10:35:00Z"
        neris_record["dispatch"]["unit_responses"][0]["staging"] = "2026-02-20T10:34:00Z"
        diff = _build_neris_diff(sample_doc, neris_record)
        assert "units" in diff
        assert "E31.staged" in diff["units"]["local"]
        assert diff["units"]["local"]["E31.staged"] == "2026-02-20T10:35:00Z"
        assert diff["units"]["neris"]["E31.staged"] == "2026-02-20T10:34:00Z"


class TestTimestampsEqual:
    """Tests for timezone-aware timestamp comparison."""

    def test_identical_strings(self):
        assert _timestamps_equal("2026-02-20T10:30:00Z", "2026-02-20T10:30:00Z")

    def test_utc_matches_local_pacific(self):
        """A naive local time (Pacific) and UTC string representing the same instant match."""
        # Feb 20 = PST (UTC-8), so 02:30 local = 10:30 UTC
        assert _timestamps_equal("2026-02-20T02:30:00", "2026-02-20T10:30:00Z")

    def test_utc_does_not_match_different_local(self):
        """Different instants should not match."""
        assert not _timestamps_equal("2026-02-20T10:30:00", "2026-02-20T10:30:00Z")

    def test_empty_strings(self):
        assert not _timestamps_equal("", "2026-02-20T10:30:00Z")
        assert not _timestamps_equal("2026-02-20T10:30:00Z", "")

    def test_invalid_strings(self):
        assert not _timestamps_equal("not-a-date", "2026-02-20T10:30:00Z")

    def test_dispatch_level_no_false_diff(self, neris_record):
        """Local timestamps in Pacific should NOT produce a diff when they match NERIS UTC."""
        # Set local timestamps as naive Pacific time matching the NERIS UTC values
        # NERIS has call_create=2026-02-20T10:29:00Z, incident_clear=2026-02-20T11:14:00Z
        # Pacific (PST = UTC-8): 02:29 and 03:14
        doc = IncidentDocument(
            id="tz-test",
            incident_number="26-002358",
            incident_datetime=datetime(2026, 2, 20, tzinfo=UTC),
            created_by="chief@sjifire.org",
            neris_incident_id="FD53055879|26SJ0020|1770457554",
            timestamps={
                "psap_answer": "2026-02-20T02:29:00",
                "incident_clear": "2026-02-20T03:14:00",
            },
        )
        diff = _build_neris_diff(doc, neris_record)
        assert "timestamps" not in diff

    def test_unit_level_no_false_diff(self, neris_record):
        """Local unit timestamps in Pacific should NOT produce a diff when they match NERIS UTC."""
        # NERIS E31 dispatch=10:30Z, enroute=10:32Z, on_scene=10:40Z, clear=11:10Z
        # PST (UTC-8): 02:30, 02:32, 02:40, 03:10
        doc = IncidentDocument(
            id="tz-unit-test",
            incident_number="26-002358",
            incident_datetime=datetime(2026, 2, 20, tzinfo=UTC),
            created_by="chief@sjifire.org",
            neris_incident_id="FD53055879|26SJ0020|1770457554",
            units=[
                UnitAssignment(
                    unit_id="E31",
                    dispatch="2026-02-20T02:30:00",
                    enroute="2026-02-20T02:32:00",
                    on_scene="2026-02-20T02:40:00",
                    cleared="2026-02-20T03:10:00",
                ),
            ],
        )
        diff = _build_neris_diff(doc, neris_record)
        assert "units" not in diff

    def test_unit_not_in_neris_still_detected(self, neris_record):
        """Units that exist locally but not in NERIS should still appear in the diff."""
        doc = IncidentDocument(
            id="extra-unit-test",
            incident_number="26-002358",
            incident_datetime=datetime(2026, 2, 20, tzinfo=UTC),
            created_by="chief@sjifire.org",
            neris_incident_id="FD53055879|26SJ0020|1770457554",
            units=[
                UnitAssignment(
                    unit_id="E33",
                    staged="2026-02-20T10:43:54Z",
                    on_scene="2026-02-20T10:50:00Z",
                ),
            ],
        )
        diff = _build_neris_diff(doc, neris_record)
        assert "units" in diff
        assert "E33.staged" in diff["units"]["local"]
        assert "E33.on_scene" in diff["units"]["local"]


class TestBuildNerisPatch:
    """Tests for _build_neris_patch."""

    def test_narrative_patch(self):
        diff = {"narrative": {"local": "Updated text", "neris": "Old text"}}
        patch = _build_neris_patch(diff)
        assert patch["base"]["outcome_narrative"] == {"action": "set", "value": "Updated text"}

    def test_timestamp_patch(self):
        diff = {
            "timestamps": {
                "local": {"psap_answer": "2026-02-20T10:30:00Z"},
                "neris": {"call_create": "2026-02-20T10:29:00Z"},
            }
        }
        patch = _build_neris_patch(diff)
        assert patch["dispatch"]["call_create"] == {
            "action": "set",
            "value": "2026-02-20T10:30:00Z",
        }

    def test_address_patch(self):
        diff = {"address": {"local": "94 Zepher Ln", "neris": "94 Zepher Lane"}}
        patch = _build_neris_patch(diff)
        assert patch["base"]["location"]["street_address"] == {
            "action": "set",
            "value": "94 Zepher Ln",
        }

    def test_unit_timestamps_patch(self):
        diff = {
            "units": {
                "local": {"E31.dispatch": "2026-02-20T10:31:00Z"},
                "neris": {"E31.dispatch": "2026-02-20T10:30:00Z"},
            }
        }
        patch = _build_neris_patch(diff)
        assert "dispatch" in patch
        assert "unit_responses" in patch["dispatch"]

    def test_first_unit_dispatched_patch(self):
        diff = {
            "timestamps": {
                "local": {"first_unit_dispatched": "2026-02-20T10:31:00Z"},
                "neris": {"first_unit_dispatched": "2026-02-20T10:30:00Z"},
            }
        }
        result = _build_neris_patch(diff)
        assert result["dispatch"]["first_unit_dispatched"] == {
            "action": "set",
            "value": "2026-02-20T10:31:00Z",
        }

    def test_automatic_alarm_patch(self):
        diff = {"automatic_alarm": {"local": True, "neris": False}}
        result = _build_neris_patch(diff)
        assert result["dispatch"]["automatic_alarm"] == {
            "action": "set",
            "value": True,
        }

    def test_unit_staged_patch(self):
        diff = {
            "units": {
                "local": {"E31.staged": "2026-02-20T10:35:00Z"},
                "neris": {"E31.staged": "2026-02-20T10:34:00Z"},
            }
        }
        result = _build_neris_patch(diff)
        unit_responses = result["dispatch"]["unit_responses"]
        assert unit_responses["action"] == "set"
        assert unit_responses["value"]["E31"]["staging"] == {
            "action": "set",
            "value": "2026-02-20T10:35:00Z",
        }

    def test_empty_diff_returns_empty_patch(self):
        patch = _build_neris_patch({})
        assert patch == {}

    def test_incident_type_patch(self):
        diff = {"incident_type": {"local": "FIRE||STRUCTURE_FIRE", "neris": "FIRE||CHIMNEY_FIRE"}}
        patch = _build_neris_patch(diff)
        assert patch["incident_types"] == {
            "action": "set",
            "value": [{"type": "FIRE||STRUCTURE_FIRE"}],
        }


class TestUpdateNerisIncident:
    """Integration tests for update_neris_incident tool."""

    @patch("sjifire.ops.incidents.tools._patch_neris_incident")
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    @patch(
        "sjifire.ops.neris.store.get_cosmos_container", new_callable=AsyncMock, return_value=None
    )
    async def test_happy_path(
        self,
        mock_cosmos,
        mock_store_cls,
        mock_get_neris,
        mock_patch_neris,
        officer_user,
        sample_doc,
        neris_record,
    ):
        """Successful update: local differs from NERIS, patch applied."""
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_get_neris.return_value = neris_record
        mock_patch_neris.return_value = {"status": "ok"}

        result = await update_neris_incident("doc-neris-1")

        assert result["status"] == "updated"
        assert "snapshot_id" in result
        assert len(result["fields_updated"]) > 0
        mock_patch_neris.assert_called_once()

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_dry_run_returns_diff_without_patching(
        self,
        mock_store_cls,
        mock_get_neris,
        officer_user,
        sample_doc,
        neris_record,
    ):
        """dry_run=True returns diff, no snapshot created, no patch called."""
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_get_neris.return_value = neris_record

        with patch("sjifire.ops.incidents.tools._patch_neris_incident") as mock_patch:
            result = await update_neris_incident("doc-neris-1", dry_run=True)

        assert result["status"] == "dry_run"
        assert "diff" in result
        assert len(result["fields_available"]) > 0
        assert result["neris_id"] == sample_doc.neris_incident_id
        mock_patch.assert_not_called()

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    @patch(
        "sjifire.ops.neris.store.get_cosmos_container", new_callable=AsyncMock, return_value=None
    )
    async def test_no_changes(self, mock_cosmos, mock_store_cls, mock_get_neris, officer_user):
        """When local matches NERIS, return no_changes."""
        doc = IncidentDocument(
            id="doc-match",
            incident_number="26-002358",
            incident_datetime=datetime(2026, 2, 20, tzinfo=UTC),
            created_by="chief@sjifire.org",
            neris_incident_id="FD53055879|26SJ0020|1770457554",
            narrative="NERIS original narrative",
            city="Friday Harbor",
            state="WA",
            zip_code="98250",
            timestamps={
                "psap_answer": "2026-02-20T10:29:00Z",
                "incident_clear": "2026-02-20T11:14:00Z",
            },
        )

        neris_record = {
            "incident_status": {"status": "SUBMITTED"},
            "base": {
                "outcome_narrative": "NERIS original narrative",
                "location": {
                    "incorporated_municipality": "Friday Harbor",
                    "state": "WA",
                    "postal_code": "98250",
                },
            },
            "dispatch": {
                "call_create": "2026-02-20T10:29:00Z",
                "incident_clear": "2026-02-20T11:14:00Z",
                "unit_responses": [],
            },
            "incident_types": [],
        }

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_get_neris.return_value = neris_record

        result = await update_neris_incident("doc-match")
        assert result["status"] == "no_changes"

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_rejects_non_editor(self, mock_store_cls, regular_user):
        """Non-editors should be rejected."""
        result = await update_neris_incident("doc-neris-1")
        assert "error" in result
        assert "not authorized" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_rejects_no_neris_id(self, mock_store_cls, officer_user):
        """Incidents without neris_incident_id should be rejected."""
        doc = IncidentDocument(
            id="doc-no-neris",
            incident_number="26-002358",
            incident_datetime=datetime(2026, 2, 20, tzinfo=UTC),
            created_by="chief@sjifire.org",
            neris_incident_id=None,
        )

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_neris_incident("doc-no-neris")
        assert "error" in result
        assert "no linked NERIS record" in result["error"]

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_rejects_approved_neris(
        self, mock_store_cls, mock_get_neris, officer_user, sample_doc
    ):
        """APPROVED NERIS records should be rejected."""
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_get_neris.return_value = {
            "incident_status": {"status": "APPROVED"},
            "base": {},
            "dispatch": {"unit_responses": []},
            "incident_types": [],
        }

        result = await update_neris_incident("doc-neris-1")
        assert "error" in result
        assert "APPROVED" in result["error"]

    @patch("sjifire.ops.incidents.tools._patch_neris_incident")
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    @patch(
        "sjifire.ops.neris.store.get_cosmos_container", new_callable=AsyncMock, return_value=None
    )
    async def test_fields_filter(
        self,
        mock_cosmos,
        mock_store_cls,
        mock_get_neris,
        mock_patch_neris,
        officer_user,
        sample_doc,
        neris_record,
    ):
        """When fields are specified, only those fields should be updated."""
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_get_neris.return_value = neris_record
        mock_patch_neris.return_value = {"status": "ok"}

        result = await update_neris_incident("doc-neris-1", fields=["narrative"])

        assert result["status"] == "updated"
        assert result["fields_updated"] == ["narrative"]

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_incident_not_found(self, mock_store_cls, officer_user):
        """Non-existent incident should return error."""
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=None)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_neris_incident("nonexistent-id")
        assert "error" in result
        assert "not found" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_neris_fetch_failure(
        self, mock_store_cls, mock_get_neris, officer_user, sample_doc
    ):
        """NERIS fetch failure should return error."""
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_get_neris.side_effect = Exception("API error")

        result = await update_neris_incident("doc-neris-1")
        assert "error" in result
        assert "Failed to fetch" in result["error"]

    @patch("sjifire.ops.incidents.tools._patch_neris_incident")
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    @patch(
        "sjifire.ops.neris.store.get_cosmos_container", new_callable=AsyncMock, return_value=None
    )
    async def test_patch_failure_returns_snapshot_id(
        self,
        mock_cosmos,
        mock_store_cls,
        mock_get_neris,
        mock_patch_neris,
        officer_user,
        sample_doc,
        neris_record,
    ):
        """If patch fails, error should include the snapshot ID."""
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_get_neris.return_value = neris_record
        mock_patch_neris.side_effect = Exception("NERIS patch failed")

        result = await update_neris_incident("doc-neris-1")
        assert "error" in result
        assert "snapshot_id" in result


class TestNerisSnapshotStore:
    """Tests for the NerisSnapshotStore (in-memory mode)."""

    async def test_create_and_get(self):
        from sjifire.ops.neris.models import NerisSnapshotDocument
        from sjifire.ops.neris.store import NerisSnapshotStore

        # Clear in-memory state
        NerisSnapshotStore._memory.clear()

        doc = NerisSnapshotDocument(
            year="2026",
            neris_id="FD53055879|26SJ0020|1770457554",
            incident_id="doc-1",
            incident_number="26-002358",
            snapshot={"base": {"outcome_narrative": "Test"}},
            patches_applied={"base": {"outcome_narrative": {"action": "set", "value": "New"}}},
            patched_by="chief@sjifire.org",
        )

        async with NerisSnapshotStore() as store:
            created = await store.create(doc)
            assert created.id == doc.id

            fetched = await store.get_by_id(doc.id, "2026")
            assert fetched is not None
            assert fetched.neris_id == doc.neris_id

    async def test_list_by_neris_id(self):
        from sjifire.ops.neris.models import NerisSnapshotDocument
        from sjifire.ops.neris.store import NerisSnapshotStore

        NerisSnapshotStore._memory.clear()

        target_neris_id = "FD53055879|26SJ0020|1770457554"

        for i in range(3):
            doc = NerisSnapshotDocument(
                year="2026",
                neris_id=target_neris_id,
                incident_id=f"doc-{i}",
                snapshot={},
                patches_applied={},
                patched_by="chief@sjifire.org",
            )
            async with NerisSnapshotStore() as store:
                await store.create(doc)

        # Add one with different neris_id
        other = NerisSnapshotDocument(
            year="2026",
            neris_id="FD53055879|OTHER|999",
            incident_id="doc-other",
            snapshot={},
            patches_applied={},
            patched_by="chief@sjifire.org",
        )
        async with NerisSnapshotStore() as store:
            await store.create(other)

        async with NerisSnapshotStore() as store:
            results = await store.list_by_neris_id(target_neris_id)
            assert len(results) == 3

    async def test_get_nonexistent(self):
        from sjifire.ops.neris.store import NerisSnapshotStore

        NerisSnapshotStore._memory.clear()

        async with NerisSnapshotStore() as store:
            result = await store.get_by_id("nonexistent", "2026")
            assert result is None
