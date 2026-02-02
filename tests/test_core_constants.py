"""Tests for core/constants.py - organizational constants."""

from sjifire.core.constants import (
    MARINE_POSITIONS,
    OPERATIONAL_POSITIONS,
    RANK_HIERARCHY,
)


class TestRankHierarchy:
    """Tests for RANK_HIERARCHY constant."""

    def test_is_list(self):
        """RANK_HIERARCHY should be a list."""
        assert isinstance(RANK_HIERARCHY, list)

    def test_not_empty(self):
        """RANK_HIERARCHY should not be empty."""
        assert len(RANK_HIERARCHY) > 0

    def test_contains_chief(self):
        """RANK_HIERARCHY should contain Chief."""
        assert "Chief" in RANK_HIERARCHY

    def test_contains_captain(self):
        """RANK_HIERARCHY should contain Captain."""
        assert "Captain" in RANK_HIERARCHY

    def test_contains_lieutenant(self):
        """RANK_HIERARCHY should contain Lieutenant."""
        assert "Lieutenant" in RANK_HIERARCHY

    def test_chief_ranks_first(self):
        """Chief ranks should come before Captain/Lieutenant."""
        chief_idx = RANK_HIERARCHY.index("Chief")
        captain_idx = RANK_HIERARCHY.index("Captain")
        lieutenant_idx = RANK_HIERARCHY.index("Lieutenant")
        assert chief_idx < captain_idx < lieutenant_idx

    def test_all_strings(self):
        """All entries should be strings."""
        for rank in RANK_HIERARCHY:
            assert isinstance(rank, str)
            assert len(rank) > 0


class TestMarinePositions:
    """Tests for MARINE_POSITIONS constant."""

    def test_is_set(self):
        """MARINE_POSITIONS should be a set."""
        assert isinstance(MARINE_POSITIONS, set)

    def test_not_empty(self):
        """MARINE_POSITIONS should not be empty."""
        assert len(MARINE_POSITIONS) > 0

    def test_contains_deckhand(self):
        """MARINE_POSITIONS should contain Marine: Deckhand."""
        assert "Marine: Deckhand" in MARINE_POSITIONS

    def test_contains_mate(self):
        """MARINE_POSITIONS should contain Marine: Mate."""
        assert "Marine: Mate" in MARINE_POSITIONS

    def test_contains_pilot(self):
        """MARINE_POSITIONS should contain Marine: Pilot."""
        assert "Marine: Pilot" in MARINE_POSITIONS

    def test_all_marine_prefixed(self):
        """All marine positions should start with 'Marine:'."""
        for position in MARINE_POSITIONS:
            assert position.startswith("Marine:"), f"'{position}' should start with 'Marine:'"

    def test_all_strings(self):
        """All entries should be non-empty strings."""
        for position in MARINE_POSITIONS:
            assert isinstance(position, str)
            assert len(position) > 0


class TestOperationalPositions:
    """Tests for OPERATIONAL_POSITIONS constant."""

    def test_is_set(self):
        """OPERATIONAL_POSITIONS should be a set."""
        assert isinstance(OPERATIONAL_POSITIONS, set)

    def test_not_empty(self):
        """OPERATIONAL_POSITIONS should not be empty."""
        assert len(OPERATIONAL_POSITIONS) > 0

    def test_contains_firefighter(self):
        """OPERATIONAL_POSITIONS should contain Firefighter."""
        assert "Firefighter" in OPERATIONAL_POSITIONS

    def test_contains_apparatus_operator(self):
        """OPERATIONAL_POSITIONS should contain Apparatus Operator."""
        assert "Apparatus Operator" in OPERATIONAL_POSITIONS

    def test_contains_support(self):
        """OPERATIONAL_POSITIONS should contain Support."""
        assert "Support" in OPERATIONAL_POSITIONS

    def test_contains_wildland(self):
        """OPERATIONAL_POSITIONS should contain Wildland Firefighter."""
        assert "Wildland Firefighter" in OPERATIONAL_POSITIONS

    def test_includes_marine_positions(self):
        """OPERATIONAL_POSITIONS should be a superset of MARINE_POSITIONS."""
        assert MARINE_POSITIONS.issubset(OPERATIONAL_POSITIONS)

    def test_all_strings(self):
        """All entries should be non-empty strings."""
        for position in OPERATIONAL_POSITIONS:
            assert isinstance(position, str)
            assert len(position) > 0


class TestConstantConsistency:
    """Tests for consistency between constants."""

    def test_marine_subset_of_operational(self):
        """All marine positions should be operational positions."""
        for position in MARINE_POSITIONS:
            assert position in OPERATIONAL_POSITIONS, f"'{position}' not in OPERATIONAL_POSITIONS"

    def test_no_rank_in_positions(self):
        """Ranks should not appear in position lists."""
        for rank in RANK_HIERARCHY:
            assert rank not in OPERATIONAL_POSITIONS, f"Rank '{rank}' in OPERATIONAL_POSITIONS"
            assert rank not in MARINE_POSITIONS, f"Rank '{rank}' in MARINE_POSITIONS"
