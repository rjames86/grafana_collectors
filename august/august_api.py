from august.api import Api
from august.keypad import KeypadDetail as AugustKeypadDetail
from august.lock import LockDetail as AugustLockDetail
from august.authenticator import Authenticator
from august.api_common import API_GET_HOUSES_URL
from os import environ

AUGUST_USERNAME = environ.get("AUGUST_USERNAME")
AUGUST_PASSWORD = environ.get("AUGUST_PASSWORD")


class KeypadDetail(AugustKeypadDetail):
    def __init__(self, house_id, keypad_name, data):
        super().__init__(house_id, keypad_name, data)
        self._battery_level_raw = data["batteryRaw"]

    @property
    def battery_level(self):
        return self._battery_level_raw


class LockDetail(AugustLockDetail):
    def __init__(self, data):
        super().__init__(data)
        if "keypad" in data:
            keypad_name = data["LockName"] + " Keypad"
            self._keypad_detail = KeypadDetail(
                self.house_id, keypad_name, data["keypad"]
            )
        else:
            self._keypad_detail = None


class House:
    def __init__(self, house_json):
        self.house_id = house_json["HouseID"]
        self.house_name = house_json["HouseName"]

    def __repr__(self) -> str:
        return "House<%r>" % (self.house_name)


class Houses(list):
    def get_house_name_by_id(self, id):
        house_name = "Unknown"
        for house in self:
            if house.house_id == id:
                house_name = house.house_name
        return house_name


class AugustAPI(Api):
    def set_access_token(self, access_token):
        self.access_token = access_token

    def _build_get_houses_request(self, access_token):
        return {
            "method": "get",
            "access_token": access_token,
            "url": API_GET_HOUSES_URL,
        }

    def _process_houses_json(self, json_dict):
        return [House(data) for data in json_dict]

    def get_houses(self, access_token):
        return self._process_houses_json(
            self._dict_to_api(self._build_get_houses_request(access_token)).json()
        )

    def get_lock_detail(self, access_token, lock_id):
        return LockDetail(
            self._dict_to_api(
                self._build_get_lock_detail_request(access_token, lock_id)
            ).json()
        )


def create_client():
    api = AugustAPI(timeout=20)
    authenticator = Authenticator(
        api,
        "email",
        AUGUST_USERNAME,
        AUGUST_PASSWORD,
        access_token_cache_file="auth_cache",
    )
    authentication = authenticator.authenticate()
    api.set_access_token(authentication.access_token)
    return api
