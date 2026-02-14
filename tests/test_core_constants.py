"""Tests for organization config values (loaded from organization.json)."""

from sjifire.core.config import get_org_config


class TestRankHierarchy:
    """Tests for rank_hierarchy config."""

    def test_is_tuple(self):
        assert isinstance(get_org_config().rank_hierarchy, tuple)

    def test_not_empty(self):
        assert len(get_org_config().rank_hierarchy) > 0

    def test_contains_chief(self):
        assert "Chief" in get_org_config().rank_hierarchy

    def test_contains_captain(self):
        assert "Captain" in get_org_config().rank_hierarchy

    def test_contains_lieutenant(self):
        assert "Lieutenant" in get_org_config().rank_hierarchy

    def test_chief_ranks_first(self):
        rh = get_org_config().rank_hierarchy
        chief_idx = rh.index("Chief")
        captain_idx = rh.index("Captain")
        lieutenant_idx = rh.index("Lieutenant")
        assert chief_idx < captain_idx < lieutenant_idx

    def test_all_strings(self):
        for rank in get_org_config().rank_hierarchy:
            assert isinstance(rank, str)
            assert len(rank) > 0


class TestMarinePositions:
    """Tests for marine_positions config."""

    def test_is_frozenset(self):
        assert isinstance(get_org_config().marine_positions, frozenset)

    def test_not_empty(self):
        assert len(get_org_config().marine_positions) > 0

    def test_contains_deckhand(self):
        assert "Marine: Deckhand" in get_org_config().marine_positions

    def test_contains_mate(self):
        assert "Marine: Mate" in get_org_config().marine_positions

    def test_contains_pilot(self):
        assert "Marine: Pilot" in get_org_config().marine_positions

    def test_all_marine_prefixed(self):
        for position in get_org_config().marine_positions:
            assert position.startswith("Marine:"), f"'{position}' should start with 'Marine:'"

    def test_all_strings(self):
        for position in get_org_config().marine_positions:
            assert isinstance(position, str)
            assert len(position) > 0


class TestOperationalPositions:
    """Tests for operational_positions config."""

    def test_is_frozenset(self):
        assert isinstance(get_org_config().operational_positions, frozenset)

    def test_not_empty(self):
        assert len(get_org_config().operational_positions) > 0

    def test_contains_firefighter(self):
        assert "Firefighter" in get_org_config().operational_positions

    def test_contains_apparatus_operator(self):
        assert "Apparatus Operator" in get_org_config().operational_positions

    def test_contains_support(self):
        assert "Support" in get_org_config().operational_positions

    def test_contains_wildland(self):
        assert "Wildland Firefighter" in get_org_config().operational_positions

    def test_includes_marine_positions(self):
        cfg = get_org_config()
        assert cfg.marine_positions.issubset(cfg.operational_positions)

    def test_all_strings(self):
        for position in get_org_config().operational_positions:
            assert isinstance(position, str)
            assert len(position) > 0


class TestConfigConsistency:
    """Tests for consistency between config values."""

    def test_marine_subset_of_operational(self):
        cfg = get_org_config()
        for position in cfg.marine_positions:
            assert position in cfg.operational_positions, (
                f"'{position}' not in operational_positions"
            )

    def test_no_rank_in_positions(self):
        cfg = get_org_config()
        for rank in cfg.rank_hierarchy:
            assert rank not in cfg.operational_positions, f"Rank '{rank}' in operational_positions"
            assert rank not in cfg.marine_positions, f"Rank '{rank}' in marine_positions"

    def test_officer_positions_not_empty(self):
        assert len(get_org_config().officer_positions) > 0

    def test_chief_unit_prefixes_not_empty(self):
        assert len(get_org_config().chief_unit_prefixes) > 0

    def test_cosmos_database_set(self):
        assert get_org_config().cosmos_database != ""

    def test_neris_entity_id_set(self):
        assert get_org_config().neris_entity_id != ""

    def test_default_city_set(self):
        assert get_org_config().default_city != ""

    def test_default_state_set(self):
        assert len(get_org_config().default_state) == 2

    def test_officer_group_name_set(self):
        assert get_org_config().officer_group_name != ""

    def test_duty_event_subject_set(self):
        assert get_org_config().duty_event_subject != ""

    def test_calendar_category_set(self):
        assert get_org_config().calendar_category != ""
