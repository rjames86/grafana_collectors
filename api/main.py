from flask import Flask, request, jsonify
from influxdb import InfluxDBClient

from env import (
    INFLUXDB_URL,
    INFLUXDB_USER,
    INFLUXDB_PASS,
)
 

influx_client = InfluxDBClient(host=INFLUXDB_URL,
                                username=INFLUXDB_USER,
                                password=INFLUXDB_PASS)

app = Flask(__name__)

@app.route("/influx/<database>/write", methods=['POST'])
def write_influxdb(database):
    data = request.get_json()
    data_points = data.get('data_points', [])
    try:
        write_influxdb(database, data_points)
        return jsonify(dict(success=True, message='Successfully written'))
    except Exception as e:
        return jsonify(dict(success=False, message=str(e)))

def write_influxdb(database, data_points):
    influx_client.create_database(database)
    influx_client.write_points(data_points, database=database, batch_size=10000)
