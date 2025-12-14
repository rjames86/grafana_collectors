import json
import logging
import requests
import datetime
from urllib.parse import urlparse

station_names = {
    "0": "Back Yard",
    "1": "Soakers", 
    "2": "South side",
    "3": "Front yard",
    "4": "North side",
    "5": "Not Used",
    "6": "S07",
    "7": "S08"
}

def on_station_message(client, userdata, msg):
    try:
        # Parse the JSON payload
        payload = json.loads(msg.payload.decode())
        state = payload.get('state')
        duration = payload.get('duration', 0)
        
        # Extract station number from topic (e.g., "opensprinkler/station/4" -> "4")
        topic_parts = msg.topic.split('/')
        station_number = topic_parts[-1] if len(topic_parts) > 2 else "Unknown"
        
        # Get friendly name for the station
        station_name = station_names.get(station_number, f"Station {station_number}")
        
        # Create human-readable message
        if state == 1:
            # Station turned on
            duration_minutes = duration // 60
            duration_seconds = duration % 60
            
            if duration_minutes > 0:
                duration_text = f"{duration_minutes}m {duration_seconds}s" if duration_seconds > 0 else f"{duration_minutes}m"
            else:
                duration_text = f"{duration_seconds}s"
            
            message = f"{station_name} turned ON - Duration: {duration_text}"
            
        elif state == 0:
            # Station turned off
            if duration > 0:
                duration_minutes = duration // 60
                duration_seconds = duration % 60
                
                if duration_minutes > 0:
                    duration_text = f"{duration_minutes}m {duration_seconds}s" if duration_seconds > 0 else f"{duration_minutes}m"
                else:
                    duration_text = f"{duration_seconds}s"
                
                message = f"{station_name} turned OFF - Ran for: {duration_text}"
            else:
                message = f"{station_name} turned OFF"
        else:
            # Unknown state
            message = f"{station_name} - Unknown state: {state}"
        
        logging.info(message)
        
        # Send notification
        requests.post('http://api:5000/pushover/sprinkler/message', json=dict(
            message=message,
            title="OpenSprinkler Notification"
        ))
        
    except Exception as e:
        logging.error(f"Error processing station MQTT message: {e}")
        _send_fallback_message(msg, "OpenSprinkler Station Error")


def on_system_message(client, userdata, msg):
    """Handle system reboot messages"""
    try:
        payload = json.loads(msg.payload.decode())
        state = payload.get('state')
        
        if state == "started":
            message = "OpenSprinkler controller has rebooted and is now online"
        else:
            message = f"OpenSprinkler system status: {state}"
        
        logging.info(message)
        
        requests.post('http://api:5000/pushover/sprinkler/message', json=dict(
            message=message,
            title="OpenSprinkler System"
        ))
        
    except Exception as e:
        logging.error(f"Error processing system MQTT message: {e}")
        _send_fallback_message(msg, "OpenSprinkler System Error")


def on_raindelay_message(client, userdata, msg):
    """Handle rain delay messages"""
    try:
        payload = json.loads(msg.payload.decode())
        state = payload.get('state')
        
        if state == 1:
            message = "Rain delay has been activated - watering suspended"
        elif state == 0:
            message = "Rain delay has been deactivated - watering can resume"
        else:
            message = f"Rain delay status unknown: {state}"
        
        logging.info(message)
        
        requests.post('http://api:5000/pushover/sprinkler/message', json=dict(
            message=message,
            title="OpenSprinkler Rain Delay"
        ))
        
    except Exception as e:
        logging.error(f"Error processing rain delay MQTT message: {e}")
        _send_fallback_message(msg, "OpenSprinkler Rain Delay Error")


def on_weather_message(client, userdata, msg):
    """Handle weather adjustment messages"""
    try:
        payload = json.loads(msg.payload.decode())
        water_level = payload.get('water level', 'unknown')
        
        message = f"Weather adjustment updated - Water level: {water_level}"
        
        logging.info(message)
        
        requests.post('http://api:5000/pushover/sprinkler/message', json=dict(
            message=message,
            title="OpenSprinkler Weather Update"
        ))
        
    except Exception as e:
        logging.error(f"Error processing weather MQTT message: {e}")
        _send_fallback_message(msg, "OpenSprinkler Weather Error")


def on_flow_alert_message(client, userdata, msg):
    """Handle flow alert messages"""
    try:
        # Flow alerts typically contain alert information
        payload_str = msg.payload.decode()
        
        # Try to parse as JSON, but flow alerts might be plain text
        try:
            payload = json.loads(payload_str)
            message = f"Flow Alert: {payload}"
        except json.JSONDecodeError:
            message = f"Flow Alert: {payload_str}"
        
        logging.warning(message)
        
        requests.post('http://api:5000/pushover/sprinkler/message', json=dict(
            message=message,
            title="⚠️ OpenSprinkler Flow Alert"
        ))
        
    except Exception as e:
        logging.error(f"Error processing flow alert MQTT message: {e}")
        _send_fallback_message(msg, "OpenSprinkler Flow Alert Error")


def on_unifi_protect_message(client, userdata, msg):
    IGNORED_TOPIC_TYPES = [
        'snapshot',
    ]

    MAC_ADDRESS_MAPPING = {
        "847848260182": "Back door",
        "8C3066FE8882": "Deck",
        "84784824F7D7": "Driveway",
        "AC83F359D0CC": "FOSCAM R2 V4",
        "84784824F7C2": "Front door",
        "847848289D28": "Garage",
        "8C3066FE87D3": "North Driveway"
    }

    """Handle all UniFi Protect MQTT messages and send to API"""
    try:
        # Parse topic to extract device MAC and topic type
        # Expected format: unifi/protect/[MAC-ADDRESS]/[topic]
        topic_parts = msg.topic.split('/')
        if len(topic_parts) < 4:
            logging.warning(f"Unexpected UniFi Protect topic format: {msg.topic}")
            return

        base_path = '/'.join(topic_parts[:2])  # "unifi/protect"
        mac_address = topic_parts[2]
        topic_type = '/'.join(topic_parts[3:])  # Everything after MAC address

        if topic_type in IGNORED_TOPIC_TYPES:
            logging.info(f"Ignoring UniFi Protect topic type: {topic_type}")
            return

        # Get device friendly name from mapping
        device_name = MAC_ADDRESS_MAPPING.get(mac_address, f"Unknown-{mac_address}")

        # Parse topic structure for better organization
        topic_levels = topic_parts[3:]  # e.g., ['motion', 'smart', 'person']

        # Determine measurement and tags based on topic structure
        if len(topic_levels) >= 1:
            measurement = topic_levels[0]  # e.g., 'motion', 'temperature', etc.

            # Build base tags that will appear on all data points
            tags = {
                "device_mac": mac_address,
                "device_name": device_name,
                "source": "mqtt"
            }

            # Handle nested topics like motion/smart/person
            if len(topic_levels) == 3 and topic_levels[1] == "smart":
                tags["smart_type"] = topic_levels[2]  # person, face, vehicle, etc.
            elif len(topic_levels) == 2:
                tags["sub_type"] = topic_levels[1]  # for topics like light/brightness
            elif len(topic_levels) > 3:
                # For deeper nesting, join remaining parts
                tags["sub_type"] = "/".join(topic_levels[1:])
        else:
            # Fallback for unexpected structure
            measurement = "unifi_protect"
            tags = {
                "device_mac": mac_address,
                "device_name": device_name,
                "source": "mqtt",
                "topic_type": topic_type
            }

        # Try to parse payload as JSON, fallback to string
        try:
            payload = json.loads(msg.payload.decode())
            payload_value = payload
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload_value = msg.payload.decode()

        # Convert data types for InfluxDB compatibility
        if isinstance(payload_value, bool):
            payload_value = int(payload_value)
        elif isinstance(payload_value, (int, float)):
            payload_value = float(payload_value)
        elif isinstance(payload_value, str):
            # Try to convert string numbers to float
            try:
                payload_value = float(payload_value)
            except ValueError:
                # Keep as string if not numeric
                pass

        if isinstance(payload_value, dict) or isinstance(payload_value, list):
            # For complex types, convert to JSON string
            print(json.dumps(payload_value))

        # Create data point for API
        data_point = {
            "measurement": measurement,
            "tags": tags,
            "fields": {
                "value": payload_value,
                "topic": msg.topic
            },
            "time": datetime.datetime.utcnow().isoformat()
        }

        # Send to API
        api_payload = {
            "data_points": [data_point],
            "verbose": False
        }

        response = requests.post(
            'http://api:5000/influx/unifi_protect/write',
            json=api_payload
        )
        response.raise_for_status()

        logging.info(f"UniFi Protect: {device_name}/{topic_type} -> {payload_value}")

    except Exception as e:
        logging.error(f"Error processing UniFi Protect MQTT message from {msg.topic}: {e}")


def _send_fallback_message(msg, title):
    """Send a fallback message when JSON parsing fails"""
    original_message = f"Received `{msg.payload.decode()}` from `{msg.topic}` topic"

    requests.post('http://api:5000/pushover/sprinkler/message', json=dict(
        message=original_message,
        title=f"{title} (Raw)"
    ))


def get_all_topics_and_message_fns():
    """
    Returns a list of tuples containing MQTT topics and their corresponding message handling functions.
    """
    return [
        # OpenSprinkler topics
        ("opensprinkler/station/+", on_station_message),
        ("opensprinkler/system", on_system_message),
        ("opensprinkler/raindelay", on_raindelay_message),
        ("opensprinkler/weather", on_weather_message),
        ("opensprinkler/alert/flow", on_flow_alert_message),

        # UniFi Protect topics - subscribe to all topics under unifi/protect (any depth)
        ("unifi/protect/+/#", on_unifi_protect_message),  # All topics under each device MAC
    ]