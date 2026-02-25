"""Lightweight Pydantic input models for NERIS API responses.

These models mirror the NERIS incident response structure for the fields
we extract during import.  All fields are optional and ``extra="allow"``
so that:
- Known fields are validated (typos in our code fail loud)
- Unknown fields land in ``model_extra`` for downstream capture
- We don't couple to the upstream ``neris-api-client`` strict schema
"""

from pydantic import BaseModel, ConfigDict


class NerisLocation(BaseModel):
    model_config = ConfigDict(extra="allow")
    complete_number: str | None = None
    number: str | None = None
    street_prefix_direction: str | None = None
    street: str | None = None
    street_postfix: str | None = None
    incorporated_municipality: str | None = None
    state: str | None = None
    postal_code: str | None = None
    county: str | None = None


class NerisLocationUse(BaseModel):
    model_config = ConfigDict(extra="allow")
    use_type: str | None = None


class NerisBase(BaseModel):
    model_config = ConfigDict(extra="allow")
    outcome_narrative: str | None = None
    people_present: bool | None = None
    displacement_count: int | None = None
    displacement_causes: list[str] | None = None
    animals_rescued: int | None = None
    impediment_narrative: str | None = None
    location: NerisLocation | None = None
    location_use: NerisLocationUse | None = None


class NerisFireLocationDetail(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str | None = None
    arrival_condition: str | None = None
    damage_type: str | None = None
    room_of_origin_type: str | None = None
    floor_of_origin: int | None = None
    cause: str | None = None
    progression_evident: bool | None = None
    acres_burned: float | None = None


class NerisFireDetail(BaseModel):
    model_config = ConfigDict(extra="allow")
    location_detail: NerisFireLocationDetail | None = None
    water_supply: str | None = None
    investigation_needed: str | None = None
    investigation_types: list[str] | None = None
    suppression_appliances: list[str] | None = None


class NerisAlarmOperation(BaseModel):
    model_config = ConfigDict(extra="allow")
    alerted_failed_other: dict | None = None


class NerisAlarmPresence(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str | None = None
    alarm_types: list[str] | None = None
    operation: NerisAlarmOperation | None = None


class NerisAlarm(BaseModel):
    model_config = ConfigDict(extra="allow")
    presence: NerisAlarmPresence | None = None


class NerisElectricHazard(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str | None = None


class NerisPowergenPvOther(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str | None = None


class NerisPowergenHazard(BaseModel):
    model_config = ConfigDict(extra="allow")
    pv_other: NerisPowergenPvOther | None = None


class NerisCsstHazard(BaseModel):
    model_config = ConfigDict(extra="allow")
    ignition_source: bool | None = None
    lightning_suspected: str | None = None
    grounded: bool | None = None


class NerisActionNoaction(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str | None = None
    noaction_type: str | None = None
    actions: list[str] | None = None


class NerisActionsTactics(BaseModel):
    model_config = ConfigDict(extra="allow")
    action_noaction: NerisActionNoaction | None = None


class NerisUnitResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    unit_neris_id: str | None = None
    reported_unit_id: str | None = None
    staffing: int | None = None
    response_mode: str | None = None
    dispatch: str | None = None
    enroute_to_scene: str | None = None
    staging: str | None = None
    on_scene: str | None = None
    unit_clear: str | None = None
    canceled_enroute: str | None = None


class NerisDispatch(BaseModel):
    model_config = ConfigDict(extra="allow")
    incident_number: str | None = None
    determinant_code: str | None = None
    dispatch_incident_number: str | None = None
    call_create: str | None = None
    incident_clear: str | None = None
    automatic_alarm: bool | None = None
    location: NerisLocation | None = None
    unit_responses: list[NerisUnitResponse] | None = None


class NerisIncidentType(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str | None = None
    primary: bool | None = None


class NerisMedicalDetail(BaseModel):
    model_config = ConfigDict(extra="allow")
    patient_care_evaluation: str | None = None
    transport_disposition: str | None = None
    patient_status: str | None = None


class NerisTacticTimestamps(BaseModel):
    model_config = ConfigDict(extra="allow")
    command_established: str | None = None
    completed_sizeup: str | None = None
    water_on_fire: str | None = None
    fire_under_control: str | None = None
    fire_knocked_down: str | None = None
    suppression_complete: str | None = None
    primary_search_begin: str | None = None
    primary_search_complete: str | None = None
    extrication_complete: str | None = None


class NerisIncidentStatus(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: str | None = None


class NerisNonfdAid(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str | None = None


class NerisRecord(BaseModel):
    """Top-level NERIS incident response — lightweight input model."""

    model_config = ConfigDict(extra="allow")
    neris_id: str | None = None
    base: NerisBase | None = None
    incident_types: list[NerisIncidentType] | None = None
    incident_status: NerisIncidentStatus | None = None
    actions_tactics: NerisActionsTactics | None = None
    dispatch: NerisDispatch | None = None
    tactic_timestamps: NerisTacticTimestamps | None = None
    fire_detail: NerisFireDetail | None = None
    smoke_alarm: NerisAlarm | None = None
    fire_alarm: NerisAlarm | None = None
    fire_suppression: NerisAlarm | None = None
    electric_hazards: list[NerisElectricHazard] | None = None
    powergen_hazards: list[NerisPowergenHazard] | None = None
    csst_hazard: NerisCsstHazard | None = None
    medical_details: list[NerisMedicalDetail] | None = None
    casualty_rescues: list[dict] | None = None  # complex nested, keep as dict
    nonfd_aids: list[NerisNonfdAid] | None = None
