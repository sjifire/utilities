"""Organizational constants used across the codebase.

These constants are imported by other modules:
- OPERATIONAL_POSITIONS: Used in ispyfire/sync.py, exchange/group_sync.py,
  core/group_strategies.py, entra/group_sync.py
- MARINE_POSITIONS: Used in group sync strategies
- RANK_HIERARCHY: Used in aladtec/models.py
"""

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
