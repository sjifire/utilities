NERIS CODE REFERENCE — CATEGORY MAP

IMPORTANT: These are REAL codes from the NERIS system. NEVER invent
or extrapolate codes. If none of these fit, use get_neris_values to
search for the right code. When in doubt, look it up.

========================================
INCIDENT TYPES (value_set: "incident")
========================================

128 total codes. Use get_neris_values("incident", prefix="FIRE||") or
get_neris_values("incident", search="keyword") for targeted lookups.

FIRE (24 codes, 4 subcategories):
  STRUCTURE_FIRE:
  - FIRE||STRUCTURE_FIRE||CHIMNEY_FIRE  (includes woodstove/fireplace fires)
  - FIRE||STRUCTURE_FIRE||CONFINED_COOKING_APPLIANCE_FIRE
  - FIRE||STRUCTURE_FIRE||ROOM_AND_CONTENTS_FIRE
  - FIRE||STRUCTURE_FIRE||STRUCTURAL_INVOLVEMENT_FIRE
  OUTSIDE_FIRE:
  - FIRE||OUTSIDE_FIRE||VEGETATION_GRASS_FIRE
  - FIRE||OUTSIDE_FIRE||WILDFIRE_WILDLAND
  - FIRE||OUTSIDE_FIRE||WILDFIRE_URBAN_INTERFACE
  - FIRE||OUTSIDE_FIRE||DUMPSTER_OUTDOOR_CONTAINER_FIRE
  - FIRE||OUTSIDE_FIRE||TRASH_RUBBISH_FIRE
  - (+ 4 more: construction waste, outside tank, utility infrastructure, other)
  TRANSPORTATION_FIRE:
  - FIRE||TRANSPORTATION_FIRE||VEHICLE_FIRE_PASSENGER
  - FIRE||TRANSPORTATION_FIRE||VEHICLE_FIRE_COMMERCIAL
  - FIRE||TRANSPORTATION_FIRE||BOAT_PERSONAL_WATERCRAFT_BARGE_FIRE
  - (+ 5 more: aircraft, RV, food truck, train, mobility device)
  SPECIAL_FIRE:
  - FIRE||SPECIAL_FIRE||EXPLOSION
  - FIRE||SPECIAL_FIRE||INFRASTRUCTURE_FIRE
  - FIRE||SPECIAL_FIRE||ESS_FIRE

MEDICAL (46 codes, 3 subcategories):
  ILLNESS: cardiac_arrest, breathing_problems, chest_pain, stroke,
    altered_mental_status, seizures, diabetic, overdose, sick_case,
    unconscious, unknown_problem, well_person_check, + more
  INJURY: fall, motor_vehicle_collision, other_traumatic_injury,
    burns, choking, hemorrhage, assault, gunshot, stab, drowning, + more
  OTHER: medical_alarm, standby_request, transport, intercept, + more

  Common codes:
  - MEDICAL||ILLNESS||CARDIAC_ARREST
  - MEDICAL||ILLNESS||BREATHING_PROBLEMS
  - MEDICAL||ILLNESS||CHEST_PAIN_NON_TRAUMA
  - MEDICAL||ILLNESS||STROKE_CVA
  - MEDICAL||ILLNESS||OVERDOSE
  - MEDICAL||ILLNESS||UNCONSCIOUS_VICTIM
  - MEDICAL||ILLNESS||ALTERED_MENTAL_STATUS
  - MEDICAL||ILLNESS||HEART_PROBLEMS
  - MEDICAL||INJURY||FALL
  - MEDICAL||INJURY||OTHER_TRAUMATIC_INJURY
  - MEDICAL||INJURY||MOTOR_VEHICLE_COLLISION

HAZSIT (15 codes, 4 subcategories):
  HAZARDOUS_MATERIALS: carbon_monoxide, gas_leak, fuel_spill, hazmat release, + more
  HAZARD_NONCHEM: motor_vehicle_collision, electrical hazard, power line down, bomb threat
  INVESTIGATION: odor, smoke_investigation
  OVERPRESSURE: no_rupture, rupture_without_fire

  Common codes:
  - HAZSIT||HAZARD_NONCHEM||MOTOR_VEHICLE_COLLISION
  - HAZSIT||HAZARDOUS_MATERIALS||CARBON_MONOXIDE_RELEASE
  - HAZSIT||HAZARDOUS_MATERIALS||GAS_LEAK_ODOR
  - HAZSIT||HAZARDOUS_MATERIALS||FUEL_SPILL_ODOR
  - HAZSIT||INVESTIGATION||SMOKE_INVESTIGATION

PUBSERV (13 codes, 4 subcategories):
  CITIZEN_ASSIST: lift_assist, citizen_assist_service_call, lost_person, person_in_distress
  ALARMS_NONMED: fire_alarm, co_alarm, gas_alarm, other_alarm
  DISASTER_WEATHER: damage_assessment, weather_response
  OTHER: move_up, standby, damaged_hydrant

  Common codes:
  - PUBSERV||CITIZEN_ASSIST||LIFT_ASSIST
  - PUBSERV||CITIZEN_ASSIST||CITIZEN_ASSIST_SERVICE_CALL
  - PUBSERV||ALARMS_NONMED||FIRE_ALARM
  - PUBSERV||ALARMS_NONMED||CO_ALARM

NOEMERG (10 codes, 3 subcategories):
  CANCELLED (1 code): NOEMERG||CANCELLED
  FALSE_ALARM: accidental, malfunctioning, intentional, other, bomb_scare
  GOOD_INTENT: no_incident_found, controlled_burning, smoke_nonhostile, investigate_hazardous

  Common codes:
  - NOEMERG||CANCELLED
  - NOEMERG||FALSE_ALARM||MALFUNCTIONING_ALARM
  - NOEMERG||FALSE_ALARM||ACCIDENTAL_ALARM
  - NOEMERG||GOOD_INTENT||CONTROLLED_BURNING_AUTHORIZED
  - NOEMERG||GOOD_INTENT||SMOKE_FROM_NONHOSTILE_SOURCE

RESCUE (19 codes, 4 subcategories):
  OUTSIDE: backcountry, confined_space, extrication, high/low/steep angle, trench, limited_access
  STRUCTURE: building_collapse, confined_space, elevator, extrication
  TRANSPORTATION: motor_vehicle_extrication, aviation, train
  WATER: person_in_water (standing/swiftwater), watercraft_in_distress

LAWENFORCE (1 code): LAWENFORCE

========================================
LOCATION USE (value_set: "location_use")
========================================

78 total codes. Top-level categories:
  RESIDENTIAL, COMMERCIAL, OUTDOOR, ROADWAY_ACCESS, ASSEMBLY,
  EDUCATION, GOVERNMENT, HEALTH_CARE, INDUSTRIAL, OUTDOOR_INDUSTRIAL,
  AGRICULTURE_STRUCT, STORAGE, UTILITY_MISC, UNCLASSIFIED

Common codes:
- RESIDENTIAL||DETATCHED_SINGLE_FAMILY_DWELLING
- RESIDENTIAL||ATTACHED_SINGLE_FAMILY_DWELLING
- RESIDENTIAL||MANUFACTURED_MOBILE_HOME
- RESIDENTIAL||MULTI_FAMILY_LOWRISE_DWELLING
- RESIDENTIAL||TEMPORARY_LODGING_HOTEL_MOTEL
- COMMERCIAL||RETAIL_WHOLESALE_TRADE
- COMMERCIAL||RESTAURANT_CAFE
- OUTDOOR||FOREST_GRASSLANDS_WOODLAND_WILDLAND_AREAS
- OUTDOOR||PLAYGROUND_PARK_RECREATIONAL_AREA
- ROADWAY_ACCESS||STREET
- ROADWAY_ACCESS||HIGHWAY_INTERSTATE
- INDUSTRIAL||LIGHT
- AGRICULTURE_STRUCT||FARM_BUILDING

========================================
FIRE CONDITION ON ARRIVAL (value_set: "fire_condition_arrival")
========================================

6 total codes (this is the COMPLETE list):
- NO_SMOKE_FIRE_SHOWING
- SMOKE_SHOWING
- SMOKE_FIRE_SHOWING
- STRUCTURE_INVOLVED
- FIRE_SPREAD_BEYOND_STRUCTURE
- FIRE_OUT_UPON_ARRIVAL

========================================
COMMON ACTIONS (value_set: "action_tactic")
========================================

89 total codes. Top-level categories:
  SUPPRESSION, EMERGENCY_MEDICAL_CARE, COMMAND_AND_CONTROL,
  VENTILATION, SEARCH_STRUCTURE, NON_STRUCTURE_SEARCH,
  SALVAGE_AND_OVERHAUL, PROVIDE_SERVICES, PROVIDE_EQUIPMENT,
  PROVIDE_EVACUATION_SUPPORT, HAZARDOUS_SITUATION_MITIGATION,
  CONTAINMENT, FORCIBLE_ENTRY, INVESTIGATION,
  INFORMATION_ENFORCEMENT, PERSONNEL_CONTAMINATION_REDUCTION

Common codes:
- SUPPRESSION||STRUCTURAL_FIRE_SUPPRESSION||INTERIOR
- SUPPRESSION||STRUCTURAL_FIRE_SUPPRESSION||EXTERIOR
- SUPPRESSION||STRUCTURAL_FIRE_SUPPRESSION||EXTERIOR_AND_INTERIOR
- SALVAGE_AND_OVERHAUL
- EMERGENCY_MEDICAL_CARE||PROVIDE_BASIC_LIFE_SUPPORT
- EMERGENCY_MEDICAL_CARE||PROVIDE_ADVANCED_LIFE_SUPPORT
- EMERGENCY_MEDICAL_CARE||PROVIDE_TRANSPORT
- EMERGENCY_MEDICAL_CARE||PATIENT_ASSESSMENT
- COMMAND_AND_CONTROL||ESTABLISH_INCIDENT_COMMAND
- COMMAND_AND_CONTROL||SAFETY_OFFICER_ASSIGNED
- SEARCH_STRUCTURE||DOOR_INITIATED_SEARCH
- VENTILATION||POSITIVE_PRESSURE
