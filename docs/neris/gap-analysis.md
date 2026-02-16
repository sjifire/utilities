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

## Completeness by call type (estimated)

| Call Type | Current Coverage | Biggest Gaps |
|-----------|-----------------|--------------|
| Simple cancel/NOACTION | ~85% | Risk reduction defaults, staffing count |
| Medical | ~70% | Casualties/rescues, patient disposition |
| Structure fire | ~55% | Risk reduction, exposures, fire timestamps, staffing |
| Hazmat | ~50% | Hazard details, emerging hazards |

## Key architectural notes

- Our `timestamps` dict and `unit_responses` list-of-dicts are **flexible enough** to hold staffing, response mode, and fire-specific times without model changes.
- The **real structural gaps** are: risk reduction, casualties/rescues, exposures, location booleans, impediment narrative.
- Instructions Step 8 ("Conditional Sections") **mentions** asking about fire/medical/hazmat specifics but there are no model fields or `update_incident` parameters to store most of them.

## Resolution tracking

| Gap | Priority | Resolution | Status |
|-----|----------|------------|--------|
| Risk reduction (alarms/sprinklers) | High | TBD | Open |
| Staffing count per unit | High | TBD | Open |
| Impediment narrative | Medium | TBD | Open |
| Location booleans (people_present, displaced, in_use) | Medium | TBD | Open |
| Casualties/rescues | Medium | TBD | Open |
| Exposures | Medium | TBD | Open |
| Fire-specific timestamps prompting | Medium | TBD | Open |
| Response mode per unit | Low | TBD | Open |
| Additional incident types (max 2) | Low | TBD | Open |
| Automatic alarm boolean | Low | TBD | Open |
| Aid given/received | Low | TBD | Open |
| Powergen/CSST hazards | Low | TBD | Open |
| County, zip, apt/suite | Low | TBD | Open |
| Dispatch metadata | Low | TBD | Open |
