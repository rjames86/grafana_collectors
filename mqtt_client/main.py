from paho.mqtt import client as mqtt_client
import logging
import time
import os
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("mqtt_client.log"),
        logging.StreamHandler()
    ]
)

broker = os.getenv('MQTT_BROKER')
port = 1883
topic = "opensprinkler/#"
# Generate a Client ID with the subscribe prefix.
client_id = f'subscribe-kvothe'
username = os.getenv('MQTT_USERNAME', 'public')
password = os.getenv('MQTT_PASSWORD', 'public')


def connect_mqtt() -> mqtt_client:
    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            print("Connected to MQTT Broker!")
        else:
            print("Failed to connect, return code %d\n", rc)

    # Specify callback_api_version for Paho MQTT 2.0 compatibility
    client = mqtt_client.Client(client_id=client_id, callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2)
    logging.info(f"{username} {password} {client_id}")
    client.username_pw_set(username, password)
    client.on_connect = on_connect
    client.connect(broker, port)
    return client


FIRST_RECONNECT_DELAY = 1
RECONNECT_RATE = 2
MAX_RECONNECT_COUNT = 12
MAX_RECONNECT_DELAY = 60

def on_disconnect(client, userdata, flags, rc, properties=None):
    logging.info("Disconnected with result code: %s", rc)
    reconnect_count, reconnect_delay = 0, FIRST_RECONNECT_DELAY
    logging.info("Reconnecting in %d seconds...", reconnect_delay)
    time.sleep(reconnect_delay)

    try:
        client.reconnect()
        logging.info("Reconnected successfully!")
        return
    except Exception as err:
        logging.error("%s. Reconnect failed. Retrying...", err)

    reconnect_delay *= RECONNECT_RATE
    reconnect_delay = min(reconnect_delay, MAX_RECONNECT_DELAY)
    logging.info("Reconnect failed after %s attempts. Exiting...", reconnect_count)


def subscribe(client: mqtt_client):
    def on_message(client, userdata, msg, properties=None):
        logging.info(f"Received `{msg.payload.decode()}` from `{msg.topic}` topic")
        requests.post('http://api:5000/pushover/sprinkler/message', json=dict(
            message=f"Received `{msg.payload.decode()}` from `{msg.topic}` topic",
            title="OpenSprinkler MQTT Notification"
        ))

    client.subscribe(topic)
    client.on_message = on_message


def run():
    client = connect_mqtt()
    client.on_disconnect = on_disconnect
    subscribe(client)
    client.loop_forever()


if __name__ == '__main__':
    run()