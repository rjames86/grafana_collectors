from influxdb import InfluxDBClient
from august.api import Api
from august.authenticator import Authenticator
from os import environ

INFLUXDB_URL = environ.get('WEATHERFLOW_COLLECTOR_INFLUXDB_URL')
INFLUXDB_USERNAME = environ.get('WEATHERFLOW_COLLECTOR_INFLUXDB_USERNAME')
INFLUXDB_PASSWORD = environ.get('WEATHERFLOW_COLLECTOR_INFLUXDB_PASSWORD')

AUGUST_USERNAME = environ.get('AUGUST_USERNAME')
AUGUST_PASSWORD = environ.get('AUGUST_PASSWORD')

api = Api(timeout=20)
authenticator = Authenticator(api, "email", AUGUST_USERNAME, AUGUST_PASSWORD,
                              access_token_cache_file="auth_cache")

client = InfluxDBClient(host=INFLUXDB_URL, port=8086, username=INFLUXDB_USERNAME, password=INFLUXDB_PASSWORD, database="august_data")
client.create_database("august_data")

authentication = authenticator.authenticate()

DUNCAN_HOUSE_ID = "3f040bfd-acd0-4b2a-b633-d4fc1539d5a4"
CENTRAL_HOUSE_ID = "06f1006e-6aa6-41ad-bf51-e2744f4ca80d"

state = authentication.state

authentication = authenticator.authenticate()

locks = api.get_locks(authentication.access_token)

json_body = []


def create_measurement(lock_detail):
    return dict(
        measurement="augustLockBattery",
        tags=dict(
            name=lock_detail.device_name,
            house="Duncan" if lock_detail.house_id == DUNCAN_HOUSE_ID else "Central",
            lock_id=lock_detail.device_id,
        ),
        fields=dict(
            battery_level=lock_detail.battery_level
        )
    )


for lock in locks:
    lock_detail = api.get_lock_detail(
        authentication.access_token, lock.device_id)
    print("Creating data for {0}".format(lock_detail.device_id))
    json_body.append(create_measurement(lock_detail))

client.write_points(json_body)
