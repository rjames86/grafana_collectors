from influxdb import InfluxDBClient
from os import environ
from activities import create_activities
from pins import create_pins

from august_api import create_client, Houses

INFLUXDB_URL = environ.get("WEATHERFLOW_COLLECTOR_INFLUXDB_URL")
INFLUXDB_USERNAME = environ.get("WEATHERFLOW_COLLECTOR_INFLUXDB_USERNAME")
INFLUXDB_PASSWORD = environ.get("WEATHERFLOW_COLLECTOR_INFLUXDB_PASSWORD")

api = create_client()

client = InfluxDBClient(
    host=INFLUXDB_URL,
    port=8086,
    username=INFLUXDB_USERNAME,
    password=INFLUXDB_PASSWORD,
    database="august_data",
)
client.create_database("august_data")

json_body = []


def create_measurement(lock_detail, houses, type="lock"):
    return dict(
        measurement="augustLockBattery",
        tags=dict(
            name=lock_detail.device_name,
            house=houses.get_house_name_by_id(lock_detail.house_id),
            lock_id=lock_detail.device_id,
            type=type,
        ),
        fields=dict(battery_level=lock_detail.battery_level),
    )


class LockDetails(list):
    def get_house_id_by_device_id(self, device_id):
        for ld in self:
            if ld.device_id == device_id:
                return ld.house_id
        return None

def get_lock_details():
    locks = api.get_locks(api.access_token)
    return [api.get_lock_detail(api.access_token, lock.device_id) for lock in locks]


lock_details = get_lock_details()
houses = Houses(api.get_houses(api.access_token))

for lock_detail in lock_details:
    measurement = create_measurement(lock_detail, houses)
    print("Creating data", measurement)
    json_body.append(create_measurement(lock_detail, houses))

    if lock_detail.keypad:
        keypad_measurement = create_measurement(lock_detail.keypad, houses, "keypad")
        print("Creating keypad data", keypad_measurement)
        json_body.append(keypad_measurement)

house_activity_measurements = create_activities(api, houses, lock_details)
pins_measurements = create_pins(api, houses)

json_body.extend(house_activity_measurements)
json_body.extend(pins_measurements)

client.write_points(json_body)
