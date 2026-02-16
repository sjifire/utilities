# ESO Suite Field Analysis

Extracted from saved HTML pages of ESO incident record 26-000944
(boat fire at Jensen Shipyard, 1293 Turn Point Rd, 01/20/2026).
Source files in `/ESO/` folder (not checked in).

## ESO Sections (10 tabs in sidebar)

### 1. Core (21 fields)
**Top-level:**
- Incident onset date* (date), onset time* (time), incident number* (text)
- Primary incident type* (hierarchical tree-select) → NERIS `incident_types[0]`
- Additional incident types (tree-select multi, max 2)
- Special incident modifiers (tree-select multi)
- Actions taken (tree-select multi)
- No action taken reason (segmented: Cancelled / Staged / No incident found)

**Dispatch subsection:**
- Dispatch run number (text)
- Initial dispatch code (text)
- Was call automatic alarm? (Yes/No)

**Aid subsection:**
- Aid given/received? (Yes/No)
- Non FD aid type (tree-select multi)

**Agency assignment subsection (all internal-only, not in NERIS):**
- Battalion, Division, Station, Shift, District, Zone (all tree-select)
- Report writer, Quality control (tree-select, personnel list)

### 2. Narrative (2 fields)
Note: ESO HTML save was a duplicate of Location page. Based on NERIS schema:
- Impediment narrative (textarea, max 100k) → NERIS `base.impediment_narrative`
- Outcome narrative (textarea, max 100k) → NERIS `base.outcome_narrative`

### 3. Location (16 fields)
**Imported address (read-only from CAD):**
- Address: "1293 TURN POINT RD, JENSEN SHIPYARD; main dock"
- City: "FRIDAY HARBOR"

**Editable fields:**
- Address search/autocomplete
- Apt/unit/suite, City, State (pre-filled: Washington), Postal code (max 5),
  Postal code ext (max 4), County, Country
- Latitude (-90 to 90, step 0.000001), Longitude (-180 to 180)
- Cross streets (repeatable)

**Location type:**
- Location use (tree-select), Active use? (Yes/No), Secondary use impacted? (Yes/No)

**People:**
- People present? (Yes/No), Displaced count (number, min 0)

### 4. Incident Times (13 toggleable timestamps)
Dispatch call arrival (date+time), Dispatch call answering, Dispatch call creation* (required),
IC established, Sizeup complete, Primary search began, Primary search complete,
Water on fire, Fire under control, Fire knocked down, Suppression efforts complete,
Extrication complete, Incident clear

Each has On/Off toggle + date + time fields. Bulk "fill date and time" + "Apply to all".

### 5. Resources (per-unit + personnel)
**Per unit (4 units in this incident: BN31 + 3 others):**
- Unit name* (tree-select, required)
- Unable to dispatch (toggle)
- Response mode to scene (Emergent / Non-emergent)
- Per-unit timeline: Dispatch, Enroute, On scene, Cleared (each date+time)
- Personnel list per unit (name + ID)

**Personnel tab (3 in this incident):**
- Personnel not on unit: Dyer Robin|2319, Eisenhardt Eric|2108, Eades Tom|611
- BN31 had 0 personnel assigned

### 6. Emerging Hazards
- Power generation type (tree-select, conditionally required)
- Additional fields appear based on hazard type

### 7. Exposures (repeatable, 1 in this incident)
Per exposure:
- Exposure type* (External/Internal radio)
- Exposure damage* (tree-select)
- Full location set (address, city, state, lat/lng, etc.)
- Location use, Active use?, Secondary use impacted?
- People present?, Displaced count
- "Same as main incident location" button
- Mark complete checkbox

### 8. Risk Reduction (5 Yes/No/Unknown questions)
- Smoke alarm present?
- Fire alarm present?
- Other alarms present?
- Fire suppression systems present?
- Cooking fire suppression present?
Conditional sub-fields appear when Yes selected.

### 9. Rescues/Casualties (repeatable, tabs: Non-FF / FF)
**Global:** Animals rescued count (number)
**Per person:**
- Rescue type* (tree-select)
- Casualty type* (Uninjured / Injured nonfatal / Injured fatal)
- Demographics: Birth month/year, Gender (6 options), Race (tree-select)
- Mark complete checkbox

### 10. Attachments
- File upload interface (table with filename, description, date, type)
- Currently empty

## CAD Notes (visible on all pages)
```
1293 TURN POINT RD; JENSEN SHIPYARD; main dock boat is on fire, 30 ft power boat,
everybody off boat, fire may have been extinguished. smoke coming from downstairs,
no more flames. 08:43:52 01/20/2026 - Cassie M RP Diane Putnam smoke has dissipated,
seems under control. 378-2850 08:46:50 01/20/2026 - D Easley 911: Kyle Gropp
916 295 8370 Jensens Boatyard, boat with huge amount of smoke. Not involved
08:47:46 01/20/2026 - Cassie M 2 patients at top of dock
```

## Component Types Used
- `eso-text-input` (text), `eso-numeric-input` (number)
- `eso-date-control` (MM/DD/YYYY + calendar), `eso-time-control` (HH:MM:SS masked)
- `app-tree-select` / `p-treeselect` (hierarchical dropdown, values loaded dynamically)
- `eso-segmented-control` (Yes/No or Yes/No/Unknown radio)
- `p-inputswitch` (on/off toggle)
- `p-autocomplete` (address search)
- `p-inputnumber` (spinner with +/- buttons)

All tree-select dropdown options are loaded dynamically (not in static HTML).
NERIS value sets at github.com/ulfsri/neris-framework/core_schemas/value_sets/ are canonical.
