from flask import Flask, request, jsonify
from influxdb import InfluxDBClient
import sys
from datetime import datetime
import pytz

from env import (
    INFLUXDB_URL,
    INFLUXDB_USER,
    INFLUXDB_PASS,
)


influx_client = InfluxDBClient(
    host=INFLUXDB_URL, username=INFLUXDB_USER, password=INFLUXDB_PASS
)

app = Flask(__name__)


@app.route("/influx/<database>/write", methods=["POST"])
def write_influxdb(database):
    data = request.get_json()
    data_points = data.get("data_points", [])
    try:
        write_influxdb(database, data_points)
        return jsonify(dict(success=True, message="Successfully written"))
    except Exception as e:
        return jsonify(dict(success=False, message=str(e)))


@app.route("/influx/latest_data", methods=["GET"])
def get_curent_data():
    tz = pytz.timezone("America/Denver")
    now = datetime.now(tz)
    start_of_day = datetime(now.year, now.month, now.day)
    start_of_day_ms = int(start_of_day.timestamp() * 1000)

    influx_client.switch_database("solar_edge")
    latest_power_query = f'SELECT last("value") FROM "sensor__power_production" WHERE time >= {start_of_day_ms}ms and time <= now()'
    total_energy_query = f'SELECT sum("value") FROM "sensor__energy_production" WHERE time >= {start_of_day_ms}ms and time <= now()'

    latest_power_results = list(influx_client.query(latest_power_query).get_points())[0]
    total_energy_results = list(influx_client.query(total_energy_query).get_points())[0]

    last_update = datetime.fromisoformat(latest_power_results["time"])

    power_watts = latest_power_results["last"]
    power_kw = f"{power_watts/1000:.2f}"

    energy_watts = total_energy_results["sum"]
    energy_kwh = f"{energy_watts/1000:.2f}"

    return jsonify(
        dict(
            power=power_kw,
            energy=energy_kwh,
            last_updated=last_update.astimezone(tz=tz).isoformat(),
        )
    )


def write_influxdb(database, data_points):
    influx_client.create_database(database)
    influx_client.write_points(data_points, database=database, batch_size=10000)
