"""Microsoft Forms update functionality."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel, Field

from ..eso.models import Apparatus, Personnel
from ..utils.config import Settings, get_settings


class UpdatePayload(BaseModel):
    """Payload sent to Power Automate to update forms."""

    update_type: str = "config_sync"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str = "python_script"
    commit: Optional[str] = None

    apparatus: list[Apparatus] = Field(default_factory=list)
    personnel: list[Personnel] = Field(default_factory=list)

    apparatus_choices: list[str] = Field(default_factory=list)
    personnel_choices: list[str] = Field(default_factory=list)

    def model_post_init(self, __context) -> None:
        """Generate choice lists after initialization."""
        if self.apparatus and not self.apparatus_choices:
            self.apparatus_choices = [a.to_choice() for a in self.apparatus]
        if self.personnel and not self.personnel_choices:
            self.personnel_choices = [p.to_choice() for p in self.personnel]


class FormUpdater:
    """Updates Microsoft Forms via Power Automate."""

    def __init__(self, settings: Optional[Settings] = None):
        """Initialize with settings."""
        self.settings = settings or get_settings()

    def load_apparatus(self, path: Optional[Path] = None) -> list[Apparatus]:
        """Load apparatus from CSV file."""
        csv_path = path or self.settings.config_dir / "apparatus.csv"

        if not csv_path.exists():
            print(f"Warning: {csv_path} not found")
            return []

        apparatus = []
        with open(csv_path) as f:
            lines = f.readlines()[1:]  # Skip header

        for line in lines:
            line = line.strip()
            if not line:
                continue

            parts = line.split(",")
            if len(parts) >= 5:
                code = parts[0].strip()
                name = parts[1].strip().strip('"')
                type_ = parts[2].strip()
                station = parts[3].strip() or None
                active = parts[4].strip().lower() == "true"

                if active and code:
                    apparatus.append(
                        Apparatus(
                            code=code,
                            name=name,
                            type=type_,
                            station=station,
                            active=active,
                        )
                    )

        return apparatus

    def load_personnel(self, path: Optional[Path] = None) -> list[Personnel]:
        """Load personnel from JSON file."""
        json_path = path or self.settings.config_dir / "personnel.json"

        if not json_path.exists():
            print(f"Warning: {json_path} not found")
            return []

        with open(json_path) as f:
            data = json.load(f)

        personnel = []
        for p in data.get("personnel", []):
            personnel.append(
                Personnel(
                    eso_id=p["esoId"],
                    first_name=p["firstName"],
                    last_name=p["lastName"],
                    full_name=p["fullName"],
                )
            )

        return sorted(personnel, key=lambda x: x.last_name)

    def build_payload(
        self,
        apparatus: Optional[list[Apparatus]] = None,
        personnel: Optional[list[Personnel]] = None,
        commit: Optional[str] = None,
    ) -> UpdatePayload:
        """Build the update payload."""
        if apparatus is None:
            apparatus = self.load_apparatus()
        if personnel is None:
            personnel = self.load_personnel()

        return UpdatePayload(
            apparatus=apparatus,
            personnel=personnel,
            commit=commit,
        )

    async def send_update(
        self,
        payload: Optional[UpdatePayload] = None,
        dry_run: bool = False,
    ) -> bool:
        """Send update to Power Automate."""
        if payload is None:
            payload = self.build_payload()

        if not self.settings.has_power_automate:
            print("Error: POWER_AUTOMATE_URL not configured")
            return False

        # Convert to JSON with camelCase keys for Power Automate
        payload_dict = {
            "updateType": payload.update_type,
            "timestamp": payload.timestamp.isoformat(),
            "source": payload.source,
            "commit": payload.commit,
            "apparatus": [a.model_dump() for a in payload.apparatus],
            "personnel": [
                {
                    "esoId": p.eso_id,
                    "firstName": p.first_name,
                    "lastName": p.last_name,
                    "fullName": p.full_name,
                }
                for p in payload.personnel
            ],
            "apparatusChoices": payload.apparatus_choices,
            "personnelChoices": payload.personnel_choices,
        }

        if dry_run:
            print("\n=== Dry Run Mode ===")
            print("Would send to Power Automate:")
            print(json.dumps(payload_dict, indent=2, default=str))
            return True

        print("\nSending to Power Automate...")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.settings.power_automate_url,
                json=payload_dict,
                timeout=30.0,
            )

        if response.is_success:
            print(f"Success! Status: {response.status_code}")
            return True
        else:
            print(f"Failed! Status: {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False

    def print_summary(self, payload: UpdatePayload) -> None:
        """Print a summary of the payload."""
        print("\n=== Update Payload Summary ===")
        print(f"Timestamp: {payload.timestamp.isoformat()}")
        print(f"Apparatus: {len(payload.apparatus)} items")
        print(f"Personnel: {len(payload.personnel)} items")

        print("\n--- Apparatus Choices ---")
        for choice in payload.apparatus_choices:
            print(f"  - {choice}")

        print("\n--- Personnel Choices ---")
        for choice in payload.personnel_choices:
            print(f"  - {choice}")
