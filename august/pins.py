import time

LIMIT = 50


class GrafanaActivity:
    def __init__(self, pin, lock, houses):
        self.pin = pin
        self.houses = houses
        self.lock = lock

    def create_measurement(self):
        print("Creating pin", self.pin.pin_id)
        return dict(
            measurement="augustPins",
            time=self.pin.created_at.isoformat(),
            tags=dict(
                house=self.houses.get_house_name_by_id(
                    self.lock.house_id),
                house_id=self.lock.house_id,
                lock_id=self.lock.device_id,
                access_type=self.pin.access_type
            ),
            fields=dict(
                state=self.pin.state,
                pin=self.pin.pin,
                first_name=self.pin.first_name,
                last_name=self.pin.last_name,
                updated_at=self.pin.updated_at.isoformat(),
                access_type=self.pin.access_type,
                access_start_time=self.pin.access_start_time.isoformat() if self.pin.access_start_time is not None else None,
                access_end_time=self.pin.access_end_time.isoformat() if self.pin.access_end_time is not None else None,
            )
        )


def create_pins(client, houses):
    json_data = []
    locks = client.get_locks(client.access_token)
    for lock in locks:
        pins = client.get_pins(client.access_token, lock.device_id)
        grafana_activities = [GrafanaActivity(pin, lock, houses) for pin in pins]
        for ga in grafana_activities:
            json_data.append(ga.create_measurement())
    return json_data
