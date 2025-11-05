#!/usr/bin/env python3

import urllib.request
import json
import os
import sys
import datetime
import requests

API_KEY_PATH = "/var/lib/purpleair/apiKey"
URL_TEMPLATE = "https://api.purpleair.com/v1/sensors/{sensor_id}?api_key={api_key}&fields=name%2Cpm1.0_atm%2Cpm2.5_atm%2Cpm10.0_atm"
TEST_JSON = None

def average_round_pm(sensors, value_name):
    total = sum(float(sensor[value_name]) for sensor in sensors if sensor and sensor.get(value_name) is not None)
    count = sum(1 for sensor in sensors if sensor and sensor.get(value_name) is not None)
    return round(total / count, 2) if count > 0 else 0

def create_api_data_points(results):
    time = datetime.datetime.fromtimestamp(results['data_time_stamp'], tz=datetime.timezone.utc)
    sensor = results['sensor']

    tags = {
        "location": "Outside",
        "host": sensor['name'],
        "sensor": "PurpleAir",
    }

    # Create separate data points for each PM measurement
    data_points = []

    # PM 1.0
    data_points.append({
        "measurement": "airquality",
        "tags": tags,
        "fields": {"pm10": float(sensor['pm1.0_atm'])},
        "time": time.isoformat()
    })

    # PM 2.5
    data_points.append({
        "measurement": "airquality",
        "tags": tags,
        "fields": {"pm25": float(sensor['pm2.5_atm'])},
        "time": time.isoformat()
    })

    # PM 10.0
    data_points.append({
        "measurement": "airquality",
        "tags": tags,
        "fields": {"pm100": float(sensor['pm10.0_atm'])},
        "time": time.isoformat()
    })

    return data_points

def get_env_variable(name):
    value = os.environ.get(name)
    if value is None:
        print(f"ERROR: {name} environment variable is not defined. Exiting")
        sys.exit(1)
    return value

def get_api_key(path):
    try:
        with open(path, 'r') as file:
            return file.readline().strip()
    except FileNotFoundError:
        print(f"ERROR: API key does not exist at {path}. Exiting")
        sys.exit(1)

def fetch_data(url):
    with urllib.request.urlopen(url) as req:
        return json.loads(req.read().decode())

def main():
    sensor_id = get_env_variable('SENSOR_ID')
    api_key = get_api_key(API_KEY_PATH)
    full_url = URL_TEMPLATE.format(sensor_id=sensor_id, api_key=api_key)

    data = fetch_data(full_url) if TEST_JSON is None else json.loads(TEST_JSON)

    if not data or 'sensor' not in data:
        print(f"No valid data returned: {data}")
        sys.exit(0)

    # Get database name for API endpoint
    influx_db = get_env_variable("INFLUX_DB")

    # Create data points for API
    data_points = create_api_data_points(data)

    print(f"Fetched sensor values from PurpleAir: \n{data}")
    print(f"API data points: \n{data_points}")

    # Send to your API
    api_payload = {
        "data_points": data_points,
        "verbose": False
    }

    try:
        response = requests.post(
            f'http://api:5000/influx/{influx_db}/write',
            json=api_payload
        )
        response.raise_for_status()
        print(f"Successfully wrote data to API: {response.json()}")
    except requests.exceptions.RequestException as e:
        print(f"Error writing to API: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
