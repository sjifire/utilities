"""Organizational constants used across the codebase."""

__all__ = ["MARINE_POSITIONS", "OPERATIONAL_POSITIONS", "RANK_HIERARCHY"]

# Rank hierarchy for determining employee type (highest first)
RANK_HIERARCHY: list[str] = [
    "Chief",
    "Division Chief",
    "Battalion Chief",
    "Captain",
    "Lieutenant",
]

# Marine positions (boat crew)
MARINE_POSITIONS: set[str] = {
    "Marine: Deckhand",
    "Marine: Mate",
    "Marine: Pilot",
}

# Operational positions that indicate active response/support roles
# Used for filtering volunteers and other operational membership criteria
OPERATIONAL_POSITIONS: set[str] = {
    "Firefighter",
    "Apparatus Operator",
    "Support",
    "Wildland Firefighter",
} | MARINE_POSITIONS
