import time

LIMIT = 50


class GrafanaActivity:
    def __init__(self, house_activity, houses, lock_details):
        self.house_activity = house_activity
        self.houses = houses
        self.lock_details = lock_details

    @property
    def activity_house_id(self):
        return self.lock_details.get_house_id_by_device_id(self.house_activity.device_id)

    @property
    def house_name(self):
        if self.activity_house_id is None:
            return 'Unknown'
        return self.houses.get_house_name_by_id(self.activity_house_id)

    def create_measurement(self):
        return dict(
            measurement="augustActivities",
            time=self.house_activity._activity_time.isoformat(),
            tags=dict(
                house=self.house_name,
                house_id=self.activity_house_id,
                activity_type=self.house_activity.activity_type.name,
                device_name=self.house_activity.device_name,
                device_type=self.house_activity.device_type,
            ),
            fields=dict(
                activity_id=self.house_activity.activity_id,
                activity_start_time=self.house_activity.activity_start_time.isoformat(),
                activity_end_time=self.house_activity.activity_end_time.isoformat(),
                action=self.house_activity.action,
                device_id=self.house_activity.device_id,
                operated_by=getattr(self.house_activity, 'operated_by', None),
                operated_remote=getattr(
                    self.house_activity, 'operated_remote', None),
                operated_keypad=getattr(
                    self.house_activity, 'operated_keypad', None),
                operated_autorelock=getattr(
                    self.house_activity, 'operated_autorelock', None),
            )
        )


def create_activities(client, houses, lock_details):
    json_data = []
    for house in houses:
        house_activities = client.get_house_activities(
            client.access_token, house.house_id, LIMIT)
        grafana_activities = [GrafanaActivity(
            ha, houses, lock_details) for ha in house_activities]
        for ga in grafana_activities:
            json_data.append(ga.create_measurement())
    return json_data
