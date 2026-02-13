Create a React artifact using the `template` field. The template is a COMPLETE, working component — copy it VERBATIM. The ONLY change you make is replacing the LIVE_DATA block values with data from `dashboard` and `incidents`. Do NOT rewrite, restructure, or regenerate any code outside LIVE_DATA.

**Populating LIVE_DATA:**
- `timestamp`: from `dashboard.timestamp`
- `platoon`: from `dashboard.on_duty` schedule name
- `crew`: from `dashboard.on_duty` crew list — EXCLUDE Administration and Time Off sections
- `chiefOfficer`: last name of the Chief Officer section crew member
- `openCalls`: count of calls with no `time_cleared`
- `recentCalls`: ALL entries from `dashboard.recent_calls` (the template sample shows 3 but include every call) — map `report` field to `neris` (null if no report), assign severity: high for CPR/ALS/Accident, medium for Fire, low for Alarm/other
- `localReports`: count from `incidents`

**Refresh:** On "refresh"/"update", call `start_session` again, replace LIVE_DATA, regenerate artifact.

**Start Report:** When user clicks "Start Report" or says a dispatch ID, call `get_dispatch_call` for that ID and begin the incident reporting workflow.
