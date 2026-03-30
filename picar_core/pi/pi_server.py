import asyncio
import json
import time
from typing import Optional
import math
import smbus2
import websockets

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer
from aiortc.sdp import candidate_from_sdp


# ============================================================================
# Raspi PCA9685 16-Channel PWM Servo Driver
# ============================================================================
class PCA9685:
    __SUBADR1 = 0x02
    __SUBADR2 = 0x03
    __SUBADR3 = 0x04
    __MODE1 = 0x00
    __PRESCALE = 0xFE
    __LED0_ON_L = 0x06
    __LED0_ON_H = 0x07
    __LED0_OFF_L = 0x08
    __LED0_OFF_H = 0x09
    __ALLLED_ON_L = 0xFA
    __ALLLED_ON_H = 0xFB
    __ALLLED_OFF_L = 0xFC
    __ALLLED_OFF_H = 0xFD

    def __init__(self, address=0x40, debug=False):
        self.bus = smbus2.SMBus(1)
        self.address = address
        self.debug = debug
        self.pwrange = {}
        if (self.debug):
            print("Reseting PCA9685")
        self.write(self.__MODE1, 0x00)

    def write(self, reg, value):
        self.bus.write_byte_data(self.address, reg, value)

    def read(self, reg):
        return self.bus.read_byte_data(self.address, reg)

    # Set the pulse width range for a given channel (in microseconds)
    def setPulseWidthRange(self, channel, minvalue, maxvalue):
        self.pwrange[channel] = (minvalue, maxvalue)

    # Set the PWM frequency (in Hz)
    def setPWMFreq(self, freq):
        prescaleval = 25000000.0 / 4096.0 / float(freq) - 1.0
        prescale = math.floor(prescaleval + 0.5)
        oldmode = self.read(self.__MODE1)
        newmode = (oldmode & 0x7F) | 0x10
        self.write(self.__MODE1, newmode)
        self.write(self.__PRESCALE, int(math.floor(prescale)))
        self.write(self.__MODE1, oldmode)
        time.sleep(0.005)
        self.write(self.__MODE1, oldmode | 0x80)

    # Set the PWM on/off values for a given channel (0-15)
    def setPWM(self, channel, on, off):
        self.write(self.__LED0_ON_L + 4 * channel, on & 0xFF)
        self.write(self.__LED0_ON_H + 4 * channel, on >> 8)
        self.write(self.__LED0_OFF_L + 4 * channel, off & 0xFF)
        self.write(self.__LED0_OFF_H + 4 * channel, off >> 8)

    # Set the pulse width for a given channel (in microseconds)
    def setServoPulse(self, channel, pulse):
        if channel in self.pwrange:
            minv, maxv = self.pwrange[channel]
            if pulse < minv:
                pulse = minv
            elif pulse > maxv:
                pulse = maxv
        pulse = pulse * 4096 / 20000
        self.setPWM(channel, 0, int(pulse))

class OutputDriver:
    def set_steer_throttle(self, steer: float, throttle: float) -> None: ...

    def arm(self, seconds: float) -> None: ...

    def neutral(self) -> None: ...


class ServoHatDriver(OutputDriver):
    def __init__(self, steer_channel=0, esc_channel=3, i2c_address=0x40, frequency_hz=50,
                 steer_center_us=1500, steer_range_us=300, esc_neutral_us=1500,
                 esc_min_us=1300, esc_max_us=1650, dry_run=False):
        self._dry = dry_run
        self.steer_ch = steer_channel
        self.esc_ch = esc_channel
        self.steer_center = steer_center_us
        self.steer_range = steer_range_us
        self.esc_neutral = esc_neutral_us
        self.esc_min = esc_min_us
        self.esc_max = esc_max_us

        if not self._dry:
            try:
                self.pwm = PCA9685(i2c_address, debug=False)
                self.pwm.setPWMFreq(frequency_hz)
                self.pwm.setPulseWidthRange(self.steer_ch, self.steer_center - self.steer_range,
                                            self.steer_center + self.steer_range)
                self.pwm.setPulseWidthRange(self.esc_ch, self.esc_min, self.esc_max)
            except Exception as e:
                print(f"[WARN] PWM control is not available, simulation mode...")
                self._dry = True
                self.pwm = None

    def _write_us(self, channel: int, us: int) -> None:
        if not self._dry: self.pwm.setServoPulse(channel, us)

    def _steer_to_us(self, steer: float) -> int:
        steer = max(-1.0, min(1.0, steer))
        return int(round(self.steer_center + steer * self.steer_range))

    def _throttle_to_us(self, throttle: float) -> int:
        throttle = max(-1.0, min(1.0, throttle))
        if throttle >= 0:
            return int(round(self.esc_neutral + throttle * (self.esc_max - self.esc_neutral)))
        else:
            return int(round(self.esc_neutral + throttle * (self.esc_neutral - self.esc_min)))

    def set_steer_throttle(self, steer: float, throttle: float) -> None:
        self._write_us(self.steer_ch, self._steer_to_us(steer))
        self._write_us(self.esc_ch, self._throttle_to_us(throttle))

    def neutral(self) -> None:
        self._write_us(self.steer_ch, self.steer_center)
        self._write_us(self.esc_ch, self.esc_neutral)

    # Arm the ESC by sending neutral throttle
    def arm(self, seconds: float) -> None:
        print(f"[INFO] Arming ESC for {seconds:.1f}s at neutral...")
        t0 = time.time()
        while time.time() - t0 < seconds:
            self.neutral()
            time.sleep(0.05)
        print("[INFO] Arming complete.")


class WebRTCServerAV:
    def __init__(self, device: str, w: int, h: int, fps: int, input_format: str, stun: Optional[str]):
        self.device = device
        self.w, self.h, self.fps = w, h, fps
        self.input_format = input_format
        self.stun = stun
        self.pc: Optional[RTCPeerConnection] = None
        self.player: Optional[MediaPlayer] = None

    # Clean up existing WebRTC connection and media player
    async def _cleanup(self):
        if self.player:
            p = self.player
            self.player = None
            for tr in (getattr(p, "video", None), getattr(p, "audio", None)):
                try:
                    if tr and hasattr(tr, "stop"): tr.stop()
                except Exception:
                    pass
            try:
                if hasattr(p, "stop"): p.stop()
            except Exception:
                pass
            try:
                if hasattr(p, "close"): p.close()
            except Exception:
                pass
        if self.pc:
            try:
                await self.pc.close()
            except Exception:
                pass
            self.pc = None

    # Create a MediaPlayer for the camera
    def _make_player(self) -> MediaPlayer:
        opts = {"video_size": f"{self.w}x{self.h}", "framerate": str(self.fps), "input_format": self.input_format}
        return MediaPlayer(self.device, format="v4l2", options=opts)

    # Handle an incoming WebRTC offer
    async def handle_offer(self, ws, obj):
        await self._cleanup()
        ice_servers = [RTCIceServer(urls=[self.stun])] if self.stun else []
        self.pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ice_servers))

        self.player = self._make_player()
        if not self.player.video:
            raise RuntimeError("Camera did not provide a video track")
        self.pc.addTrack(self.player.video)

        @self.pc.on("icecandidate")
        async def on_icecandidate(candidate):
            if candidate:
                await ws.send(json.dumps({
                    "type": "webrtc_ice",
                    "candidate": {"candidate": candidate.to_sdp(), "sdpMid": candidate.sdpMid,
                                  "sdpMLineIndex": candidate.sdpMLineIndex}
                }))

        offer = RTCSessionDescription(sdp=obj.get("sdp", ""), type="offer")
        await self.pc.setRemoteDescription(offer)
        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)
        await ws.send(json.dumps({"type": "webrtc_answer", "sdp": self.pc.localDescription.sdp}))

    # Handle an incoming ICE candidate from the client
    async def handle_ice(self, obj):
        if not self.pc: return
        c = obj.get("candidate", {})
        cand_line = c.get("candidate")
        if cand_line:
            cand = candidate_from_sdp(cand_line)
            cand.sdpMid = c.get("sdpMid")
            cand.sdpMLineIndex = c.get("sdpMLineIndex")
            await self.pc.addIceCandidate(cand)

async def failsafe_task(driver: OutputDriver, last_rx_ref: list, failsafe_s: float):
    # Constantly sends a neutral signal if the last command was too long ago.
    while True:
        await asyncio.sleep(0.05)
        if time.time() - last_rx_ref[0] > failsafe_s:
            driver.neutral()


async def sensor_task(ws, sensors: list, interval_s: float = 1.0):
    # Periodically reads all sensors and sends the data to the pc server
    while True:
        payload = {
            "type": "sensor",
            "data": {}
        }
        for sensor in sensors:
            payload["data"][sensor.name] = sensor.read()

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
    camera_conf = config.get("camera", {})
    backend_url = config.get("backend_url", "ws://mono.inf.elte.hu:3333")

    last_rx_ref = [time.time()]
    failsafe_timeout = 0.5

    # Initialize the servo driver and arm the ESC.
    driver = ServoHatDriver(
        steer_channel=driver_conf.get("steer_channel", 0),
        esc_channel=driver_conf.get("esc_channel", 3),
        steer_center_us=driver_conf.get("steer_center_us", 1530),
        steer_range_us=driver_conf.get("steer_range_us", 300),
        esc_neutral_us=driver_conf.get("esc_neutral_us", 1500),
        esc_min_us=driver_conf.get("esc_min_us", 1350),
        esc_max_us=driver_conf.get("esc_max_us", 1600),
        dry_run=driver_conf.get("dry_run", False)
    )
    driver.arm(2.0)

    asyncio.create_task(failsafe_task(driver, last_rx_ref, failsafe_timeout))

    for sensor in active_sensors:
        sensor.setup()

    while True:
        try:
            # Connect to the backend WebSocket server
            async with websockets.connect(backend_url) as ws:
                # Identify as the car to the backend
                await ws.send(json.dumps({"role": "car"}))
                print(f"[PICAR] Connected to backend at {backend_url}")
                # Start the sensor task to periodically send sensor data
                asyncio.create_task(sensor_task(ws, active_sensors, interval_s=1.0))
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
                        if abs(steer) < 0.03: steer = 0.0
                        if abs(throttle) < 0.02: throttle = 0.0

                        driver.set_steer_throttle(steer, throttle)
                        print(f"[CONTROL] Received - Steer: {steer:.3f}, Throttle: {throttle:.3f}")
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
    from picar_core.pi.sensor import CPUSensor

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
