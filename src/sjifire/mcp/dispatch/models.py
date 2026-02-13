"""Pydantic models for dispatch call documents stored in Cosmos DB."""

from dataclasses import asdict
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from sjifire.ispyfire.models import DispatchCall


class DispatchCallDocument(BaseModel):
    """Dispatch call stored in Cosmos DB.

    Mirrors the iSpyFire ``DispatchCall`` dataclass with additional
    fields for Cosmos DB storage: ``year`` (partition key), ``stored_at``,
    and embedded ``call_log``.

    Once a call is completed (``is_completed=True``), it's immutable in
    iSpyFire and safe to cache permanently.
    """

    id: str  # iSpyFire UUID (_id)
    year: str  # Partition key, e.g. "2026"
    long_term_call_id: str  # Dispatch ID, e.g. "26-001678"
    nature: str
    address: str
    agency_code: str
    type: str = ""
    zone_code: str = ""
    time_reported: str = ""
    is_completed: bool = False
    comments: str = ""
    joined_responders: str = ""
    responder_details: list[dict] = []
    ispy_responders: dict = {}
    city: str = ""
    state: str = ""
    zip_code: str = ""
    geo_location: str = ""
    created_timestamp: int | None = None
    call_log: list[dict] = []
    stored_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_dispatch_call(
        cls,
        call: DispatchCall,
        call_log: list[dict] | None = None,
    ) -> DispatchCallDocument:
        """Convert from an iSpyFire DispatchCall dataclass.

        Extracts the year from ``time_reported`` (e.g., "2026-02-12 14:30:00"
        → "2026"). Falls back to deriving from the dispatch ID prefix
        (e.g., "26" → "2026") if time_reported is empty.

        Args:
            call: DispatchCall dataclass from iSpyFire
            call_log: Optional audit log entries to embed
        """
        d = asdict(call)
        # Convert nested UnitResponse dataclasses to dicts (asdict handles this)
        year = _extract_year(call.time_reported, call.long_term_call_id)
        return cls(
            id=call.id,
            year=year,
            long_term_call_id=call.long_term_call_id,
            nature=call.nature,
            address=call.address,
            agency_code=call.agency_code,
            type=call.type,
            zone_code=call.zone_code,
            time_reported=call.time_reported,
            is_completed=call.is_completed,
            comments=call.comments,
            joined_responders=call.joined_responders,
            responder_details=d["responder_details"],
            ispy_responders=d["ispy_responders"],
            city=call.city,
            state=call.state,
            zip_code=call.zip_code,
            geo_location=call.geo_location,
            created_timestamp=call.created_timestamp,
            call_log=call_log or [],
        )

    def to_cosmos(self) -> dict:
        """Serialize for Cosmos DB storage."""
        return self.model_dump(mode="json")

    @classmethod
    def from_cosmos(cls, data: dict) -> DispatchCallDocument:
        """Deserialize from Cosmos DB document."""
        return cls.model_validate(data)

    def to_dict(self) -> dict:
        """Convert to tool-output dict, stripping Cosmos-only fields.

        Returns the same shape as the original ``_call_to_dict()`` output
        so existing tool consumers see no change.
        """
        d = self.model_dump(mode="json")
        # Remove Cosmos storage fields not in the original DispatchCall
        for key in ("year", "stored_at", "call_log"):
            d.pop(key, None)
        return d


def _extract_year(time_reported: str, dispatch_id: str) -> str:
    """Extract four-digit year from time_reported or dispatch ID prefix.

    Args:
        time_reported: Timestamp string like "2026-02-12 14:30:00"
        dispatch_id: Dispatch ID like "26-001678"

    Returns:
        Four-digit year string, e.g. "2026"
    """
    # Try time_reported first (most reliable)
    if time_reported and len(time_reported) >= 4:
        candidate = time_reported[:4]
        if candidate.isdigit():
            return candidate

    # Fall back to dispatch ID prefix: "26" → "2026"
    if dispatch_id and "-" in dispatch_id:
        prefix = dispatch_id.split("-")[0]
        if len(prefix) == 2 and prefix.isdigit():
            return f"20{prefix}"

    return str(datetime.now(UTC).year)


def year_from_dispatch_id(dispatch_id: str) -> str | None:
    """Derive the partition key year from a dispatch ID prefix.

    Args:
        dispatch_id: e.g. "26-001678"

    Returns:
        Four-digit year like "2026", or None if not derivable.
    """
    if dispatch_id and "-" in dispatch_id:
        prefix = dispatch_id.split("-")[0]
        if len(prefix) == 2 and prefix.isdigit():
            return f"20{prefix}"
    return None
