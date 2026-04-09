# NERIS Incident Report — Gap Analysis

Comparison of ESO Suite (10-tab UI), actual NERIS PDF output, and our current system.
Reference: NERIS PDF `FD53055879_1770485007_1770087182` (Warbass fire alarm, 02/02/2026).
ESO reference: `docs/neris/eso-field-analysis.md` (boat fire 26-000944).

Last updated: 2026-02-15

---

## Core Incident

| Field | ESO (10 tabs) | NERIS PDF | Our System | Gap? |
|-------|--------------|-----------|------------|------|
| Incident number | Incident number* (required) | 1770485007 | `incident_number` | No |
| Incident date/time | Onset date* + onset time* | Derived from timestamps | `incident_date` (date only) | No |
| Primary incident type | Hierarchical tree-select* (required) | `PUBSERV\|\|ALARMS_NONMED\|\|FIRE_ALARM` | `incident_type` | No |
| Additional incident types | Tree-select multi, max 2 | Not shown in this report | **Missing** | Yes |
| Special incident modifiers | Tree-select multi | Not shown | **Missing** | Yes |
| Actions taken codes | Tree-select multi | N/A (NOACTION) | `action_codes` | No |
| No action taken reason | Segmented: Cancelled/Staged/No incident found | CANCELLED | `noaction_reason` | No |
| Dispatch run number | Text field | N/A | We store `incident_number` as dispatch ID | No |
| Initial dispatch code | Text field | Determinant Code (empty) | **Missing** | Yes |
| Was call automatic alarm? | Yes/No | "Automatic Alarm" shown | **Missing** | Yes |
| Aid given/received | Yes/No + Non-FD aid type | N/A | **Missing** | Yes |
| Agency assignment | Battalion, Division, Station, Shift, District, Zone | Internal only | `station` only | Partial |
| Report writer / QC | Tree-select (personnel) | Internal only | `created_by` (auto) | No |

## Narrative

| Field | ESO | NERIS PDF | Our System | Gap? |
|-------|-----|-----------|------------|------|
| Outcome narrative | Textarea, max 100k | "False alarm; call canceled by keyholder..." | `narratives.outcome` | No |
| Impediment narrative | Textarea, max 100k | "None" | **Missing** | Yes |
| Actions taken narrative | N/A (ESO doesn't have this separately) | N/A | `narratives.actions_taken` (we added this) | No (bonus) |

## Location

| Field | ESO | NERIS PDF | Our System | Gap? |
|-------|-----|-----------|------------|------|
| Address | Auto-imported from CAD + editable | "Warbass" | `address` | No |
| City / State / Zip | Pre-filled, editable | Friday Harbor, WA 98250 | `city`, `state` (no zip) | Partial (no zip) |
| County | Editable | San Juan County | **Missing** | Yes |
| Latitude / Longitude | -90 to 90, step 0.000001 | Not in PDF output | `latitude`, `longitude` | No |
| Cross streets | Repeatable | N/A | Asked in instructions but **not stored on incident** | Yes |
| Apt/unit/suite | Text field | "APARTMENT N/A" | **Missing** | Yes |
| Location use | Tree-select | `RESIDENTIAL\|\|MULTI_FAMILY_LOWRISE_DWELLING` | `location_use` | No |
| In Use | Yes/No | True | **Missing** | Yes |
| Used as Intended | Yes/No | True | **Missing** | Yes |
| Secondary use impacted | Yes/No | Blank | **Missing** | Yes |
| Vacancy cause | Conditional | Blank | **Missing** | Yes |
| People present | Yes/No | True | **Missing** | Yes |
| Number displaced | Number, min 0 | 0 | **Missing** | Yes |

## Incident Times

| Field | ESO | NERIS PDF | Our System | Gap? |
|-------|-----|-----------|------------|------|
| Call arrival (PSAP) | Date+time, toggleable | 02/02/2026 18:51:21 | `timestamps["psap_answer"]` | No |
| Call answered | Date+time, toggleable | 02/02/2026 18:51:21 | **Missing** (only psap_answer) | Yes |
| Call created (dispatch)* | Required, date+time | 02/02/2026 18:53:02 | `timestamps["first_unit_dispatched"]` | No |
| Incident clear | Date+time, toggleable | 02/02/2026 18:58:21 | `timestamps["incident_clear"]` | No |
| IC established | Date+time, toggleable | N/A | Dict supports it, **not prompted** | Prompt gap |
| Sizeup complete | Date+time, toggleable | N/A | **Not prompted** | Prompt gap |
| Primary search began | Date+time, toggleable | N/A | **Not prompted** | Prompt gap |
| Primary search complete | Date+time, toggleable | N/A | **Not prompted** | Prompt gap |
| Water on fire | Date+time, toggleable | N/A | **Not prompted** | Prompt gap |
| Fire under control | Date+time, toggleable | N/A | **Not prompted** | Prompt gap |
| Fire knocked down | Date+time, toggleable | N/A | **Not prompted** | Prompt gap |
| Suppression efforts complete | Date+time, toggleable | N/A | **Not prompted** | Prompt gap |
| Extrication complete | Date+time, toggleable | N/A | **Not prompted** | Prompt gap |

Note: Our `timestamps` dict is flexible enough to hold all of these. The gap is in prompting, not storage.

## Resources / Units

| Field | ESO | NERIS PDF | Our System | Gap? |
|-------|-----|-----------|------------|------|
| Unit name | Tree-select* (required) | B31, E31 | In `unit_responses` dict | No |
| Staffing count | Implicit from personnel list | B31: 1, E31: 2 | **Not prompted** | Yes |
| Response mode | Emergent / Non-emergent | EMERGENT | **Not prompted** | Yes |
| Transport mode | N/A | Blank | **Not prompted** | Yes |
| Per-unit dispatch time | Date+time | 02/02/2026 18:53:02 | In `unit_responses` dict | No |
| Per-unit enroute time | Date+time | B31: 18:55:55 | In `unit_responses` dict | No |
| Per-unit on scene time | Date+time | Blank | In `unit_responses` dict | No |
| Per-unit cleared time | Date+time | Blank | In `unit_responses` dict | No |
| Personnel per unit | Name + ID list | Not in PDF | `crew` with `unit` field | No |
| Unable to dispatch | Toggle | N/A | **Missing** | Yes |

## Risk Reduction / Alarms (ENTIRE SECTION MISSING)

| Field | ESO | NERIS PDF | Our System | Gap? |
|-------|-----|-----------|------------|------|
| Smoke alarm present? | Yes/No/Unknown | NOT_APPLICABLE | **Missing** | Yes |
| Smoke alarm working? | Conditional | N/A | **Missing** | Yes |
| Smoke alarm types | Conditional | N/A | **Missing** | Yes |
| Fire alarm present? | Yes/No/Unknown | NOT_APPLICABLE | **Missing** | Yes |
| Other alarms present? | Yes/No/Unknown | NOT_APPLICABLE | **Missing** | Yes |
| Other alarm types | Conditional | N/A | **Missing** | Yes |
| Sprinkler system present? | Yes/No/Unknown | NOT_APPLICABLE | **Missing** | Yes |
| Sprinkler types | Conditional | N/A | **Missing** | Yes |
| Cooking suppression present? | Yes/No/Unknown | NOT_APPLICABLE | **Missing** | Yes |

## Powergen / Emerging Hazards

| Field | ESO | NERIS PDF | Our System | Gap? |
|-------|-----|-----------|------------|------|
| Power generation type | Tree-select | NOT_APPLICABLE | **Missing** | Yes |
| CSST Hazard - Ignition Source | Boolean | False | **Missing** | Yes |
| CSST Hazard - Lightning Suspected | Value | UNKNOWN | **Missing** | Yes |
| CSST Hazard - Grounded | Value | N/A | **Missing** | Yes |

## Exposures (ENTIRE SECTION MISSING)

| Field | ESO | NERIS PDF | Our System | Gap? |
|-------|-----|-----------|------------|------|
| Exposure type | External/Internal | Section empty | **Missing** | Yes |
| Exposure damage | Tree-select | N/A | **Missing** | Yes |
| Exposure location | Full address set | N/A | **Missing** | Yes |
| Location use / people present | Same as main location fields | N/A | **Missing** | Yes |

## Casualty & Rescues (ENTIRE SECTION MISSING)

| Field | ESO | NERIS PDF | Our System | Gap? |
|-------|-----|-----------|------------|------|
| Animals rescued count | Number | Section empty | **Missing** | Yes |
| Rescue type | Tree-select | N/A | **Missing** | Yes |
| Casualty type | Uninjured/Injured nonfatal/Injured fatal | N/A | **Missing** | Yes |
| Demographics | Birth month/year, Gender, Race | N/A | **Missing** | Yes |

## Dispatch Metadata

| Field | ESO | NERIS PDF | Our System | Gap? |
|-------|-----|-----------|------------|------|
| Center ID | N/A | Blank | **Missing** | Yes |
| Determinant Code | Text | Blank | **Missing** | Yes |
| Incident Code | Text | Blank | **Missing** | Yes |
| Disposition | N/A | Blank | **Missing** | Yes |
| Dispatch Comments | CAD notes sidebar | Blank | Not stored on incident | Yes |

---

## Architecture Decision: Strict Core + Flexible Extras

Rather than typing every NERIS field, the model uses:

- **First-class fields** — data that appears on every call, drives business logic, or needs querying
- **`extras: dict`** — everything else. Claude saves edge-case data with descriptive `snake_case` keys. Can be promoted to first-class later if needed.

Instruction to Claude: *"If you need to save information that doesn't fit a named field, add it to `extras` with a descriptive snake_case key and the value."*

For NERIS submission, `to_neris_payload()` reads from typed fields first, then pulls from extras to fill in conditional sections.

---

## Decisions by Section

### Core Incident

| # | Field | Decision | Detail |
|---|-------|----------|--------|
| 1 | Incident number | **Keep** | `incident_number` |
| 2 | Incident date/time | **Promote** | `incident_date: date` → `incident_datetime: datetime` |
| 3 | Primary incident type | **Keep** | `incident_type` |
| 4 | Additional incident types | **First-class** | `additional_incident_types: list[str]` (max 2) |
| 5 | Special incident modifiers | **Extras** | |
| 6 | Actions taken codes | **Keep** | `action_codes` |
| 7 | No action taken reason | **Keep** | `noaction_reason` |
| 8 | Dispatch run number | **Keep** | Same as incident_number |
| 9 | Initial dispatch code | **Skip** | |
| 10 | Automatic alarm? | **First-class** | `automatic_alarm: bool \| None` |
| 11 | Aid given/received | **Extras** | |
| 12 | Station + agency assignment | **Extras** | Remove `station` as required first-class field |
| 13 | Report writers | **First-class** | `contributed_by: list[str]` |

### Narrative

| # | Field | Decision | Detail |
|---|-------|----------|--------|
| 1 | Narrative | **First-class** | Single `narrative: str` — replaces `Narratives` class (outcome + actions_taken combined) |
| 2 | Impediment narrative | **Extras** | Claude extracts from narrative/CAD notes if needed |

### Location

| # | Field | Decision | Detail |
|---|-------|----------|--------|
| 1 | Address | **Keep** | `address` |
| 2 | City | **Keep** | `city` |
| 3 | State | **Keep** | `state` |
| 4 | Zip code | **First-class** | `zip_code: str` |
| 5 | County | **First-class** | `county: str` |
| 6 | Apt/unit/suite | **First-class** | `apt_suite: str \| None` |
| 7 | Latitude | **Keep** | `latitude` |
| 8 | Longitude | **Keep** | `longitude` |
| 9 | Cross streets | **Extras** | |
| 10 | Location use | **Keep** | `location_use` |
| 11 | In Use | **Extras** | |
| 12 | Used as Intended | **Extras** | |
| 13 | Secondary use impacted | **Extras** | |
| 14 | Vacancy cause | **Extras** | |
| 15 | People present | **First-class** | `people_present: bool \| None` |
| 16 | Number displaced | **First-class** | `displaced_count: int \| None` |

### Incident Times

| # | Field | Decision | Detail |
|---|-------|----------|--------|
| 1 | PSAP answer | **Keep in dict** | `timestamps["psap_answer"]` |
| 2 | Agency paged (SJF3/SJF2) | **Add to dict** | `timestamps["alarm_time"]` — look for "Paged" on SJF3/SJF2 |
| 3 | First unit dispatched | **Remove** | Redundant with alarm_time |
| 4 | First unit enroute | **Keep in dict** | `timestamps["first_unit_enroute"]` |
| 5 | First unit arrived | **Keep in dict** | `timestamps["first_unit_arrived"]` |
| 6 | IC established | **Add to dict** | `timestamps["ic_established"]` |
| 7 | Incident clear | **Keep in dict** | `timestamps["incident_clear"]` |
| 8-15 | Fire/rescue-specific times | **Same dict** | Standardized keys, prompted by Claude for fire incidents |

### Resources / Units

| # | Field | Decision | Detail |
|---|-------|----------|--------|
| 1 | Units + personnel | **Merge** | Single `units` list — each entry has unit_id, times, response_mode, personnel[] |
| 2 | Staffing count | **Auto-calculate** | `len(personnel)` at NERIS submission |
| 3 | Response mode | **Key in each unit** | `response_mode: EMERGENT \| NON_EMERGENT` |
| 4 | Transport mode | **Skip** | |
| 5 | POV responders | **Unit designator** | `unit_id: "POV"` with personnel nested |
| 6 | Unable to dispatch | **Extras** | |

### Fire Module (conditional: required for all FIRE incident types)

| # | Field | Decision | Detail |
|---|-------|----------|--------|
| 1 | Arrival conditions | **First-class** | `arrival_conditions: str \| None` — 6 NERIS values (NO_SMOKE_FIRE_SHOWING, SMOKE_SHOWING, SMOKE_FIRE_SHOWING, STRUCTURE_INVOLVED, FIRE_SPREAD_BEYOND_STRUCTURE, FIRE_OUT_UPON_ARRIVAL). Auto-extract from CAD notes/radio log when possible. |
| 2 | Water supply | **Extras** | Required for fire; 9 NERIS values |
| 3 | Investigation needed | **Extras** | Required for fire; 6 NERIS values |
| 4 | Investigation type | **Extras** | Conditional: if formal investigation |
| 5 | Structure: floor of origin | **Extras** | Conditional: STRUCTURE_FIRE only |
| 6 | Structure: room of origin | **Extras** | Conditional: STRUCTURE_FIRE only |
| 7 | Structure: fire cause | **Extras** | Conditional: STRUCTURE_FIRE only |
| 8 | Structure: damage type | **Extras** | Conditional: STRUCTURE_FIRE only |
| 9 | Structure: progression | **Extras** | Conditional: STRUCTURE_FIRE only |
| 10 | Outside: fire cause | **First-class** | `outside_fire_cause: str \| None` — 14 NERIS values. Conditional: OUTSIDE_FIRE only. |
| 11 | Outside: acres burned | **First-class** | `outside_fire_acres: float \| None` — Conditional: OUTSIDE_FIRE only. |

### Risk Reduction / Alarms

| # | Field | Decision | Detail |
|---|-------|----------|--------|
| 1-10 | All alarm/sprinkler fields | **Extras** | Prompted for STRUCTURE_FIRE incidents. Presence uses `bool \| None` (True=present, False=not present, None=unknown). |

### Powergen / Emerging Hazards

| # | Field | Decision | Detail |
|---|-------|----------|--------|
| 1-6 | All powergen/CSST fields | **Extras** | Hazards checklist for fire/gas/electrical/CO incidents |

### Exposures

| # | Field | Decision | Detail |
|---|-------|----------|--------|
| 1-4 | All exposure fields | **Extras** | Prompted for fire incidents: item (4 types), damage (4 levels), location |

### Casualty & Rescues

| # | Field | Decision | Detail |
|---|-------|----------|--------|
| 1-5 | All casualty/rescue/medical fields | **Extras** | Context-driven prompting hints based on incident type and CAD notes |

### Dispatch Metadata

| # | Field | Decision | Detail |
|---|-------|----------|--------|
| 1-4 | Center ID, Determinant Code, Incident Code, Disposition | **Skip** | |
| 5 | Dispatch comments / CAD notes | **First-class** | `dispatch_comments: str` — snapshot from completed dispatch record at creation time. If call is still open, warn user that data may be incomplete but don't block. |

---

## Prompting Improvements Needed

| Trigger | Claude Should Ask About |
|---------|----------------------|
| All fire incidents | Arrival conditions — auto-extract from CAD notes ("nothing showing", "smoke showing", etc.) and confirm with user |
| All fire incidents | Water supply, investigation needed |
| Structure fire incidents | Floor/room of origin, cause, damage type, progression |
| Outside fire incidents | Fire cause, acres burned |
| Fire, gas, electrical, CO incidents | Hazards checklist: alarms, sprinklers, solar/battery/generators, CSST |
| Structure fire incidents | Risk reduction: smoke alarms, fire alarms, sprinklers (Yes/No/Unknown) |
| Fire incidents with spread | Exposures: what was affected, damage level |
| Medical incidents / any patient | Patient count, care disposition, transport, status at handoff |
| Rescue incidents | Rescue actions, impediments, elevation |
| Firefighter injury | Activity when injured, cause, PPE |
| Civilian casualty at fire | Casualty type, demographics |
| All structure fire incidents | Fire-specific timestamps (water on fire, fire under control, etc.) |
| CAD notes mention access issues | Impediment narrative |

---

## New Model Summary (first-class fields only)

**Changed:**
- `incident_date: date` → `incident_datetime: datetime`
- `Narratives` class (outcome + actions_taken) → single `narrative: str`
- `unit_responses` + `crew` → merged `units: list` with nested personnel
- Remove `station` as required field → extras

**Added:**
- `additional_incident_types: list[str]`
- `automatic_alarm: bool | None`
- `arrival_conditions: str | None` — NERIS `fire_condition_arrival` value; auto-extracted from CAD notes when possible
- `outside_fire_cause: str | None` — NERIS `fire_cause_out` value (14 codes); for outdoor fire incidents
- `outside_fire_acres: float | None` — estimated acres burned; for outdoor fire incidents
- `contributed_by: list[str]`
- `zip_code: str`
- `county: str`
- `apt_suite: str | None`
- `people_present: bool | None`
- `displaced_count: int | None`
- `dispatch_comments: str`
- `extras: dict`

**Removed:**
- `station` (→ extras)
- `Narratives` class
- Separate `crew` list (→ nested under units)
