from flask import Flask, request, jsonify
from influxdb import InfluxDBClient as InfluxDBV1Client
from influxdb_client import InfluxDBClient as InfluxDBV2Client, Point
from influxdb_client.client.write_api import SYNCHRONOUS
import sys
from datetime import datetime
import pytz
import requests
from os import environ
import logging

from env import (
    INFLUXDB_V1_URL,
    INFLUXDB_V1_USER,
    INFLUXDB_V1_PASS,
    INFLUXDB_V2_URL,
    INFLUXDB_V2_TOKEN,
    INFLUXDB_V2_ORG,
    PUSHOVER_USER,
    PUSHOVER_SPRINKLER_TOKEN,
)


influxV1_client = InfluxDBV1Client(
    host=INFLUXDB_V1_URL, username=INFLUXDB_V1_USER, password=INFLUXDB_V1_PASS
)
influxV2_client = InfluxDBV2Client(url=INFLUXDB_V2_URL, token=INFLUXDB_V2_TOKEN, org=INFLUXDB_V2_ORG)
write_api = influxV2_client.write_api(write_options=SYNCHRONOUS)


app = Flask(__name__)

logger = logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint to verify if the API is running."""
    return jsonify(dict(status="ok", message="API is running")), 200

@app.route("/influx/<database>/write", methods=["POST"])
def write_influxdb_post(database):
    data = request.get_json()
    data_points = data.get("data_points", [])
    verbose = data.get("verbose", False)

    if verbose:
        print(data_points)

    try:
        logger.info("Attempting to write to InfluxDB v2")
        write_influxdb_v2(database, data_points)
    except Exception as e:
        logger.error(f"Failed to write to InfluxDB v2: {e}")
        pass

    try:
        write_influxdb(database, data_points)
        return jsonify(dict(success=True, message="Successfully written"))
    except Exception as e:
        return jsonify(dict(success=False, message=str(e)))


def write_influxdb_v2(bucket, data_points):
    points = []
    for dp in data_points:
        p = Point(dp["measurement"])
        for k, v in dp.get("tags", {}).items():
            p.tag(k, v)
        for k, v in dp.get("fields", {}).items():
            p.field(k, v)
        if "time" in dp:
            p.time(dp["time"])
        points.append(p)

    logger.info(f"Org: {INFLUXDB_V2_ORG}, Bucket: {bucket}")

    write_api.write(bucket=bucket, org=INFLUXDB_V2_ORG, record=points)



@app.route("/influx/latest_data", methods=["GET"])
def get_curent_data():
    tz = pytz.timezone("America/Denver")
    now = datetime.now(tz)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day_ms = int(start_of_day.timestamp() * 1000)

    influxV1_client.switch_database("solar_edge")
    latest_power_query = f'SELECT last("value") FROM "sensor__power_production" WHERE time >= {start_of_day_ms}ms and time <= now()'
    total_energy_query = f'SELECT sum("value") FROM "sensor__energy_production" WHERE time >= {start_of_day_ms}ms and time <= now()'
    total_energy_consumption_query = f'SELECT sum("value") FROM "sensor__energy_consumption" WHERE time >= {start_of_day_ms}ms and time <= now()'

    latest_power_results = list(influxV1_client.query(latest_power_query).get_points())[0]
    total_energy_results = list(influxV1_client.query(total_energy_query).get_points())[0]
    total_energy_consumption_results = list(
        influxV1_client.query(total_energy_consumption_query).get_points()
    )[0]

    last_update = datetime.fromisoformat(latest_power_results["time"])

    power_watts = latest_power_results["last"]
    power_kw = f"{power_watts/1000:.2f}"

    energy_watts = total_energy_results["sum"]
    energy_kwh = f"{energy_watts/1000:.2f}"

    consumption_watts = total_energy_consumption_results["sum"]
    consumption_kwh = f"{consumption_watts/1000:.2f}"

    return jsonify(
        dict(
            power=power_kw,
            energy=energy_kwh,
            consumption=consumption_kwh,
            last_updated=last_update.astimezone(tz=tz).isoformat(),
        )
    )


@app.route("/pushover/sprinkler/message", methods=["POST"])
def send_pushover_message():
    data = request.get_json()
    message = data.get("message", "")
    title = data.get("title", "Alert")

    user_token = PUSHOVER_USER
    app_token = PUSHOVER_SPRINKLER_TOKEN

    resp = requests.post(
        "https://api.pushover.net/1/messages.json",
        data={"token": app_token, "user": user_token, "message": message, "title": title},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code != 200:
        return jsonify(dict(success=False, message=f"Failed to send message: {resp.text}", status_code=resp.status_code)), 500
    return jsonify(dict(success=True, message="Message sent successfully"))


def write_influxdb(database, data_points):
    influxV1_client.create_database(database)
    influxV1_client.write_points(data_points, database=database, batch_size=10000)
