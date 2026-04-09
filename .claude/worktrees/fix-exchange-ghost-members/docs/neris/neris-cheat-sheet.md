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
NO-ACTION REASONS (when action_taken=NOACTION)
========================================

3 values (COMPLETE list):
- CANCELLED — Call cancelled before arrival
- STAGED_STANDBY — Units staged/stood by, not needed
- NO_INCIDENT_FOUND — Arrived on scene, no incident found

Use NOACTION when no on-scene activity occurred.
Use ACTION when crew performed ANY on-scene activity, even brief.

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

========================================
WATER SUPPLY (value_set: "water_supply")
========================================

9 total codes (COMPLETE list):
- HYDRANT_LESS_500
- HYDRANT_GREATER_500
- TANK_WATER
- WATER_TENDER_SHUTTLE
- NURSE_OTHER_APPARATUS
- DRAFT_FROM_STATIC_SOURCE
- SUPPLY_FROM_FIRE_BOAT
- FOAM_ADDITIVE
- NONE

========================================
FIRE CAUSE — INSIDE (value_set: "fire_cause_in")
========================================

13 total codes (COMPLETE list):
- OPERATING_EQUIPMENT
- ELECTRICAL
- BATTERY_POWER_STORAGE
- HEAT_FROM_ANOTHER_OBJECT
- EXPLOSIVES_FIREWORKS
- SMOKING_MATERIALS_ILLICIT_DRUGS
- OPEN_FLAME
- COOKING
- CHEMICAL
- ACT_OF_NATURE
- INCENDIARY
- OTHER_HEAT_SOURCE
- UNABLE_TO_BE_DETERMINED

========================================
FIRE CAUSE — OUTSIDE (value_set: "fire_cause_out")
========================================

14 total codes (COMPLETE list):
- NATURAL
- EQUIPMENT_VEHICLE_USE
- SMOKING_MATERIALS_ILLICIT_DRUGS
- RECREATION_CEREMONY
- DEBRIS_OPEN_BURNING
- RAILROAD_OPS_MAINTENANCE
- FIREARMS_EXPLOSIVES
- FIREWORKS
- POWER_GEN_TRANS_DIST
- STRUCTURE
- INCENDIARY
- BATTERY_POWER_STORAGE
- SPREAD_FROM_CONTROLLED_BURN
- UNABLE_TO_BE_DETERMINED

========================================
FIRE INVESTIGATION (value_set: "fire_invest_need")
========================================

6 total codes (COMPLETE list):
- YES
- NO
- NOT_EVALUATED
- NOT_APPLICABLE
- NO_CAUSE_OBVIOUS
- OTHER

========================================
FIRE BUILDING DAMAGE (value_set: "fire_bldg_damage")
========================================

4 total codes (COMPLETE list):
- NO_DAMAGE
- MINOR_DAMAGE
- MODERATE_DAMAGE
- MAJOR_DAMAGE

========================================
RESPONSE MODE (value_set: "response_mode")
========================================

2 total codes (COMPLETE list):
- EMERGENT
- NON_EMERGENT

========================================
MEDICAL — CARE DISPOSITION (value_set: "medical_patient_care")
========================================

6 total codes (COMPLETE list):
- PATIENT_EVALUATED_CARE_PROVIDED
- PATIENT_EVALUATED_REFUSED_CARE
- PATIENT_EVALUATED_NO_CARE_REQUIRED
- PATIENT_REFUSED_EVALUATION_CARE
- PATIENT_SUPPORT_SERVICES_PROVIDED
- PATIENT_DEAD_ON_ARRIVAL

========================================
MEDICAL — TRANSPORT DISPOSITION (value_set: "medical_transport")
========================================

5 total codes (COMPLETE list):
- TRANSPORT_BY_EMS_UNIT
- OTHER_AGENCY_TRANSPORT
- PATIENT_REFUSED_TRANSPORT
- NONPATIENT_TRANSPORT
- NO_TRANSPORT

========================================
MEDICAL — PATIENT STATUS (value_set: "medical_patient_status")
========================================

3 total codes (COMPLETE list):
- IMPROVED
- UNCHANGED
- WORSE

========================================
RESCUE MODE (value_set: "rescue_mode")
========================================

5 total codes (COMPLETE list):
- REMOVAL_FROM_STRUCTURE
- EXTRICATION
- DISENTANGLEMENT
- RECOVERY
- OTHER

========================================
RESCUE ACTIONS (value_set: "rescue_action")
========================================

9 total codes (COMPLETE list):
- VENTILATION
- HYDRAULIC_TOOL_USE
- UNDERWATER_DIVE
- ROPE_RIGGING
- BREAK_BREACH_WALL
- BRACE_WALL_INFRASTRUCTURE
- TRENCH_SHORING
- SUPPLY_AIR
- NONE

========================================
RESCUE IMPEDIMENT (value_set: "rescue_impediment")
========================================

6 total codes (COMPLETE list):
- HOARDING_CONDITIONS
- ACCESS_LIMITATIONS
- PHYSICAL_MEDICAL_CONDITIONS_PERSON
- IMPAIRED_PERSON
- OTHER
- NONE

========================================
RESCUE ELEVATION (value_set: "rescue_elevation")
========================================

4 total codes (COMPLETE list):
- ON_FLOOR
- ON_BED
- ON_FURNITURE
- OTHER

========================================
CASUALTY — ACTIVITY (value_set: "casualty_action")
========================================

13 total codes (COMPLETE list):
- SEARCH_RESCUE
- CARRYING_SETTINGUP_EQUIPMENT
- ADVANCING_OPERATING_HOSELINE
- VEHICLE_EXTRICATION
- VENTILATION
- FORCIBLE_ENTRY
- PUMP_OPERATIONS
- EMS_PATIENT_CARE
- DURING_INCIDENT_RESPONSE
- SCENE_SAFETY_DIRECTING_TRAFFIC
- STANDBY
- INCIDENT_COMMAND
- OTHER

========================================
CASUALTY — CAUSE (value_set: "casualty_cause")
========================================

9 total codes (COMPLETE list):
- CAUGHT_TRAPPED_BY_FIRE_EXPLOSION
- FALL_JUMP
- STRESS_OVEREXERTION
- COLLAPSE
- CAUGHT_TRAPPED_BY_OBJECT
- STRUCK_CONTACT_WITH_OBJECT
- EXPOSURE
- VEHICLE_COLLISION
- OTHER

========================================
CASUALTY — PPE (value_set: "casualty_ppe")
========================================

14 total codes (COMPLETE list):
- TURNOUT_COAT
- BUNKER_PANTS
- PROTECTIVE_HOOD
- GLOVES
- FACE_SHIELD_GOGGLES
- HELMET
- SCBA
- PASS_DEVICE
- RUBBER_KNEE_BOOTS
- 3_4_BOOTS
- BRUSH_GEAR
- REFLECTIVE_VEST
- OTHER_SPECIAL_EQUIPMENT
- NONE

========================================
CASUALTY — TIMELINE (value_set: "casualty_timeline")
========================================

6 total codes (COMPLETE list):
- RESPONDING
- INITIAL_RESPONSE
- CONTINUING_OPERATIONS
- EXTENDED_OPERATIONS
- AFTER_CONCLUSION_OF_INCIDENT
- UNKNOWN

========================================
ROOM OF ORIGIN (value_set: "room")
========================================

14 total codes (COMPLETE list):
- ASSEMBLY
- BATHROOM
- BEDROOM
- KITCHEN
- LIVING_SPACE
- HALLWAY_FOYER
- GARAGE
- BALCONY_PORCH_DECK
- BASEMENT
- ATTIC
- OFFICE
- UTILITY_ROOM
- OTHER
- UNKNOWN
