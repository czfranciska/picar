import asyncio
import json
import time
import smbus2
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer
from aiortc.sdp import candidate_from_sdp
from picar_core.pi.hardware import ServoHatDriver, OutputDriver
from picar_core.pi.webrtc import WebRTCServerAV
from picar_core.pi.sensor import CPUSensor

async def failsafe_task(driver: OutputDriver, last_rx_ref: list, failsafe_s: float, poll_interval: float):
    # Stops the car (set throttle to neutral) if no control message is received for more than failsafe_s seconds.
    while True:
        await asyncio.sleep(poll_interval)
        if time.time() - last_rx_ref[0] > failsafe_s:
            driver.neutral()

async def sensor_task(ws, sensors: list, active_subs: dict, interval_s: float = 1.0):
    # Periodically reads requested sensors and sends the data to the pc server
    while True:
        payload = {
            "type": "sensor",
            "data": {}
        }
        for sensor in sensors:
            if sensor.name in active_subs.get("requested_sensors", set()):
                payload["data"][sensor.name] = sensor.read()

        if payload["data"]:
            try:
                await ws.send(json.dumps(payload))
            except websockets.exceptions.ConnectionClosed:
                break

        await asyncio.sleep(interval_s)

async def main(config_path="picar_core/pi/pi_config.json", active_sensors=None):
    if active_sensors is None:
        active_sensors = []
    # Load configuration from JSON file
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        print(f"[INFO] Loaded configuration from {config_path}")
    except FileNotFoundError:
        print(f"[ERROR] Could not find {config_path}.")
        return

    driver_conf = config.get("driver", {})
    sensor_conf = config.get("sensors", {})
    camera_conf = config.get("camera", {})
    backend_url = config.get("backend_url", "ws://mono.inf.elte.hu:3333")

    last_rx_ref = [time.time()]
    failsafe_timeout = driver_conf.get("failsafe_s", 0.5)
    poll_interval = driver_conf.get("failsafe_poll_interval_s", 0.05)
    steer_dz = driver_conf.get("steer_deadzone", 0.03)
    throttle_dz = driver_conf.get("throttle_deadzone", 0.02)

    # Initialize the servo driver and arm the ESC.
    driver = ServoHatDriver(
        steer_channel=driver_conf.get("steer_channel", 0),
        esc_channel=driver_conf.get("esc_channel", 3),
        i2c_address=driver_conf.get("i2c_address", 0x40),
        frequency_hz=driver_conf.get("frequency_hz", 50),
        steer_center_us=driver_conf.get("steer_center_us", 1530),
        steer_range_us=driver_conf.get("steer_range_us", 300),
        esc_neutral_us=driver_conf.get("esc_neutral_us", 1500),
        esc_min_us=driver_conf.get("esc_min_us", 1350),
        esc_max_us=driver_conf.get("esc_max_us", 1600),
        dry_run=driver_conf.get("dry_run", False)
    )
    driver.arm(driver_conf.get("arming_s", 2.0))

    asyncio.create_task(failsafe_task(driver, last_rx_ref, failsafe_timeout, poll_interval))

    poll_rate = sensor_conf.get("poll_interval_s", 1.0)  #
    for sensor in active_sensors:
        sensor.setup()
    active_subs = {"requested_sensors": set()}

    while True:
        try:
            # Connect to the backend WebSocket server
            async with websockets.connect(backend_url) as ws:
                # Identify as the car to the backend
                await ws.send(json.dumps({"role": "car"}))
                print(f"[PICAR] Connected to backend at {backend_url}")
                # Start the sensor task to periodically send sensor data
                asyncio.create_task(sensor_task(ws, active_sensors, active_subs, interval_s=poll_rate))
                # Initialize the WebRTC server
                rtc = WebRTCServerAV(
                    camera_conf.get("device", "/dev/video0"),
                    camera_conf.get("width", 320),
                    camera_conf.get("height", 240),
                    camera_conf.get("fps", 20),
                    camera_conf.get("format", "yuyv422"),
                    camera_conf.get("stun", "stun:stun.l.google.com:19302")
                )
                # Main loop to receive messages
                async for msg in ws:
                    try:
                        obj = json.loads(msg)
                    except Exception:
                        continue

                    mtype = obj.get("type")
                    # Handle control commands
                    if mtype == "control":
                        last_rx_ref[0] = time.time()

                        steer = float(obj.get("steer", 0.0))
                        throttle = float(obj.get("throttle", 0.0))
                        if abs(steer) < steer_dz: steer = 0.0
                        if abs(throttle) < throttle_dz: throttle = 0.0

                        driver.set_steer_throttle(steer, throttle)
                        print(f"[CONTROL] Received - Steer: {steer:.3f}, Throttle: {throttle:.3f}")
                    # Handle requests for available sensors
                    elif mtype == "get_sensor_list":
                        # Create a list of names from the active_sensors list
                        available = [s.name for s in active_sensors]
                        await ws.send(json.dumps({
                            "type": "sensor_list",
                            "sensors": available
                        }))
                    elif mtype == "subscribe_sensors":
                        requested = obj.get("sensors", [])
                        active_subs["requested_sensors"] = set(requested)
                        print(f"[PICAR] Client requested sensors: {active_subs['requested_sensors']}")
                    # Handle WebRTC offers
                    elif mtype == "webrtc_offer":
                        asyncio.create_task(rtc.handle_offer(ws, obj))
                    # Handle incoming ICE candidates
                    elif mtype == "webrtc_ice":
                        await rtc.handle_ice(obj)
        except Exception as e:
            print(f"[PICAR] Connection lost/failed: {e}. Retrying in 5 seconds...")
            driver.neutral()
            await asyncio.sleep(5)

def start():
    # Synchronous entry point for pyproject.toml scripts.
    import sys
    import asyncio
    cfg = sys.argv[1] if len(sys.argv) > 1 else "picar_core/pi/pi_config.json"

    active_sensors = [CPUSensor(name="cpu_core")]

    try:
        asyncio.run(main(cfg, active_sensors))
    except KeyboardInterrupt:
        print("\n[PICAR] Program stopped by user.")
    finally:
        print("\n[PICAR] Shutting down hardware...")
        for sensor in active_sensors:
            sensor.cleanup()

if __name__ == "__main__":
    start()