import json
import os
from datetime import datetime

import googlemaps
import pandas as pd
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


def plot_to_png(jsonl_filename: str, output_png: str):
    df = pd.read_json(jsonl_filename, lines=True)
    df["query_time"] = pd.to_datetime(df["query_time"])
    df = df.set_index("query_time")
    ax = df[["clear_duration_mins", "traffic_duration_mins"]].plot(
        title="Traffic Duration Over Time",
        ylabel="Duration (minutes)",
        xlabel="Time",
        figsize=(10, 6),
    )
    ax.grid(True)
    fig = ax.get_figure()
    fig.savefig(output_png)
    print(f"Saved plot to {output_png}")


def main():
    load_dotenv()  # take environment variables
    google_api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    traffic_monitor = TrafficMonitor(api_key=google_api_key)
    response = traffic_monitor.get_traffic_data(
        "164 Devonshire Road, London SE23 3SZ",
        "Rosemead Preparatory School, 70 Thurlow Park Road, London SE21 8HZ",
    )

    output_jsonl_filename = "traffic_report.jsonl"
    # append this result to the file, which will be creart4d if it doesn't exist
    with open(output_jsonl_filename, "a") as f:
        f.write(f"{json.dumps(response)}\n")
    print(f"Appended traffic data to {output_jsonl_filename}")
    plot_to_png(output_jsonl_filename, "traffic_report.png")


if __name__ == "__main__":
    main()
