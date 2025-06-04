import unittest
from unittest.mock import Mock, patch, MagicMock
import json
import logging

from messages import (
    on_station_message, on_system_message, on_raindelay_message,
    on_weather_message, on_flow_alert_message, get_all_topics_and_message_fns
)

# For testing purposes, we'll include the functions here
# In practice, you'd import them from your actual module

class MockMQTTMessage:
    """Mock MQTT message for testing"""
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload.encode() if isinstance(payload, str) else payload

    def decode(self):
        return self.payload.decode()


class TestStationMessageHandler(unittest.TestCase):
    
    def setUp(self):
        self.client = Mock()
        self.userdata = None
        
    @patch('requests.post')
    @patch('logging.info')
    def test_station_turned_on(self, mock_logging, mock_requests):
        """Test station turning on with duration"""
        payload = {"state": 1, "duration": 1800}  # 30 minutes
        msg = MockMQTTMessage("opensprinkler/station/3", json.dumps(payload))
        
        on_station_message(self.client, self.userdata, msg)
        
        mock_logging.assert_called_with("Front yard turned ON - Duration: 30m")
        mock_requests.assert_called_once()
        
        # Check the request payload
        call_args = mock_requests.call_args
        self.assertEqual(call_args[0][0], 'http://api:5000/pushover/sprinkler/message')
        request_data = call_args[1]['json']
        self.assertEqual(request_data['message'], "Front yard turned ON - Duration: 30m")
        self.assertEqual(request_data['title'], "OpenSprinkler Notification")
    
    @patch('requests.post')
    @patch('logging.info')
    def test_station_turned_on_with_seconds(self, mock_logging, mock_requests):
        """Test station turning on with duration in seconds only"""
        payload = {"state": 1, "duration": 45}  # 45 seconds
        msg = MockMQTTMessage("opensprinkler/station/1", json.dumps(payload))
        
        on_station_message(self.client, self.userdata, msg)
        
        mock_logging.assert_called_with("Soakers turned ON - Duration: 45s")
    
    @patch('requests.post')
    @patch('logging.info')
    def test_station_turned_on_mixed_duration(self, mock_logging, mock_requests):
        """Test station turning on with mixed minutes and seconds"""
        payload = {"state": 1, "duration": 2730}  # 45 minutes 30 seconds
        msg = MockMQTTMessage("opensprinkler/station/0", json.dumps(payload))
        
        on_station_message(self.client, self.userdata, msg)
        
        mock_logging.assert_called_with("Back Yard turned ON - Duration: 45m 30s")
    
    @patch('requests.post')
    @patch('logging.info')
    def test_station_turned_off_with_runtime(self, mock_logging, mock_requests):
        """Test station turning off with actual runtime"""
        payload = {"state": 0, "duration": 1680}  # 28 minutes
        msg = MockMQTTMessage("opensprinkler/station/4", json.dumps(payload))
        
        on_station_message(self.client, self.userdata, msg)
        
        mock_logging.assert_called_with("North side turned OFF - Ran for: 28m")
    
    @patch('requests.post')
    @patch('logging.info')
    def test_station_turned_off_no_duration(self, mock_logging, mock_requests):
        """Test station turning off without duration"""
        payload = {"state": 0, "duration": 0}
        msg = MockMQTTMessage("opensprinkler/station/2", json.dumps(payload))
        
        on_station_message(self.client, self.userdata, msg)
        
        mock_logging.assert_called_with("South side turned OFF")
    
    @patch('requests.post')
    @patch('logging.info')
    def test_unknown_station_number(self, mock_logging, mock_requests):
        """Test with unknown station number"""
        payload = {"state": 1, "duration": 600}
        msg = MockMQTTMessage("opensprinkler/station/99", json.dumps(payload))
        
        on_station_message(self.client, self.userdata, msg)
        
        mock_logging.assert_called_with("Station 99 turned ON - Duration: 10m")
    
    @patch('requests.post')
    @patch('logging.info')
    def test_unknown_state(self, mock_logging, mock_requests):
        """Test with unknown state value"""
        payload = {"state": 5, "duration": 600}
        msg = MockMQTTMessage("opensprinkler/station/1", json.dumps(payload))
        
        on_station_message(self.client, self.userdata, msg)
        
        mock_logging.assert_called_with("Soakers - Unknown state: 5")
    
    @patch('requests.post')
    @patch('logging.error')
    def test_invalid_json_payload(self, mock_logging, mock_requests):
        """Test with invalid JSON payload"""
        msg = MockMQTTMessage("opensprinkler/station/1", "invalid json")
        
        on_station_message(self.client, self.userdata, msg)
        
        mock_logging.assert_called()
        # Should send fallback message
        mock_requests.assert_called()
        call_args = mock_requests.call_args
        request_data = call_args[1]['json']
        self.assertIn("Received `invalid json`", request_data['message'])


class TestSystemMessageHandler(unittest.TestCase):
    
    def setUp(self):
        self.client = Mock()
        self.userdata = None
    
    @patch('requests.post')
    @patch('logging.info')
    def test_system_started(self, mock_logging, mock_requests):
        """Test system reboot/started message"""
        payload = {"state": "started"}
        msg = MockMQTTMessage("opensprinkler/system", json.dumps(payload))
        
        on_system_message(self.client, self.userdata, msg)
        
        mock_logging.assert_called_with("OpenSprinkler controller has rebooted and is now online")
        mock_requests.assert_called_once()
        
        call_args = mock_requests.call_args
        request_data = call_args[1]['json']
        self.assertEqual(request_data['title'], "OpenSprinkler System")
    
    @patch('requests.post')
    @patch('logging.info')
    def test_system_unknown_state(self, mock_logging, mock_requests):
        """Test system message with unknown state"""
        payload = {"state": "maintenance"}
        msg = MockMQTTMessage("opensprinkler/system", json.dumps(payload))
        
        on_system_message(self.client, self.userdata, msg)
        
        mock_logging.assert_called_with("OpenSprinkler system status: maintenance")


class TestRainDelayHandler(unittest.TestCase):
    
    def setUp(self):
        self.client = Mock()
        self.userdata = None
    
    @patch('requests.post')
    @patch('logging.info')
    def test_rain_delay_activated(self, mock_logging, mock_requests):
        """Test rain delay activation"""
        payload = {"state": 1}
        msg = MockMQTTMessage("opensprinkler/raindelay", json.dumps(payload))
        
        on_raindelay_message(self.client, self.userdata, msg)
        
        mock_logging.assert_called_with("Rain delay has been activated - watering suspended")
        
        call_args = mock_requests.call_args
        request_data = call_args[1]['json']
        self.assertEqual(request_data['title'], "OpenSprinkler Rain Delay")
    
    @patch('requests.post')
    @patch('logging.info')
    def test_rain_delay_deactivated(self, mock_logging, mock_requests):
        """Test rain delay deactivation"""
        payload = {"state": 0}
        msg = MockMQTTMessage("opensprinkler/raindelay", json.dumps(payload))
        
        on_raindelay_message(self.client, self.userdata, msg)
        
        mock_logging.assert_called_with("Rain delay has been deactivated - watering can resume")


class TestWeatherHandler(unittest.TestCase):
    
    def setUp(self):
        self.client = Mock()
        self.userdata = None
    
    @patch('requests.post')
    @patch('logging.info')
    def test_weather_adjustment(self, mock_logging, mock_requests):
        """Test weather adjustment message"""
        payload = {"water level": "85%"}
        msg = MockMQTTMessage("opensprinkler/weather", json.dumps(payload))
        
        on_weather_message(self.client, self.userdata, msg)
        
        mock_logging.assert_called_with("Weather adjustment updated - Water level: 85%")
        
        call_args = mock_requests.call_args
        request_data = call_args[1]['json']
        self.assertEqual(request_data['title'], "OpenSprinkler Weather Update")


class TestFlowAlertHandler(unittest.TestCase):
    
    def setUp(self):
        self.client = Mock()
        self.userdata = None
    
    @patch('requests.post')
    @patch('logging.warning')
    def test_flow_alert_json(self, mock_logging, mock_requests):
        """Test flow alert with JSON payload"""
        payload = {"alert": "High flow detected", "zone": 3}
        msg = MockMQTTMessage("opensprinkler/alert/flow", json.dumps(payload))
        
        on_flow_alert_message(self.client, self.userdata, msg)
        
        mock_logging.assert_called()
        
        call_args = mock_requests.call_args
        request_data = call_args[1]['json']
        self.assertEqual(request_data['title'], "⚠️ OpenSprinkler Flow Alert")
        self.assertIn("Flow Alert:", request_data['message'])
    
    @patch('requests.post')
    @patch('logging.warning')
    def test_flow_alert_plain_text(self, mock_logging, mock_requests):
        """Test flow alert with plain text payload"""
        msg = MockMQTTMessage("opensprinkler/alert/flow", "Flow sensor disconnected")
        
        on_flow_alert_message(self.client, self.userdata, msg)
        
        mock_logging.assert_called()
        
        call_args = mock_requests.call_args
        request_data = call_args[1]['json']
        self.assertIn("Flow sensor disconnected", request_data['message'])


class TestTopicConfiguration(unittest.TestCase):
    
    def test_get_all_topics_and_message_fns(self):
        """Test that all topics and handlers are properly configured"""
        topics_and_handlers = get_all_topics_and_message_fns()
        
        expected_topics = [
            "opensprinkler/station/+",
            "opensprinkler/system",
            "opensprinkler/raindelay", 
            "opensprinkler/weather",
            "opensprinkler/alert/flow"
        ]
        
        actual_topics = [topic for topic, handler in topics_and_handlers]
        
        self.assertEqual(len(topics_and_handlers), 5)
        self.assertEqual(actual_topics, expected_topics)
        
        # Verify all handlers are callable
        for topic, handler in topics_and_handlers:
            self.assertTrue(callable(handler))


if __name__ == '__main__':
    # Configure logging for tests
    logging.basicConfig(level=logging.INFO)
    
    # Run the tests
    unittest.main(verbosity=2)