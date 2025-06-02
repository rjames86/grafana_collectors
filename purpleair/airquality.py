#!/usr/bin/env python3

import urllib.request
import json
import os
import sys
import datetime
from influxdb import InfluxDBClient

API_KEY_PATH = "/var/lib/purpleair/apiKey"
URL_TEMPLATE = "https://api.purpleair.com/v1/sensors/{sensor_id}?api_key={api_key}&fields=name%2Cpm1.0_atm%2Cpm2.5_atm%2Cpm10.0_atm"
TEST_JSON = None

def average_round_pm(sensors, value_name):
    total = sum(float(sensor[value_name]) for sensor in sensors if sensor and sensor.get(value_name) is not None)
    count = sum(1 for sensor in sensors if sensor and sensor.get(value_name) is not None)
    return round(total / count, 2) if count > 0 else 0

def create_influx_pm_measurements(results):
    time = datetime.datetime.utcfromtimestamp(results['data_time_stamp'])
    sensor = results['sensor']
    base_measurement = {
        "measurement": "airquality",
        "tags": {
            "location": "Outside",
            "host": sensor['name'],
            "sensor": "PurpleAir",
        },
        "time": time
    }
    pm10 = {**base_measurement, "fields": {"pm10": float(sensor['pm1.0_atm'])}}
    pm25 = {**base_measurement, "fields": {"pm25": float(sensor['pm2.5_atm'])}}
    pm100 = {**base_measurement, "fields": {"pm100": float(sensor['pm10.0_atm'])}}

    return [pm10, pm25, pm100]

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

    influxdb_url = get_env_variable("INFLUXDB_URL")
    influxdb_username = get_env_variable("INFLUXDB_USERNAME")
    influxdb_password = get_env_variable("INFLUXDB_PASSWORD")
    influxdb_name = get_env_variable("INFLUX_DB")

    client = InfluxDBClient(
        host=influxdb_url,
        port=8086,
        username=influxdb_username,
        password=influxdb_password,
        database=influxdb_name,
    )
    client.create_database(influxdb_name)

    influx_data = create_influx_pm_measurements(data)

    print(f"Fetched sensor values from PurpleAir: \n{data}")
    print(f"Influx data is \n{influx_data}")

    client.write_points(influx_data)

if __name__ == "__main__":
    main()
