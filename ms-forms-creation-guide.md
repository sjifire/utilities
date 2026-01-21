# Microsoft Forms Manual Creation Instructions

## Form: Fire Incident Report

Incident report form based on ESO NFIRS incident reporting structure

---

## Section: Basic Information

*Core incident identification and timing*

### 1. Incident Number

- **Type:** text
- **Required:** Yes
- **Description/Help text:** Format: YY-NNNNNN (e.g., 26-000944)

### 2. Incident Date

- **Type:** date
- **Required:** Yes

### 3. NFIRS Number

- **Type:** text
- **Required:** No

### 4. Incident Type

- **Type:** choice
- **Required:** Yes
- **Choices:**
  - 111 - Building fire
  - 112 - Fires in structure other than building
  - 113 - Cooking fire, confined to container
  - 114 - Chimney or flue fire, confined
  - 115 - Incinerator overload or malfunction
  - 116 - Fuel burner/boiler malfunction, fire confined
  - 117 - Commercial compactor fire, confined
  - 118 - Trash or rubbish fire, contained
  - 120 - Fire in mobile property used as a fixed structure
  - 121 - Fire in mobile home used as fixed residence
  - 122 - Fire in motor home, camper, recreational vehicle
  - 123 - Fire in portable building, fixed location
  - 130 - Mobile property (vehicle) fire, other
  - 131 - Passenger vehicle fire
  - 132 - Road freight or transport vehicle fire
  - 133 - Rail vehicle fire
  - 134 - Water vehicle fire
  - 135 - Aircraft fire
  - 136 - Self-propelled motor home fire
  - 137 - Camper or RV fire
  - *(... and 138 more options)*

### 5. Initial Dispatch Code

- **Type:** text
- **Required:** No

### 6. Working Fire

- **Type:** choice
- **Required:** No
- **Choices:**
  - Yes
  - No

### 7. COVID-19 Factor

- **Type:** choice
- **Required:** No
- **Choices:**
  - Yes
  - No

### 8. Critical Incident

- **Type:** choice
- **Required:** No
- **Choices:**
  - Yes
  - No

### 9. Critical Incident Team Mobilized

- **Type:** choice
- **Required:** No
- **Choices:**
  - Yes
  - No

### 10. Temporary Resident Involvement

- **Type:** choice
- **Required:** No
- **Choices:**
  - Yes
  - No

### 11. Number of Alarms

- **Type:** text
- **Required:** No

## Section: Station and Location

*Station assignment and incident location details*

### 12. Station

- **Type:** choice
- **Required:** Yes
- **Choices:**
  - (31) - Station 31
  - (32) - Station 32
  - (33) - Station 33
  - (34) - Station 34

### 13. Shift

- **Type:** choice
- **Required:** No
- **Choices:**
  - A
  - B
  - C
  - Day

### 14. District

- **Type:** text
- **Required:** No

### 15. Location Type

- **Type:** choice
- **Required:** No
- **Choices:**
  - Address
  - Intersection
  - Landmark

### 16. Address

- **Type:** text
- **Required:** Yes

### 17. Latitude

- **Type:** text
- **Required:** No

### 18. Longitude

- **Type:** text
- **Required:** No

### 19. Property Use

- **Type:** choice
- **Required:** No
- **Choices:**
  - 000 - Property Use, other
  - 100 - Assembly, other
  - 110 - Fixed-use amusement or recreation place
  - 111 - Bowling establishment
  - 112 - Billiard center
  - 113 - Electronic amusement center
  - 114 - Ice rink
  - 115 - Roller rink
  - 116 - Swimming facility
  - 120 - Variable-use amusement, recreation place
  - 121 - Ballroom, gymnasium
  - 122 - Convention center, exhibition hall
  - 123 - Stadium, arena
  - 124 - Playground
  - 129 - Amusement center
  - 130 - Church, place of worship
  - 131 - Church, place of worship
  - 140 - Club
  - 141 - Athletic or health club
  - 142 - Civic, fraternal club
  - *(... and 123 more options)*

### 20. Census Tract

- **Type:** text
- **Required:** No

## Section: Incident Times

*Key timestamps during the incident*

### 21. Fire Discovery Date

- **Type:** date
- **Required:** No

### 22. Fire Discovery Time

- **Type:** text
- **Required:** No
- **Description/Help text:** Format: HH:MM:SS

### 23. PSAP Received Date

- **Type:** date
- **Required:** No

### 24. PSAP Received Time

- **Type:** text
- **Required:** No

### 25. Dispatch Notified Date

- **Type:** date
- **Required:** No

### 26. Dispatch Notified Time

- **Type:** text
- **Required:** No

### 27. Alarm Date

- **Type:** date
- **Required:** Yes

### 28. Alarm Time

- **Type:** text
- **Required:** Yes

### 29. Arrival Date

- **Type:** date
- **Required:** Yes

### 30. Arrival Time

- **Type:** text
- **Required:** Yes

### 31. Incident Controlled Date

- **Type:** date
- **Required:** No

### 32. Incident Controlled Time

- **Type:** text
- **Required:** No

### 33. Last Unit Cleared Date

- **Type:** date
- **Required:** Yes

### 34. Last Unit Cleared Time

- **Type:** text
- **Required:** Yes

## Section: Actions and Aid

*Actions taken and mutual aid information*

### 35. Action Taken 1

- **Type:** choice
- **Required:** Yes
- **Choices:**
  - 00 - Action taken, other
  - 10 - Fire control or extinguishment, other
  - 11 - Extinguishment by fire service personnel
  - 12 - Salvage & overhaul
  - 13 - Establish fire lines
  - 14 - Search
  - 15 - Rescue, remove from harm
  - 16 - Fires, rescues, hazardous conditions, other
  - 17 - Confine fire
  - 20 - Search & rescue, other
  - 21 - Search
  - 22 - Rescue, remove from harm
  - 23 - Extricate, disentangle
  - 24 - Recover body
  - 25 - Search/rescue using dive team
  - 26 - Provide basic life support (BLS)
  - 27 - Provide advanced life support (ALS)
  - 30 - EMS & transport, other
  - 31 - Provide basic life support (BLS)
  - 32 - Provide advanced life support (ALS)
  - *(... and 53 more options)*

### 36. Action Taken 2

- **Type:** choice
- **Required:** No
- **Choices:**
  - (Same as Action Taken 1)

### 37. Action Taken 3

- **Type:** choice
- **Required:** No
- **Choices:**
  - (Same as Action Taken 1)

### 38. Aid Given / Received

- **Type:** choice
- **Required:** No
- **Choices:**
  - N - None
  - 1 - Mutual aid received
  - 2 - Automatic aid received
  - 3 - Mutual aid given
  - 4 - Automatic aid given
  - 5 - Other aid given

### 39. Aided Agency

- **Type:** text
- **Required:** No

### 40. Their Incident Number

- **Type:** text
- **Required:** No

## Section: Resources

*Apparatus and personnel counts*

### 41. Suppression Apparatus Count

- **Type:** text
- **Required:** No

### 42. Suppression Personnel Count

- **Type:** text
- **Required:** No

### 43. EMS Apparatus Count

- **Type:** text
- **Required:** No

### 44. EMS Personnel Count

- **Type:** text
- **Required:** No

### 45. Other Apparatus Count

- **Type:** text
- **Required:** No

### 46. Other Personnel Count

- **Type:** text
- **Required:** No

## Section: Losses and Values

*Property and content loss estimates*

### 47. Estimated Property Losses

- **Type:** text
- **Required:** Yes
- **Description/Help text:** Dollar amount or 'None'

### 48. Estimated Property Value

- **Type:** text
- **Required:** No

### 49. Estimated Content Losses

- **Type:** text
- **Required:** Yes
- **Description/Help text:** Dollar amount or 'None'

### 50. Estimated Contents Value

- **Type:** text
- **Required:** No

## Section: Report Authorization

*Report writer and authorization information*

### 51. Report Writer

- **Type:** text
- **Required:** Yes

### 52. Officer In Charge

- **Type:** text
- **Required:** Yes

### 53. Authorization Date

- **Type:** date
- **Required:** No

## Section: Unit Reports

*Individual unit response information (repeat for each unit)*

> **Note:** This section is repeatable. Create multiple copies for each unit.

### 54. Unit/Apparatus Name

- **Type:** choice
- **Required:** Yes
- **Choices:**
  - BN31 - Battalion 31
  - E31 - Engine 31
  - E32 - Engine 32
  - E33 - Engine 33
  - L31 - Ladder 31
  - M31 - Medic 31
  - M32 - Medic 32
  - OPS31 - Operations 31
  - FB31 - Fireboat 31
  - POV - Private Vehicle
  - R31 - Rescue 31
  - T31 - Tender 31
  - BR31 - Brush 31

### 55. Unit Type

- **Type:** choice
- **Required:** Yes
- **Choices:**
  - SUPPRESSION
  - EMS
  - OTHER

### 56. Response Priority

- **Type:** choice
- **Required:** Yes
- **Choices:**
  - EMERGENT
  - NON-EMERGENT

### 57. Dispatch Time

- **Type:** text
- **Required:** Yes
- **Description/Help text:** Format: HH:MM:SS

### 58. Enroute Time

- **Type:** text
- **Required:** No

### 59. Arrival Time

- **Type:** text
- **Required:** No

### 60. At Patient Time

- **Type:** text
- **Required:** No

### 61. Clear Time

- **Type:** text
- **Required:** Yes

### 62. In District Time

- **Type:** text
- **Required:** No

### 63. Personnel on Unit

- **Type:** text
- **Required:** No
- **Description/Help text:** Comma-separated list of names

## Section: Fire Module

*Fire-specific information (required for fire incidents)*

### 64. Area of Fire Origin

- **Type:** choice
- **Required:** No
- **Choices:**
  - 00 - Area of origin undetermined
  - 01 - Hallway, corridor, mall
  - 02 - Exterior stairway
  - 03 - Interior stairway
  - 04 - Escalator
  - 05 - Entrance, lobby
  - 06 - Chute: laundry, trash, mail
  - 07 - Exterior balcony, unenclosed porch
  - 08 - Open egress
  - 09 - Chimney
  - 10 - Means of egress, other
  - 20 - Assembly, sales areas, other
  - 21 - Large assembly area with fixed seats: > 100
  - 22 - Large assembly area without fixed seats: > 100
  - 23 - Small assembly area with fixed seats: < 100
  - 24 - Small assembly area without fixed seats: < 100
  - 25 - Common room, lounge, living room, den
  - 26 - Specialty area: stage, press box, projection room
  - 27 - Sales or showroom area
  - 30 - Function areas, other
  - *(... and 56 more options)*

### 65. Heat Source

- **Type:** choice
- **Required:** No
- **Choices:**
  - 00 - Heat source undetermined
  - 10 - Operating equipment, other
  - 11 - Spark, ember, flame from operating equipment
  - 12 - Radiated, conducted heat from operating equipment
  - 13 - Electrical arcing
  - 40 - Hot or smoldering object, other
  - 41 - Heat, spark from friction
  - 42 - Molten, hot material
  - 43 - Hot ember or ash
  - 50 - Explosives, fireworks, other
  - 51 - Munitions
  - 52 - Blasting agent, explosive
  - 53 - Fireworks
  - 54 - Incendiary device
  - 60 - Other heat source
  - 61 - Chemical reaction
  - 62 - Spontaneous combustion
  - 63 - Sun's heat
  - 64 - Lightning
  - 65 - Re-kindling from prior fire
  - *(... and 4 more options)*

### 66. Item First Ignited

- **Type:** choice
- **Required:** No
- **Choices:**
  - 00 - Item first ignited undetermined
  - 10 - Structural component, finish, other
  - 11 - Ceiling cover/finish
  - 12 - Wall covering, surface, finish
  - 13 - Floor covering, rug, carpet, mat
  - 14 - Interior wall covering
  - 15 - Structural member or framing
  - 16 - Thermal, acoustic insulation within structural
  - 17 - Exterior sidewall covering, surface, finish
  - 18 - Exterior roof covering, finish, membrane
  - 19 - Exterior trim, including doors & windows
  - 20 - Furniture, utensils, other
  - 21 - Upholstered furniture, chair, sofa
  - 22 - Unupholstered furniture
  - 23 - Cabinetry
  - 24 - Appliance housing
  - 25 - Electrical or electronic equipment
  - 30 - Soft goods, wearing apparel, other
  - 31 - Mattress, pillow
  - 32 - Bedding: blankets, sheets, comforter
  - *(... and 31 more options)*

### 67. Cause of Ignition

- **Type:** choice
- **Required:** No
- **Choices:**
  - 1 - Intentional
  - 2 - Unintentional
  - 3 - Failure of equipment or heat source
  - 4 - Act of nature
  - 5 - Cause under investigation
  - U - Cause undetermined after investigation

### 68. Fire Spread

- **Type:** choice
- **Required:** No
- **Choices:**
  - 1 - Confined to object of origin
  - 2 - Confined to room of origin
  - 3 - Confined to floor of origin
  - 4 - Confined to building of origin
  - 5 - Beyond building of origin

### 69. Structure Type

- **Type:** choice
- **Required:** No
- **Choices:**
  - 1 - Enclosed building
  - 2 - Fixed portable or mobile structure
  - 3 - Open structure
  - 4 - Vehicle
  - 5 - Outdoor open storage
  - 0 - Not classified

### 70. Detector Alerted Occupants

- **Type:** choice
- **Required:** No
- **Choices:**
  - 1 - Yes
  - 2 - No
  - U - Undetermined

---

## Creating the Form in Microsoft Forms

1. Go to https://forms.microsoft.com
2. Click "New Form"
3. Set the title to: "Fire Incident Report"
4. Add each question following the specifications above
5. For dropdown/choice questions, enter all the options listed
6. Mark required questions as required
7. Use sections to organize the form (Forms > Add Section)
8. Save and share the form

## Tips for Dropdown Menus with Many Options

For questions with many choices (like Incident Type, Property Use, Area of Fire Origin):
- Use the "Add options in bulk" feature in Microsoft Forms
- Copy the choices from the specification and paste them all at once
- Each choice should be on its own line

