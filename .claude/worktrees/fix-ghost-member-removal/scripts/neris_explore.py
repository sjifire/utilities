"""Quick exploration script to connect to NERIS and pull entity/incident data."""

import json

from sjifire.neris.client import NerisClient


def main():
    """Pull and display NERIS entity and incident data."""
    with NerisClient() as client:
        # Health check
        print("=== Health Check ===")
        print(client.health())

        # Entity details
        print(f"\n=== Entity: {client.entity_id} ===")
        entity = client.get_entity()
        print(f"Name: {entity['name']}")
        print(f"Type: {entity['department_type']}")
        print(f"Staffing: {json.dumps(entity.get('staffing'), indent=2)}")
        print(f"Stations: {len(entity.get('stations', []))}")
        for station in entity.get("stations", []):
            print(f"  {station['neris_id']}: {station['station_id']}")
            for unit in station.get("units", []):
                print(f"    {unit['neris_id']}: {unit['cad_designation_1']} ({unit['type']})")

        # All incidents
        print("\n=== Incidents ===")
        incidents = client.get_all_incidents()
        print(f"Total: {len(incidents)}")
        for inc in incidents:
            dispatch = inc.get("dispatch", {})
            types = [t["type"] for t in inc.get("incident_types", [])]
            status = inc.get("incident_status", {}).get("status", "N/A")
            print(
                f"  {dispatch.get('incident_number', 'N/A'):>15} | "
                f"{dispatch.get('call_create', 'N/A')[:10]} | "
                f"{status:<18} | "
                f"{types[0] if types else 'N/A'}"
            )


if __name__ == "__main__":
    main()
