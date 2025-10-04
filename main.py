import json
import os
from datetime import datetime

import googlemaps
from dotenv import load_dotenv


class TrafficMonitor:
    """
    https://github.com/googlemaps/google-maps-services-python?tab=readme-ov-file
    """

    def __init__(self, api_key: str):
        self.gmaps = googlemaps.Client(key=api_key)

    def get_traffic_data(self, origin: str, destination: str):
        # Request directions via public transit
        directions_result = self.gmaps.distance_matrix(
            origin,
            destination,
            mode="driving",
            departure_time="now",
            traffic_model="pessimistic",
        )
        origin_address = directions_result["origin_addresses"][0]
        destination_address = directions_result["destination_addresses"][0]
        clear_duration_secs = directions_result["rows"][0]["elements"][0]["duration"]["value"]
        traffic_duration_secs = directions_result["rows"][0]["elements"][0]["duration_in_traffic"]["value"]

        return {
            "query_time": datetime.now().isoformat(),
            "origin": origin_address,
            "destination": destination_address,
            "clear_duration_mins": clear_duration_secs / 60,
            "traffic_duration_mins": traffic_duration_secs / 60,
        }


def main():
    load_dotenv()  # take environment variables
    google_api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    traffic_monitor = TrafficMonitor(api_key=google_api_key)
    traff = traffic_monitor.get_traffic_data(
        "164 Devonshire Road, London SE23 3SZ",
        "Rosemead Preparatory School, 70 Thurlow Park Road, London SE21 8HZ",
    )

    output_filenme = "traffic_report.jsonl"
    # append this result to the file, which will be creart4d if it doesn't exist
    with open(output_filenme, "a") as f:
        f.write(f"{json.dumps(traff)}\n")
    print(f"Appended traffic data to {output_filenme}")


if __name__ == "__main__":
    main()
