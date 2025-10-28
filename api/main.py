from flask import Flask, request, jsonify
from influxdb import InfluxDBClient as InfluxDBV1Client
from influxdb_client import InfluxDBClient as InfluxDBV2Client, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from datetime import datetime
import pytz
import requests
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

# ---------------------------
# Influx clients
# ---------------------------
influxV1_client = InfluxDBV1Client(
    host=INFLUXDB_V1_URL, username=INFLUXDB_V1_USER, password=INFLUXDB_V1_PASS
)

influxV2_client = InfluxDBV2Client(
    url=INFLUXDB_V2_URL, token=INFLUXDB_V2_TOKEN, org=INFLUXDB_V2_ORG
)
write_api_v2 = influxV2_client.write_api(write_options=SYNCHRONOUS)
buckets_api = influxV2_client.buckets_api()

# ---------------------------
# Flask app & logger
# ---------------------------
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------
# Helper for InfluxDB v2
# ---------------------------
def ensure_bucket(bucket_name: str):
    """Ensure InfluxDB v2 bucket exists; create if missing."""
    buckets = buckets_api.find_buckets().buckets
    bucket = next((b for b in buckets if b.name == bucket_name), None)
    if not bucket:
        logger.info(f"Bucket '{bucket_name}' not found. Creating it...")
        bucket = buckets_api.create_bucket(bucket_name=bucket_name, org=INFLUXDB_V2_ORG)
    return bucket

def write_influxdb_v2(bucket_name: str, data_points: list):
    """Write data points to InfluxDB v2, ensuring bucket exists."""
    ensure_bucket(bucket_name)
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

    logger.info(f"Writing {len(points)} points to InfluxDB v2 bucket '{bucket_name}'")
    write_api_v2.write(bucket=bucket_name, org=INFLUXDB_V2_ORG, record=points)

# ---------------------------
# Flask routes
# ---------------------------
@app.route("/health", methods=["GET"])
def health_check():
    return jsonify(dict(status="ok", message="API is running")), 200

@app.route("/influx/<database>/write", methods=["POST"])
def write_influxdb_post(database):
    data = request.get_json()
    data_points = data.get("data_points", [])
    verbose = data.get("verbose", False)
    use_v1_legacy = data.get("use_v1_legacy", False)  # New parameter for legacy v1 support

    if verbose:
        logger.info(f"Data points received: {data_points}")

    # Default to InfluxDB v2 (new behavior)
    if not use_v1_legacy:
        try:
            logger.info(f"Writing to InfluxDB v2 bucket '{database}'")
            write_influxdb_v2(database, data_points)
            return jsonify(dict(success=True, message="Successfully written to InfluxDB v2", version="v2"))
        except Exception as e:
            logger.error(f"Failed to write to InfluxDB v2: {e}")
            return jsonify(dict(success=False, message=f"InfluxDB v2 write failed: {str(e)}", version="v2")), 500

    # Legacy InfluxDB v1 support (only when explicitly requested)
    else:
        try:
            logger.info(f"Writing to InfluxDB v1 database '{database}' (legacy mode)")
            write_influxdb_v1(database, data_points)
            return jsonify(dict(success=True, message="Successfully written to InfluxDB v1 (legacy)", version="v1"))
        except Exception as e:
            logger.error(f"Failed to write to InfluxDB v1: {e}")
            return jsonify(dict(success=False, message=f"InfluxDB v1 write failed: {str(e)}", version="v1")), 500

@app.route("/influx/latest_data", methods=["GET"])
def get_current_data():
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

    power_kw = f"{latest_power_results['last']/1000:.2f}"
    energy_kwh = f"{total_energy_results['sum']/1000:.2f}"
    consumption_kwh = f"{total_energy_consumption_results['sum']/1000:.2f}"

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

    resp = requests.post(
        "https://api.pushover.net/1/messages.json",
        data={
            "token": PUSHOVER_SPRINKLER_TOKEN,
            "user": PUSHOVER_USER,
            "message": message,
            "title": title,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if resp.status_code != 200:
        return jsonify(dict(success=False, message=f"Failed to send message: {resp.text}", status_code=resp.status_code)), 500
    return jsonify(dict(success=True, message="Message sent successfully"))

# ---------------------------
# InfluxDB v1 write helper
# ---------------------------
def write_influxdb_v1(database, data_points):
    influxV1_client.create_database(database)
    influxV1_client.write_points(data_points, database=database, batch_size=10000)

# ---------------------------
# Flask app entry point
# ---------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
