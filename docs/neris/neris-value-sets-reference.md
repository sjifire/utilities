# NERIS Value Sets — Quick Reference

> Auto-generated from `neris-api-client`. These are the 35 most commonly used
> value sets (592 values) for incident reporting. The full package contains
> 88 value sets with 1605 total values — use `list_neris_value_sets`
> and `get_neris_values` tools for the complete set.

Reference data for the most commonly used NERIS value sets in incident reporting.
For other value sets, use the `get_neris_values` MCP tool.

---

# Incident Types

NERIS value set: `TypeIncidentValue` (128 values)
Values use `||` as hierarchy separator.

## Fire

### Outside Fire

- `FIRE||OUTSIDE_FIRE||CONSTRUCTION_WASTE`
- `FIRE||OUTSIDE_FIRE||DUMPSTER_OUTDOOR_CONTAINER_FIRE`
- `FIRE||OUTSIDE_FIRE||OTHER_OUTSIDE_FIRE`
- `FIRE||OUTSIDE_FIRE||OUTSIDE_TANK_FIRE`
- `FIRE||OUTSIDE_FIRE||TRASH_RUBBISH_FIRE`
- `FIRE||OUTSIDE_FIRE||UTILITY_INFRASTRUCTURE_FIRE`
- `FIRE||OUTSIDE_FIRE||VEGETATION_GRASS_FIRE`
- `FIRE||OUTSIDE_FIRE||WILDFIRE_URBAN_INTERFACE`
- `FIRE||OUTSIDE_FIRE||WILDFIRE_WILDLAND`

### Special Fire

- `FIRE||SPECIAL_FIRE||ESS_FIRE`
- `FIRE||SPECIAL_FIRE||EXPLOSION`
- `FIRE||SPECIAL_FIRE||INFRASTRUCTURE_FIRE`

### Structure Fire

- `FIRE||STRUCTURE_FIRE||CHIMNEY_FIRE`
- `FIRE||STRUCTURE_FIRE||CONFINED_COOKING_APPLIANCE_FIRE`
- `FIRE||STRUCTURE_FIRE||ROOM_AND_CONTENTS_FIRE`
- `FIRE||STRUCTURE_FIRE||STRUCTURAL_INVOLVEMENT_FIRE`

### Transportation Fire

- `FIRE||TRANSPORTATION_FIRE||AIRCRAFT_FIRE`
- `FIRE||TRANSPORTATION_FIRE||BOAT_PERSONAL_WATERCRAFT_BARGE_FIRE`
- `FIRE||TRANSPORTATION_FIRE||POWERED_MOBILITY_DEVICE_FIRE`
- `FIRE||TRANSPORTATION_FIRE||TRAIN_RAIL_FIRE`
- `FIRE||TRANSPORTATION_FIRE||VEHICLE_FIRE_COMMERCIAL`
- `FIRE||TRANSPORTATION_FIRE||VEHICLE_FIRE_FOOD_TRUCK`
- `FIRE||TRANSPORTATION_FIRE||VEHICLE_FIRE_PASSENGER`
- `FIRE||TRANSPORTATION_FIRE||VEHICLE_FIRE_RV`

## Hazsit

### Hazardous Materials

- `HAZSIT||HAZARDOUS_MATERIALS||BIOLOGICAL_RELEASE_INCIDENT`
- `HAZSIT||HAZARDOUS_MATERIALS||CARBON_MONOXIDE_RELEASE`
- `HAZSIT||HAZARDOUS_MATERIALS||FUEL_SPILL_ODOR`
- `HAZSIT||HAZARDOUS_MATERIALS||GAS_LEAK_ODOR`
- `HAZSIT||HAZARDOUS_MATERIALS||HAZMAT_RELEASE_FACILITY`
- `HAZSIT||HAZARDOUS_MATERIALS||HAZMAT_RELEASE_TRANSPORT`
- `HAZSIT||HAZARDOUS_MATERIALS||RADIOACTIVE_RELEASE_INCIDENT`

### Hazard Nonchem

- `HAZSIT||HAZARD_NONCHEM||BOMB_THREAT_RESPONSE_SUSPICIOUS_PACKAGE`
- `HAZSIT||HAZARD_NONCHEM||ELEC_HAZARD_SHORT_CIRCUIT`
- `HAZSIT||HAZARD_NONCHEM||ELEC_POWER_LINE_DOWN_ARCHING_MALFUNC`
- `HAZSIT||HAZARD_NONCHEM||MOTOR_VEHICLE_COLLISION`

### Investigation

- `HAZSIT||INVESTIGATION||ODOR`
- `HAZSIT||INVESTIGATION||SMOKE_INVESTIGATION`

### Overpressure

- `HAZSIT||OVERPRESSURE||NO_RUPTURE`
- `HAZSIT||OVERPRESSURE||RUPTURE_WITHOUT_FIRE`

## Medical

### Illness

- `MEDICAL||ILLNESS||ABDOMINAL_PAIN`
- `MEDICAL||ILLNESS||ALLERGIC_REACTION_STINGS`
- `MEDICAL||ILLNESS||ALTERED_MENTAL_STATUS`
- `MEDICAL||ILLNESS||BACK_PAIN_NON_TRAUMA`
- `MEDICAL||ILLNESS||BREATHING_PROBLEMS`
- `MEDICAL||ILLNESS||CARDIAC_ARREST`
- `MEDICAL||ILLNESS||CHEST_PAIN_NON_TRAUMA`
- `MEDICAL||ILLNESS||CONVULSIONS_SEIZURES`
- `MEDICAL||ILLNESS||DIABETIC_PROBLEMS`
- `MEDICAL||ILLNESS||HEADACHE`
- `MEDICAL||ILLNESS||HEART_PROBLEMS`
- `MEDICAL||ILLNESS||NAUSEA_VOMITING`
- `MEDICAL||ILLNESS||NO_APPROPRIATE_CHOICE`
- `MEDICAL||ILLNESS||OVERDOSE`
- `MEDICAL||ILLNESS||PANDEMIC_EPIDEMIC_OUTBREAK`
- `MEDICAL||ILLNESS||PREGNANCY_CHILDBIRTH`
- `MEDICAL||ILLNESS||PSYCHOLOGICAL_BEHAVIOR_ISSUES`
- `MEDICAL||ILLNESS||SICK_CASE`
- `MEDICAL||ILLNESS||STROKE_CVA`
- `MEDICAL||ILLNESS||UNCONSCIOUS_VICTIM`
- `MEDICAL||ILLNESS||UNKNOWN_PROBLEM`
- `MEDICAL||ILLNESS||WELL_PERSON_CHECK`

### Injury

- `MEDICAL||INJURY||ANIMAL_BITES`
- `MEDICAL||INJURY||ASSAULT`
- `MEDICAL||INJURY||BURNS_EXPLOSION`
- `MEDICAL||INJURY||CARBON_MONOXIDE_OTHER_INHALATION_INJURY`
- `MEDICAL||INJURY||CHOKING`
- `MEDICAL||INJURY||DROWNING_DIVING_SCUBA_ACCIDENT`
- `MEDICAL||INJURY||ELECTROCUTION`
- `MEDICAL||INJURY||EYE_TRAUMA`
- `MEDICAL||INJURY||FALL`
- `MEDICAL||INJURY||GUNSHOT_WOUND`
- `MEDICAL||INJURY||HEAT_COLD_EXPOSURE`
- `MEDICAL||INJURY||HEMORRHAGE_LACERATION`
- `MEDICAL||INJURY||INDUSTRIAL_INACCESSIBLE_ENTRAPMENT`
- `MEDICAL||INJURY||MOTOR_VEHICLE_COLLISION`
- `MEDICAL||INJURY||OTHER_TRAUMATIC_INJURY`
- `MEDICAL||INJURY||POISONING`
- `MEDICAL||INJURY||STAB_PENETRATING_TRAUMA`

### Other

- `MEDICAL||OTHER||AIRMEDICAL_TRANSPORT`
- `MEDICAL||OTHER||COMMUNITY_PUBLIC_HEALTH`
- `MEDICAL||OTHER||HEALTHCARE_PROFESSIONAL_ADMISSION`
- `MEDICAL||OTHER||INTERCEPT_OTHER_UNIT`
- `MEDICAL||OTHER||MEDICAL_ALARM`
- `MEDICAL||OTHER||STANDBY_REQUEST`
- `MEDICAL||OTHER||TRANSFER_INTERFACILITY`

## Noemerg

- `NOEMERG||CANCELLED` — Cancelled

### False Alarm

- `NOEMERG||FALSE_ALARM||ACCIDENTAL_ALARM`
- `NOEMERG||FALSE_ALARM||BOMB_SCARE`
- `NOEMERG||FALSE_ALARM||INTENTIONAL_FALSE_ALARM`
- `NOEMERG||FALSE_ALARM||MALFUNCTIONING_ALARM`
- `NOEMERG||FALSE_ALARM||OTHER_FALSE_CALL`

### Good Intent

- `NOEMERG||GOOD_INTENT||CONTROLLED_BURNING_AUTHORIZED`
- `NOEMERG||GOOD_INTENT||INVESTIGATE_HAZARDOUS_RELEASE`
- `NOEMERG||GOOD_INTENT||NO_INCIDENT_FOUND_LOCATION_ERROR`
- `NOEMERG||GOOD_INTENT||SMOKE_FROM_NONHOSTILE_SOURCE`

## Pubserv

### Alarms Nonmed

- `PUBSERV||ALARMS_NONMED||CO_ALARM`
- `PUBSERV||ALARMS_NONMED||FIRE_ALARM`
- `PUBSERV||ALARMS_NONMED||GAS_ALARM`
- `PUBSERV||ALARMS_NONMED||OTHER_ALARM`

### Citizen Assist

- `PUBSERV||CITIZEN_ASSIST||CITIZEN_ASSIST_SERVICE_CALL`
- `PUBSERV||CITIZEN_ASSIST||LIFT_ASSIST`
- `PUBSERV||CITIZEN_ASSIST||LOST_PERSON`
- `PUBSERV||CITIZEN_ASSIST||PERSON_IN_DISTRESS`

### Disaster Weather

- `PUBSERV||DISASTER_WEATHER||DAMAGE_ASSESSMENT`
- `PUBSERV||DISASTER_WEATHER||WEATHER_RESPONSE`

### Other

- `PUBSERV||OTHER||DAMAGED_HYDRANT`
- `PUBSERV||OTHER||MOVE_UP`
- `PUBSERV||OTHER||STANDBY`

## Rescue

### Outside

- `RESCUE||OUTSIDE||BACKCOUNTRY_RESCUE`
- `RESCUE||OUTSIDE||CONFINED_SPACE_RESCUE`
- `RESCUE||OUTSIDE||EXTRICATION_ENTRAPPED`
- `RESCUE||OUTSIDE||HIGH_ANGLE_RESCUE`
- `RESCUE||OUTSIDE||LIMITED_NO_ACCESS`
- `RESCUE||OUTSIDE||LOW_ANGLE_RESCUE`
- `RESCUE||OUTSIDE||STEEP_ANGLE_RESCUE`
- `RESCUE||OUTSIDE||TRENCH`

### Structure

- `RESCUE||STRUCTURE||BUILDING_STRUCTURE_COLLAPSE`
- `RESCUE||STRUCTURE||CONFINED_SPACE_RESCUE`
- `RESCUE||STRUCTURE||ELEVATOR_ESCALATOR_RESCUE`
- `RESCUE||STRUCTURE||EXTRICATION_ENTRAPPED`

### Transportation

- `RESCUE||TRANSPORTATION||AVIATION_COLLISION_CRASH`
- `RESCUE||TRANSPORTATION||AVIATION_STANDBY`
- `RESCUE||TRANSPORTATION||MOTOR_VEHICLE_EXTRICATION_ENTRAPPED`
- `RESCUE||TRANSPORTATION||TRAIN_RAIL_COLLISION_DERAILMENT`

### Water

- `RESCUE||WATER||PERSON_IN_WATER_STANDING`
- `RESCUE||WATER||PERSON_IN_WATER_SWIFTWATER`
- `RESCUE||WATER||WATERCRAFT_IN_DISTRESS`

- `LAWENFORCE` — Lawenforce

---

# Actions / Tactics Taken

NERIS value set: `TypeActionTacticValue` (89 values)
Values use `||` as hierarchy separator.

## Command And Control

- `COMMAND_AND_CONTROL||ACCOUNTABILITY_OFFICER_ASSIGNED` — Accountability Officer Assigned
- `COMMAND_AND_CONTROL||ESTABLISH_INCIDENT_COMMAND` — Establish Incident Command
- `COMMAND_AND_CONTROL||INCIDENT_ASSESSMENT_COMPLETED` — Incident Assessment Completed
- `COMMAND_AND_CONTROL||NOTIFY_OTHER_AGENCIES` — Notify Other Agencies
- `COMMAND_AND_CONTROL||PIO_ASSIGNED` — Pio Assigned
- `COMMAND_AND_CONTROL||SAFETY_OFFICER_ASSIGNED` — Safety Officer Assigned

## Containment

### Outside Fire Suppression

- `CONTAINMENT||OUTSIDE_FIRE_SUPPRESSION||DOZER_FUEL_BREAK`
- `CONTAINMENT||OUTSIDE_FIRE_SUPPRESSION||HAND_CREW_FUEL_BREAK`

## Emergency Medical Care

- `EMERGENCY_MEDICAL_CARE||PATIENT_ASSESSMENT` — Patient Assessment
- `EMERGENCY_MEDICAL_CARE||PATIENT_REFERRAL` — Patient Referral
- `EMERGENCY_MEDICAL_CARE||PROVIDE_ADVANCED_LIFE_SUPPORT` — Provide Advanced Life Support
- `EMERGENCY_MEDICAL_CARE||PROVIDE_BASIC_LIFE_SUPPORT` — Provide Basic Life Support
- `EMERGENCY_MEDICAL_CARE||PROVIDE_TRANSPORT` — Provide Transport

## Hazardous Situation Mitigation

- `HAZARDOUS_SITUATION_MITIGATION||ATMOSPHERIC_MONITORING_EXTERIOR_FENCELINE` — Atmospheric Monitoring Exterior Fenceline
- `HAZARDOUS_SITUATION_MITIGATION||ATMOSPHERIC_MONITORING_INTERIOR` — Atmospheric Monitoring Interior
- `HAZARDOUS_SITUATION_MITIGATION||DECONTAMINATION` — Decontamination
- `HAZARDOUS_SITUATION_MITIGATION||LEAK_STOP` — Leak Stop
- `HAZARDOUS_SITUATION_MITIGATION||REMOVE_HAZARD` — Remove Hazard
- `HAZARDOUS_SITUATION_MITIGATION||SPILL_CONTROL` — Spill Control
- `HAZARDOUS_SITUATION_MITIGATION||TAKE_SAMPLES` — Take Samples

## Information Enforcement

- `INFORMATION_ENFORCEMENT||ENFORCE_CODE_OR_LAW` — Enforce Code Or Law
- `INFORMATION_ENFORCEMENT||PROVIDE_PUBLIC_INFORMATION` — Provide Public Information
- `INFORMATION_ENFORCEMENT||REFER_TO_PROPER_AHJ` — Refer To Proper Ahj

## Non Structure Search

- `NON_STRUCTURE_SEARCH||BODY_RECOVERY` — Body Recovery
- `NON_STRUCTURE_SEARCH||SEARCH_AREA_OF_COLLAPSE` — Search Area Of Collapse
- `NON_STRUCTURE_SEARCH||SEARCH_UNDERGROUND_INFRASTRUCTURE` — Search Underground Infrastructure
- `NON_STRUCTURE_SEARCH||SEARCH_WATERWAY` — Search Waterway
- `NON_STRUCTURE_SEARCH||USAR_K9_SEARCH` — Usar K9 Search
- `NON_STRUCTURE_SEARCH||WIDE_AREA_OUTDOOR_SEARCH` — Wide Area Outdoor Search

## Personnel Contamination Reduction

- `PERSONNEL_CONTAMINATION_REDUCTION||CLEAN_CAB_TRANSPORT` — Clean Cab Transport
- `PERSONNEL_CONTAMINATION_REDUCTION||ON_SCENE_CONTAMINATION_REDUCTION` — On Scene Contamination Reduction
- `PERSONNEL_CONTAMINATION_REDUCTION||PPE_WASHED_POST_INCIDENT` — Ppe Washed Post Incident

## Provide Equipment

- `PROVIDE_EQUIPMENT||PROVIDE_DRONE_VIDEO_EQUIPMENT` — Provide Drone Video Equipment
- `PROVIDE_EQUIPMENT||PROVIDE_ELECTRICAL_POWER` — Provide Electrical Power
- `PROVIDE_EQUIPMENT||PROVIDE_LIGHT` — Provide Light
- `PROVIDE_EQUIPMENT||PROVIDE_SPECIAL_EQUIPMENT` — Provide Special Equipment

## Provide Evacuation Support

- `PROVIDE_EVACUATION_SUPPORT||CONNECTED_INTERIOR_SPACES` — Connected Interior Spaces
- `PROVIDE_EVACUATION_SUPPORT||LARGE_AREA` — Large Area
- `PROVIDE_EVACUATION_SUPPORT||NEARBY_BUILDINGS` — Nearby Buildings
- `PROVIDE_EVACUATION_SUPPORT||REMOTE_INTERIOR_SPACES` — Remote Interior Spaces

## Provide Services

- `PROVIDE_SERVICES||ASSIST_ANIMAL` — Assist Animal
- `PROVIDE_SERVICES||ASSIST_UNINJURED_PERSON` — Assist Uninjured Person
- `PROVIDE_SERVICES||CONTROL_CROWD` — Control Crowd
- `PROVIDE_SERVICES||CONTROL_TRAFFIC` — Control Traffic
- `PROVIDE_SERVICES||DAMAGE_ASSESSMENT` — Damage Assessment
- `PROVIDE_SERVICES||PROVIDE_APPARATUS_WATER` — Provide Apparatus Water
- `PROVIDE_SERVICES||REMOVE_WATER` — Remove Water
- `PROVIDE_SERVICES||RESTORE_RESET_ALARM_SYSTEM` — Restore Reset Alarm System
- `PROVIDE_SERVICES||RESTORE_SPRINKLER_SYSTEM` — Restore Sprinkler System
- `PROVIDE_SERVICES||SECURE_PROPERTY` — Secure Property
- `PROVIDE_SERVICES||SHUT_DOWN_ALARM` — Shut Down Alarm
- `PROVIDE_SERVICES||SHUT_DOWN_SPRINKLER_SYSTEM` — Shut Down Sprinkler System

## Search Structure

- `SEARCH_STRUCTURE||DOOR_INITIATED_SEARCH` — Door Initiated Search
- `SEARCH_STRUCTURE||WINDOW_INITIATED_SEARCH` — Window Initiated Search

### Door Initiated Search

- `SEARCH_STRUCTURE||DOOR_INITIATED_SEARCH||DURING_SUPPRESSION`
- `SEARCH_STRUCTURE||DOOR_INITIATED_SEARCH||POST_SUPPRESSION`
- `SEARCH_STRUCTURE||DOOR_INITIATED_SEARCH||PRIOR_TO_SUPPRESSION`

### Window Initiated Search

- `SEARCH_STRUCTURE||WINDOW_INITIATED_SEARCH||DURING_SUPPRESSION`
- `SEARCH_STRUCTURE||WINDOW_INITIATED_SEARCH||POST_SUPPRESSION`
- `SEARCH_STRUCTURE||WINDOW_INITIATED_SEARCH||PRIOR_TO_SUPPRESSION`

## Suppression

### Outside Fire Suppression

- `SUPPRESSION||OUTSIDE_FIRE_SUPPRESSION||BACKBURN`
- `SUPPRESSION||OUTSIDE_FIRE_SUPPRESSION||CONFINEMENT`
- `SUPPRESSION||OUTSIDE_FIRE_SUPPRESSION||ESTABLISH_FIRE_LINES`
- `SUPPRESSION||OUTSIDE_FIRE_SUPPRESSION||FIRE_CONTROL_EXTINGUISHMENT`
- `SUPPRESSION||OUTSIDE_FIRE_SUPPRESSION||FIRE_RETARDANT_DROP`
- `SUPPRESSION||OUTSIDE_FIRE_SUPPRESSION||STRUCTURE_PROTECTION`
- `SUPPRESSION||OUTSIDE_FIRE_SUPPRESSION||WATER_DROP`

### Structural Fire Suppression

- `SUPPRESSION||STRUCTURAL_FIRE_SUPPRESSION||EXTERIOR`
- `SUPPRESSION||STRUCTURAL_FIRE_SUPPRESSION||EXTERIOR_AND_INTERIOR`
- `SUPPRESSION||STRUCTURAL_FIRE_SUPPRESSION||INTERIOR`

## Ventilation

- `VENTILATION||HORIZONTAL` — Horizontal
- `VENTILATION||HYDRAULIC` — Hydraulic
- `VENTILATION||POSITIVE_PRESSURE` — Positive Pressure
- `VENTILATION||VERTICAL` — Vertical

### Horizontal

- `VENTILATION||HORIZONTAL||DURING_SUPPRESSION`
- `VENTILATION||HORIZONTAL||POST_SUPPRESSION`
- `VENTILATION||HORIZONTAL||PRIOR_TO_SUPPRESSION`

### Hydraulic

- `VENTILATION||HYDRAULIC||DURING_SUPPRESSION`
- `VENTILATION||HYDRAULIC||POST_SUPPRESSION`
- `VENTILATION||HYDRAULIC||PRIOR_TO_SUPPRESSION`

### Positive Pressure

- `VENTILATION||POSITIVE_PRESSURE||DURING_SUPPRESSION`
- `VENTILATION||POSITIVE_PRESSURE||POST_SUPPRESSION`
- `VENTILATION||POSITIVE_PRESSURE||PRIOR_TO_SUPPRESSION`

### Vertical

- `VENTILATION||VERTICAL||DURING_SUPPRESSION`
- `VENTILATION||VERTICAL||POST_SUPPRESSION`
- `VENTILATION||VERTICAL||PRIOR_TO_SUPPRESSION`

- `FORCIBLE_ENTRY` — Forcible Entry
- `INVESTIGATION` — Investigation
- `SALVAGE_AND_OVERHAUL` — Salvage And Overhaul

---

# Location Use Types

NERIS value set: `TypeLocationUseValue` (78 values)
Values use `||` as hierarchy separator.

## Agriculture Struct

- `AGRICULTURE_STRUCT||ANIMAL_PROCESSING` — Animal Processing
- `AGRICULTURE_STRUCT||AUCTION_FEEDLOT` — Auction Feedlot
- `AGRICULTURE_STRUCT||FARM_BUILDING` — Farm Building
- `AGRICULTURE_STRUCT||STORAGE_SILO` — Storage Silo
- `AGRICULTURE_STRUCT||VETERINARY_LIVESTOCK` — Veterinary Livestock

## Assembly

- `ASSEMBLY||COMMUNITY_CENTER` — Community Center
- `ASSEMBLY||CONVENTION_CENTER` — Convention Center
- `ASSEMBLY||INDOOR_ARENA` — Indoor Arena
- `ASSEMBLY||MUSEUM_EXHIBIT_HALL_LIBRARY` — Museum Exhibit Hall Library
- `ASSEMBLY||OUTDOOR_ARENA_AMPHITHEATER_PARK` — Outdoor Arena Amphitheater Park
- `ASSEMBLY||RELIGIOUS` — Religious
- `ASSEMBLY||TEMP_OUTDOOR_STRUCT_EVENT` — Temp Outdoor Struct Event

## Commercial

- `COMMERCIAL||BAR_NIGHTCLUB` — Bar Nightclub
- `COMMERCIAL||ENTERTAINMENT_RECREATION` — Entertainment Recreation
- `COMMERCIAL||OFFICE_OTHER_TECHNICAL_SERVICES` — Office Other Technical Services
- `COMMERCIAL||RESTAURANT_CAFE` — Restaurant Cafe
- `COMMERCIAL||RETAIL_WHOLESALE_TRADE` — Retail Wholesale Trade
- `COMMERCIAL||THEATERS_STUDIO` — Theaters Studio
- `COMMERCIAL||VEHICLE_FUELING_CHARGING_STATION` — Vehicle Fueling Charging Station
- `COMMERCIAL||VEHICLE_REPAIR_SERVICES` — Vehicle Repair Services
- `COMMERCIAL||VETERINARY_PET` — Veterinary Pet

## Education

- `EDUCATION||COLLEGES_UNIVERSITIES` — Colleges Universities
- `EDUCATION||K_12_SCHOOLS` — K 12 Schools
- `EDUCATION||OTHER_EDUCATIONAL_BUILDINGS` — Other Educational Buildings
- `EDUCATION||PREK_DAYCARE` — Prek Daycare

## Government

- `GOVERNMENT||FIRE_MEDICAL_STATION` — Fire Medical Station
- `GOVERNMENT||GENERAL_SERVICES` — General Services
- `GOVERNMENT||JAIL_PRISON_REFORMATORY` — Jail Prison Reformatory
- `GOVERNMENT||NON_CIVILIAN_STRUCTURES` — Non Civilian Structures
- `GOVERNMENT||POLICE_EMERGENCY_STATION` — Police Emergency Station

## Health Care

- `HEALTH_CARE||ALCOHOL_DRUG_REHABILITATION_CENTER` — Alcohol Drug Rehabilitation Center
- `HEALTH_CARE||HOSPITAL_24_HOUR_MEDICAL_FACILITIES` — Hospital 24 Hour Medical Facilities
- `HEALTH_CARE||MEDICAL_OFFICE_CLINIC` — Medical Office Clinic
- `HEALTH_CARE||NURSING_HOME_ASSISTED_LIVING_RESIDENCE_ONSITE` — Nursing Home Assisted Living Residence Onsite

## Industrial

- `INDUSTRIAL||CHEMICAL` — Chemical
- `INDUSTRIAL||COLD_STORAGE` — Cold Storage
- `INDUSTRIAL||FOOD_DRUGS` — Food Drugs
- `INDUSTRIAL||HEAVY` — Heavy
- `INDUSTRIAL||LIGHT` — Light
- `INDUSTRIAL||METALS_MINERALS_PROCESSING` — Metals Minerals Processing

## Outdoor

- `OUTDOOR||CAMP_SITE` — Camp Site
- `OUTDOOR||FOREST_GRASSLANDS_WOODLAND_WILDLAND_AREAS` — Forest Grasslands Woodland Wildland Areas
- `OUTDOOR||GROUND_VACANT_LAND` — Ground Vacant Land
- `OUTDOOR||HIKING_TRAIL` — Hiking Trail
- `OUTDOOR||OPEN_WATER` — Open Water
- `OUTDOOR||ORCHARD_CROPS_FARMLAND` — Orchard Crops Farmland
- `OUTDOOR||PLAYGROUND_PARK_RECREATIONAL_AREA` — Playground Park Recreational Area
- `OUTDOOR||WATERFRONT` — Waterfront

## Outdoor Industrial

- `OUTDOOR_INDUSTRIAL||CONSTRUCTION_SITE` — Construction Site
- `OUTDOOR_INDUSTRIAL||DUMP_LANDFILL` — Dump Landfill
- `OUTDOOR_INDUSTRIAL||INDUSTRIAL_YARD` — Industrial Yard
- `OUTDOOR_INDUSTRIAL||MINE` — Mine

## Residential

- `RESIDENTIAL||ATTACHED_SINGLE_FAMILY_DWELLING` — Attached Single Family Dwelling
- `RESIDENTIAL||CONGREGATE_HOUSING` — Congregate Housing
- `RESIDENTIAL||DETATCHED_GARAGE` — Detatched Garage
- `RESIDENTIAL||DETATCHED_SINGLE_FAMILY_DWELLING` — Detatched Single Family Dwelling
- `RESIDENTIAL||MANUFACTURED_MOBILE_HOME` — Manufactured Mobile Home
- `RESIDENTIAL||MULTI_FAMILY_HIGHRISE_DWELLING` — Multi Family Highrise Dwelling
- `RESIDENTIAL||MULTI_FAMILY_LOWRISE_DWELLING` — Multi Family Lowrise Dwelling
- `RESIDENTIAL||MULTI_FAMILY_MIDRISE_DWELLING` — Multi Family Midrise Dwelling
- `RESIDENTIAL||TEMPORARY_LODGING_HOTEL_MOTEL` — Temporary Lodging Hotel Motel
- `RESIDENTIAL||UNHOUSED_TEMPORARY_SHELTER` — Unhoused Temporary Shelter

## Roadway Access

- `ROADWAY_ACCESS||BRIDGE` — Bridge
- `ROADWAY_ACCESS||HIGHWAY_INTERSTATE` — Highway Interstate
- `ROADWAY_ACCESS||LIMITED_ACCESS_HIGHWAY_INTERSTATE` — Limited Access Highway Interstate
- `ROADWAY_ACCESS||PARKING_LOT_GARAGE` — Parking Lot Garage
- `ROADWAY_ACCESS||RAILROAD_RAILYARD` — Railroad Railyard
- `ROADWAY_ACCESS||SIDEWALK` — Sidewalk
- `ROADWAY_ACCESS||STREET` — Street
- `ROADWAY_ACCESS||TUNNEL` — Tunnel

## Storage

- `STORAGE||STORAGE_MULTI_TENANT` — Storage Multi Tenant
- `STORAGE||STORAGE_PORTABLE_BUILDING` — Storage Portable Building
- `STORAGE||STORAGE_SINGLE_TENANT` — Storage Single Tenant

## Unclassified

- `UNCLASSIFIED||UNCLASSIFIED` — Unclassified

## Utility Misc

- `UTILITY_MISC||ENERGY_FACILITY_INFRASTRUCTURE` — Energy Facility Infrastructure
- `UTILITY_MISC||TRANSPORTATION_STATION_HUB_AREA` — Transportation Station Hub Area
- `UTILITY_MISC||TRASH_RECYCLING_FACILITY` — Trash Recycling Facility
- `UTILITY_MISC||WATER_SANITATION_FACILITY_INFRASTRUCTURE` — Water Sanitation Facility Infrastructure

---

# No Action Reason

NERIS value set: `TypeNoactionValue` (3 values)
- `CANCELLED` — Cancelled
- `STAGED_STANDBY` — Staged Standby
- `NO_INCIDENT_FOUND` — No Incident Found

---

# Response Mode

NERIS value set: `TypeResponseModeValue` (2 values)
- `EMERGENT` — Emergent
- `NON_EMERGENT` — Non Emergent

---

# Special Incident Modifiers

NERIS value set: `TypeSpecialModifierValue` (7 values)
- `ACTIVE_ASSAILANT` — Active Assailant
- `MCI` — Mci
- `FEDERAL_DECLARED_DISASTER` — Federal Declared Disaster
- `STATE_DECLARED_DISASTER` — State Declared Disaster
- `COUNTY_LOCAL_DECLARED_DISASTER` — County Local Declared Disaster
- `URBAN_CONFLAGRATION` — Urban Conflagration
- `VIOLENCE_AGAINST_RESPONDER` — Violence Against Responder

---

# Yes / No / Unknown

NERIS value set: `TypeYesNoUnknownValue` (3 values)
- `YES` — Yes
- `NO` — No
- `UNKNOWN` — Unknown

---

# Unit Types

NERIS value set: `TypeUnitValue` (49 values)
- `CREW_TRANS` — Crew Trans
- `ENGINE_STRUCT` — Engine Struct
- `ENGINE_WUI` — Engine Wui
- `BOAT` — Boat
- `BOAT_LARGE` — Boat Large
- `LADDER_SMALL` — Ladder Small
- `LADDER_QUINT` — Ladder Quint
- `LADDER_TALL` — Ladder Tall
- `QUINT_TALL` — Quint Tall
- `PLATFORM` — Platform
- `PLATFORM_QUINT` — Platform Quint
- `LADDER_TILLER` — Ladder Tiller
- `ARFF` — Arff
- `FOAM` — Foam
- `TENDER` — Tender
- `CREW` — Crew
- `HELO_GENERAL` — Helo General
- `HELO_FIRE` — Helo Fire
- `HELO_RESCUE` — Helo Rescue
- `UAS_FIRE` — Uas Fire
- `UAS_RECON` — Uas Recon
- `AIR_TANKER` — Air Tanker
- `AIR_EMS` — Air Ems
- `AIR_RECON` — Air Recon
- `ALS_AMB` — Als Amb
- `BLS_AMB` — Bls Amb
- `EMS_NOTRANS` — Ems Notrans
- `EMS_SUPV` — Ems Supv
- `MAB` — Mab
- `CHIEF_STAFF_COMMAND` — Chief Staff Command
- `HAZMAT` — Hazmat
- `DECON` — Decon
- `POV` — Pov
- `RESCUE_HEAVY` — Rescue Heavy
- `RESCUE_MEDIUM` — Rescue Medium
- `RESCUE_LIGHT` — Rescue Light
- `RESCUE_USAR` — Rescue Usar
- `RESCUE_WATER` — Rescue Water
- `SCBA` — Scba
- `AIR_LIGHT` — Air Light
- `REHAB` — Rehab
- `MOBILE_ICP` — Mobile Icp
- `MOBILE_COMMS` — Mobile Comms
- `DOZER` — Dozer
- `OTHER_GROUND` — Other Ground
- `ATV_EMS` — Atv Ems
- `ATV_FIRE` — Atv Fire
- `INVEST` — Invest
- `UTIL` — Util

---

# FD Services

NERIS value set: `TypeServFdValue` (37 values)
- `STRUCTURAL_FIREFIGHTING` — Structural Firefighting
- `HIGHRISE_FIREFIGHTING` — Highrise Firefighting
- `WILDLAND_FIREFIGHTING` — Wildland Firefighting
- `PETROCHEM_FIREFIGHTING` — Petrochem Firefighting
- `ARFF_FIREFIGHTING` — Arff Firefighting
- `MARINE_FIREFIGHTING` — Marine Firefighting
- `HAZMAT_OPS` — Hazmat Ops
- `HAZMAT_TECHNICIAN` — Hazmat Technician
- `ROPE_RESCUE` — Rope Rescue
- `COLLAPSE_RESCUE` — Collapse Rescue
- `VEHICLE_RESCUE` — Vehicle Rescue
- `ANIMAL_TECHRESCUE` — Animal Techrescue
- `WILDERNESS_SAR` — Wilderness Sar
- `TRENCH_RESCUE` — Trench Rescue
- `CONFINED_SPACE` — Confined Space
- `MACHINERY_RESCUE` — Machinery Rescue
- `CAVE_SAR` — Cave Sar
- `MINE_SAR` — Mine Sar
- `HELO_SAR` — Helo Sar
- `WATER_SAR` — Water Sar
- `SWIFTWATER_SAR` — Swiftwater Sar
- `DIVE_SAR` — Dive Sar
- `ICE_RESCUE` — Ice Rescue
- `SURF_RESCUE` — Surf Rescue
- `WATERCRAFT_RESCUE` — Watercraft Rescue
- `FLOOD_SAR` — Flood Sar
- `TOWER_SAR` — Tower Sar
- `REHABILITATION` — Rehabilitation
- `RRD_EXISTING` — Rrd Existing
- `RRD_NEWCONST` — Rrd Newconst
- `RRD_PUBLICED` — Rrd Publiced
- `RRD_PLANS` — Rrd Plans
- `CAUSE_ORIGIN` — Cause Origin
- `TRAINING_ELF` — Training Elf
- `TRAINING_VETFF` — Training Vetff
- `TRAINING_OD` — Training Od
- `TRAINING_DRIVER` — Training Driver

---

# EMS Services

NERIS value set: `TypeServEmsValue` (7 values)
- `NO_MEDICAL` — No Medical
- `BLS_NO_TRANSPORT` — Bls No Transport
- `ALS_NO_TRANSPORT` — Als No Transport
- `BLS_TRANSPORT` — Bls Transport
- `ALS_TRANSPORT` — Als Transport
- `AERO_TRANSPORT` — Aero Transport
- `COMMUNITY_MED` — Community Med

---

# Duty Status

NERIS value set: `TypeDutyValue` (7 values)
- `RESPONDING_TO_EMERGENCY_INCIDENT` — Responding To Emergency Incident
- `WORKING_AT_SCENE_OF_FIRE_INCIDENT` — Working At Scene Of Fire Incident
- `WORKING_AT_SCENE_OF_NONFIRE_INCIDENT` — Working At Scene Of Nonfire Incident
- `RETURNING_FROM_EMERGENCY_INCIDENT` — Returning From Emergency Incident
- `TRAINING` — Training
- `AFTER_INCIDENT` — After Incident
- `OTHER_ON_DUTY_INCIDENT` — Other On Duty Incident

---

# Aid Type

NERIS value set: `TypeAidValue` (3 values)
- `SUPPORT_AID` — Support Aid
- `IN_LIEU_AID` — In Lieu Aid
- `ACTING_AS_AID` — Acting As Aid

---

# Aid Direction

NERIS value set: `TypeAidDirectionValue` (2 values)
- `GIVEN` — Given
- `RECEIVED` — Received

---

# Fire Condition on Arrival

NERIS value set: `TypeFireConditionArrivalValue` (6 values)
- `NO_SMOKE_FIRE_SHOWING` — No Smoke Fire Showing
- `SMOKE_SHOWING` — Smoke Showing
- `SMOKE_FIRE_SHOWING` — Smoke Fire Showing
- `STRUCTURE_INVOLVED` — Structure Involved
- `FIRE_SPREAD_BEYOND_STRUCTURE` — Fire Spread Beyond Structure
- `FIRE_OUT_UPON_ARRIVAL` — Fire Out Upon Arrival

---

# Indoor Cause of Ignition

NERIS value set: `TypeFireCauseInValue` (13 values)
- `OPERATING_EQUIPMENT` — Operating Equipment
- `ELECTRICAL` — Electrical
- `BATTERY_POWER_STORAGE` — Battery Power Storage
- `HEAT_FROM_ANOTHER_OBJECT` — Heat From Another Object
- `EXPLOSIVES_FIREWORKS` — Explosives Fireworks
- `SMOKING_MATERIALS_ILLICIT_DRUGS` — Smoking Materials Illicit Drugs
- `OPEN_FLAME` — Open Flame
- `COOKING` — Cooking
- `CHEMICAL` — Chemical
- `ACT_OF_NATURE` — Act Of Nature
- `INCENDIARY` — Incendiary
- `OTHER_HEAT_SOURCE` — Other Heat Source
- `UNABLE_TO_BE_DETERMINED` — Unable To Be Determined

---

# Outdoor Cause of Ignition

NERIS value set: `TypeFireCauseOutValue` (14 values)
- `NATURAL` — Natural
- `EQUIPMENT_VEHICLE_USE` — Equipment Vehicle Use
- `SMOKING_MATERIALS_ILLICIT_DRUGS` — Smoking Materials Illicit Drugs
- `RECREATION_CEREMONY` — Recreation Ceremony
- `DEBRIS_OPEN_BURNING` — Debris Open Burning
- `RAILROAD_OPS_MAINTENANCE` — Railroad Ops Maintenance
- `FIREARMS_EXPLOSIVES` — Firearms Explosives
- `FIREWORKS` — Fireworks
- `POWER_GEN_TRANS_DIST` — Power Gen Trans Dist
- `STRUCTURE` — Structure
- `INCENDIARY` — Incendiary
- `BATTERY_POWER_STORAGE` — Battery Power Storage
- `SPREAD_FROM_CONTROLLED_BURN` — Spread From Controlled Burn
- `UNABLE_TO_BE_DETERMINED` — Unable To Be Determined

---

# Fire Building Damage

NERIS value set: `TypeFireBldgDamageValue` (4 values)
- `NO_DAMAGE` — No Damage
- `MINOR_DAMAGE` — Minor Damage
- `MODERATE_DAMAGE` — Moderate Damage
- `MAJOR_DAMAGE` — Major Damage

---

# Fire Investigation

NERIS value set: `TypeFireInvestValue` (8 values)
- `INVESTIGATED_ON_SCENE_RESOURCE` — Investigated On Scene Resource
- `INVESTIGATED_BY_ARSON_FIRE_INVESTIGATOR` — Investigated By Arson Fire Investigator
- `INVESTIGATED_BY_OUTSIDE_AGENCY` — Investigated By Outside Agency
- `INVESTIGATED_BY_STATE_FIRE_MARSHAL` — Investigated By State Fire Marshal
- `INVESTIGATED_BY_INSURANCE` — Investigated By Insurance
- `INVESTIGATED_BY_NONFIRE_LAW_ENFORCEMENT` — Investigated By Nonfire Law Enforcement
- `INVESTIGATED_BY_OTHER` — Investigated By Other
- `NONE` — None

---

# Fire Investigation Need

NERIS value set: `TypeFireInvestNeedValue` (6 values)
- `YES` — Yes
- `NO` — No
- `NOT_EVALUATED` — Not Evaluated
- `NOT_APPLICABLE` — Not Applicable
- `NO_CAUSE_OBVIOUS` — No Cause Obvious
- `OTHER` — Other

---

# Room of Origin

NERIS value set: `TypeRoomValue` (14 values)
- `ASSEMBLY` — Assembly
- `BATHROOM` — Bathroom
- `BEDROOM` — Bedroom
- `KITCHEN` — Kitchen
- `LIVING_SPACE` — Living Space
- `HALLWAY_FOYER` — Hallway Foyer
- `GARAGE` — Garage
- `BALCONY_PORCH_DECK` — Balcony Porch Deck
- `BASEMENT` — Basement
- `ATTIC` — Attic
- `OFFICE` — Office
- `UTILITY_ROOM` — Utility Room
- `OTHER` — Other
- `UNKNOWN` — Unknown

---

# Water Supply

NERIS value set: `TypeWaterSupplyValue` (9 values)
- `HYDRANT_LESS_500` — Hydrant Less 500
- `HYDRANT_GREATER_500` — Hydrant Greater 500
- `TANK_WATER` — Tank Water
- `WATER_TENDER_SHUTTLE` — Water Tender Shuttle
- `NURSE_OTHER_APPARATUS` — Nurse Other Apparatus
- `DRAFT_FROM_STATIC_SOURCE` — Draft From Static Source
- `SUPPLY_FROM_FIRE_BOAT` — Supply From Fire Boat
- `FOAM_ADDITIVE` — Foam Additive
- `NONE` — None

---

# Suppression Appliances

NERIS value set: `TypeSuppressApplianceValue` (12 values)
- `FIRE_EXTINGUISHER` — Fire Extinguisher
- `BOOSTER_FIRE_HOSE` — Booster Fire Hose
- `SMALL_DIAMETER_FIRE_HOSE` — Small Diameter Fire Hose
- `MEDIUM_DIAMETER_FIRE_HOSE` — Medium Diameter Fire Hose
- `GROUND_MONITOR` — Ground Monitor
- `MASTER_STREAM` — Master Stream
- `ELEVATED_MASTER_STREAM_STANDPIPE` — Elevated Master Stream Standpipe
- `BUILDING_STANDPIPE` — Building Standpipe
- `BUILDING_FDC` — Building Fdc
- `AIRATTACK_HELITACK` — Airattack Helitack
- `OTHER` — Other
- `NONE` — None

---

# Fire Suppression Type

NERIS value set: `TypeSuppressFireValue` (8 values)
- `WET_PIPE_SPRINKLER_SYSTEM` — Wet Pipe Sprinkler System
- `DRY_PIPE_SPRINKLER_SYSTEM` — Dry Pipe Sprinkler System
- `PRE_ACTION_SYSTEM` — Pre Action System
- `DELUGE_SYSTEM` — Deluge System
- `CLEAN_AGENT_SYSTEM` — Clean Agent System
- `INDUSTRIAL_DRY_CHEM_SYSTEM` — Industrial Dry Chem System
- `OTHER` — Other
- `UNKNOWN` — Unknown

---

# Suppression Operation

NERIS value set: `TypeSuppressOperationValue` (3 values)
- `OPERATED_EFFECTIVE` — Operated Effective
- `OPERATED_NOT_EFFECTIVE` — Operated Not Effective
- `NO_OPERATION` — No Operation

---

# Medical Patient Care Disposition

NERIS value set: `TypeMedicalPatientCareValue` (6 values)
- `PATIENT_EVALUATED_CARE_PROVIDED` — Patient Evaluated Care Provided
- `PATIENT_EVALUATED_REFUSED_CARE` — Patient Evaluated Refused Care
- `PATIENT_EVALUATED_NO_CARE_REQUIRED` — Patient Evaluated No Care Required
- `PATIENT_REFUSED_EVALUATION_CARE` — Patient Refused Evaluation Care
- `PATIENT_SUPPORT_SERVICES_PROVIDED` — Patient Support Services Provided
- `PATIENT_DEAD_ON_ARRIVAL` — Patient Dead On Arrival

---

# Medical Patient Status

NERIS value set: `TypeMedicalPatientStatusValue` (3 values)
- `IMPROVED` — Improved
- `UNCHANGED` — Unchanged
- `WORSE` — Worse

---

# Medical Transport Disposition

NERIS value set: `TypeMedicalTransportValue` (5 values)
- `TRANSPORT_BY_EMS_UNIT` — Transport By Ems Unit
- `OTHER_AGENCY_TRANSPORT` — Other Agency Transport
- `PATIENT_REFUSED_TRANSPORT` — Patient Refused Transport
- `NONPATIENT_TRANSPORT` — Nonpatient Transport
- `NO_TRANSPORT` — No Transport

---

# Casualty Activity

NERIS value set: `TypeCasualtyActionValue` (13 values)
- `SEARCH_RESCUE` — Search Rescue
- `CARRYING_SETTINGUP_EQUIPMENT` — Carrying Settingup Equipment
- `ADVANCING_OPERATING_HOSELINE` — Advancing Operating Hoseline
- `VEHICLE_EXTRICATION` — Vehicle Extrication
- `VENTILATION` — Ventilation
- `FORCIBLE_ENTRY` — Forcible Entry
- `PUMP_OPERATIONS` — Pump Operations
- `EMS_PATIENT_CARE` — Ems Patient Care
- `DURING_INCIDENT_RESPONSE` — During Incident Response
- `SCENE_SAFETY_DIRECTING_TRAFFIC` — Scene Safety Directing Traffic
- `STANDBY` — Standby
- `INCIDENT_COMMAND` — Incident Command
- `OTHER` — Other

---

# Casualty Cause

NERIS value set: `TypeCasualtyCauseValue` (9 values)
- `CAUGHT_TRAPPED_BY_FIRE_EXPLOSION` — Caught Trapped By Fire Explosion
- `FALL_JUMP` — Fall Jump
- `STRESS_OVEREXERTION` — Stress Overexertion
- `COLLAPSE` — Collapse
- `CAUGHT_TRAPPED_BY_OBJECT` — Caught Trapped By Object
- `STRUCK_CONTACT_WITH_OBJECT` — Struck Contact With Object
- `EXPOSURE` — Exposure
- `VEHICLE_COLLISION` — Vehicle Collision
- `OTHER` — Other

---

# Casualty PPE

NERIS value set: `TypeCasualtyPpeValue` (14 values)
- `TURNOUT_COAT` — Turnout Coat
- `BUNKER_PANTS` — Bunker Pants
- `PROTECTIVE_HOOD` — Protective Hood
- `GLOVES` — Gloves
- `FACE_SHIELD_GOGGLES` — Face Shield Goggles
- `HELMET` — Helmet
- `SCBA` — Scba
- `PASS_DEVICE` — Pass Device
- `RUBBER_KNEE_BOOTS` — Rubber Knee Boots
- `3_4_BOOTS` — 3 4 Boots
- `BRUSH_GEAR` — Brush Gear
- `REFLECTIVE_VEST` — Reflective Vest
- `OTHER_SPECIAL_EQUIPMENT` — Other Special Equipment
- `NONE` — None

---

# Casualty Timeline

NERIS value set: `TypeCasualtyTimelineValue` (6 values)
- `RESPONDING` — Responding
- `INITIAL_RESPONSE` — Initial Response
- `CONTINUING_OPERATIONS` — Continuing Operations
- `EXTENDED_OPERATIONS` — Extended Operations
- `AFTER_CONCLUSION_OF_INCIDENT` — After Conclusion Of Incident
- `UNKNOWN` — Unknown

---

# Rescue Actions

NERIS value set: `TypeRescueActionValue` (9 values)
- `VENTILATION` — Ventilation
- `HYDRAULIC_TOOL_USE` — Hydraulic Tool Use
- `UNDERWATER_DIVE` — Underwater Dive
- `ROPE_RIGGING` — Rope Rigging
- `BREAK_BREACH_WALL` — Break Breach Wall
- `BRACE_WALL_INFRASTRUCTURE` — Brace Wall Infrastructure
- `TRENCH_SHORING` — Trench Shoring
- `SUPPLY_AIR` — Supply Air
- `NONE` — None

---

# Rescue Elevation

NERIS value set: `TypeRescueElevationValue` (4 values)
- `ON_FLOOR` — On Floor
- `ON_BED` — On Bed
- `ON_FURNITURE` — On Furniture
- `OTHER` — Other

---

# Rescue Impediment

NERIS value set: `TypeRescueImpedimentValue` (6 values)
- `HOARDING_CONDITIONS` — Hoarding Conditions
- `ACCESS_LIMITATIONS` — Access Limitations
- `PHYSICAL_MEDICAL_CONDITIONS_PERSON` — Physical Medical Conditions Person
- `IMPAIRED_PERSON` — Impaired Person
- `OTHER` — Other
- `NONE` — None

---

# Rescue Mode

NERIS value set: `TypeRescueModeValue` (5 values)
- `REMOVAL_FROM_STRUCTURE` — Removal From Structure
- `EXTRICATION` — Extrication
- `DISENTANGLEMENT` — Disentanglement
- `RECOVERY` — Recovery
- `OTHER` — Other
