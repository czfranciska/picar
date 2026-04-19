import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import json
import asyncio
import picar_core.pc.pc_server as pc


class TestRelayServer(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Reset global state before each test
        pc.car_connection = None
        pc.client_connections = set()
        pc.subscriptions.clear()

    async def test_client_to_car_relay(self):
        # Verify control commands from client are sent to the car.
        #  Mock car
        car_ws = AsyncMock()
        pc.car_connection = car_ws

        # Mock client
        client_ws = AsyncMock()
        # First call is the role identification
        client_ws.recv.return_value = json.dumps({"role": "client"})

        # Simulate a control command coming in through the async iterator
        control_cmd = json.dumps({"type": "control", "steer": 0.5, "throttle": 0.2})
        client_ws.__aiter__.return_value = [control_cmd]

        # Run Handler
        await pc.handler(client_ws)

        # Verify the control command was forwarded to the car
        expected_call = unittest.mock.call(control_cmd)
        car_ws.send.assert_has_calls([expected_call], any_order=True)

    @patch("websockets.broadcast")
    async def test_selective_forwarding(self, mock_broadcast):
        # Verify sensor data is forwarded only to subscribed clients
        import picar_core.pc.pc_server as pc

        # Client A is subscribed, Client B is not
        client_a = MagicMock()
        client_b = MagicMock()
        pc.subscriptions["cpu_core"].add(client_a)
        pc.client_connections.add(client_b)

        # Car sends sensor data
        car_ws = AsyncMock()
        car_ws.recv.return_value = json.dumps({"role": "car"})
        sensor_payload = {"type": "sensor", "data": {"cpu_core": {"val": 45}}}
        car_ws.__aiter__.return_value = [json.dumps(sensor_payload)]

        await pc.handler(car_ws)

        # Verify: broadcast should be called for client_a's group, not client_b
        mock_broadcast.assert_called()
        # Check if the first argument (the set of recipients) contains client_a
        recipients = mock_broadcast.call_args[0][0]
        self.assertIn(client_a, recipients)
        self.assertNotIn(client_b, recipients)

    @patch("websockets.broadcast")
    async def test_webrtc_signal_broadcast(self, mock_broadcast):
        # Verify WebRTC signals are broadcast to all clients.
        import picar_core.pc.pc_server as pc

        # Mocked car sends an ICE candidate
        car_ws = AsyncMock()
        car_ws.recv.return_value = json.dumps({"role": "car"})
        ice_msg = json.dumps({"type": "webrtc_ice", "candidate": "..."})
        car_ws.__aiter__.return_value = [ice_msg]

        # Multiple clients are connected
        client_1 = MagicMock()
        client_2 = MagicMock()
        pc.client_connections.add(client_1)
        pc.client_connections.add(client_2)

        await pc.handler(car_ws)

        # Verify: broadcast was called with the full client_connections set
        mock_broadcast.assert_called_with(pc.client_connections, ice_msg)

    async def test_client_cleanup_on_disconnect(self):
        # Verify client is removed from subscriptions and connections on disconnect.
        # Connect a client and subscribe them to a sensor
        client_ws = AsyncMock()
        client_ws.recv.return_value = json.dumps({"role": "client"})

        # Send a subscription then simulate disconnection by making the async iterator empty
        sub_msg = json.dumps({"type": "subscribe_sensors", "sensors": ["cpu_core"]})
        client_ws.__aiter__.return_value = [sub_msg]

        # Run the handler
        await pc.handler(client_ws)

        # Verify the client is gone from all tracking structures
        self.assertNotIn(client_ws, pc.client_connections)
        self.assertNotIn(client_ws, pc.subscriptions["cpu_core"])

    async def test_client_command_no_car(self):
        # Verify server doesn't crash if a client sends a command while car is offline.
        pc.car_connection = None

        client_ws = AsyncMock()
        client_ws.recv.return_value = json.dumps({"role": "client"})
        control_msg = json.dumps({"type": "control", "steer": 1.0})
        client_ws.__aiter__.return_value = [control_msg]

        # Should not raise an exception even though there's no car to send to
        await pc.handler(client_ws)

    @patch("websockets.broadcast")
    async def test_sensor_broadcast_to_multiple_subscribers(self, mock_broadcast):
        # Verify sensor data is broadcast to all clients subscribed to that specific sensor.
        # Mock two clients subscribed to 'cpu_core'
        client1 = MagicMock()
        client2 = MagicMock()
        pc.subscriptions["cpu_core"].add(client1)
        pc.subscriptions["cpu_core"].add(client2)

        # Mock car and send sensor data
        car_ws = AsyncMock()
        car_ws.recv.return_value = json.dumps({"role": "car"})
        payload = {"type": "sensor", "data": {"cpu_core": {"val": 10}}}
        car_ws.__aiter__.return_value = [json.dumps(payload)]

        await pc.handler(car_ws)

        # Verify broadcast was called with both clients in the recipient set
        recipients = mock_broadcast.call_args[0][0]
        self.assertEqual(len(recipients), 2)
        self.assertIn(client1, recipients)
        self.assertIn(client2, recipients)