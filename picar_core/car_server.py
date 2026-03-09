#!/usr/bin/env python3
"""
RC Car UDP control server + TCP MJPEG-like stream (length-prefixed JPEG frames).

CONTROL:
  - UDP server receives steering/throttle commands (unchanged).
VIDEO:
  - TCP server sends a sequence of frames:
        [4-byte big-endian length][JPEG bytes] repeated
    One client at a time (additional connections wait until the current client disconnects).

Usage (on the Pi):
  sudo python3 rc_server.py --port 9999 \
      --video-port 9998 --camera-index 0 --video-width 640 --video-height 480 --video-fps 20

Dependencies on the Pi:
  - adafruit-circuitpython-servokit (for PWM)
  - opencv-python, numpy (for camera/encoding)
"""

import argparse
import asyncio
import json
import socket
import struct
import threading
import time
from typing import Optional, Tuple
import math
import smbus2
import websockets

# ============================================================================
# Raspi PCA9685 16-Channel PWM Servo Driver
# ============================================================================

class PCA9685:

  # Registers/etc.
  __SUBADR1            = 0x02
  __SUBADR2            = 0x03
  __SUBADR3            = 0x04
  __MODE1              = 0x00
  __PRESCALE           = 0xFE
  __LED0_ON_L          = 0x06
  __LED0_ON_H          = 0x07
  __LED0_OFF_L         = 0x08
  __LED0_OFF_H         = 0x09
  __ALLLED_ON_L        = 0xFA
  __ALLLED_ON_H        = 0xFB
  __ALLLED_OFF_L       = 0xFC
  __ALLLED_OFF_H       = 0xFD

  def __init__(self, address=0x40, debug=False):
    self.bus = smbus2.SMBus(1)
    self.address = address
    self.debug = debug
    self.pwrange = {}
    if (self.debug):
      print("Reseting PCA9685")
    self.write(self.__MODE1, 0x00)

  def write(self, reg, value):
    "Writes an 8-bit value to the specified register/address"
    self.bus.write_byte_data(self.address, reg, value)
    if (self.debug):
      print("I2C: Write 0x%02X to register 0x%02X" % (value, reg))

  def read(self, reg):
    "Read an unsigned byte from the I2C device"
    result = self.bus.read_byte_data(self.address, reg)
    if (self.debug):
      print("I2C: Device 0x%02X returned 0x%02X from reg 0x%02X" % (self.address, result & 0xFF, reg))
    return result

  def setPulseWidthRange(self, channel, minvalue, maxvalue):
    self.pwrange[channel] = (minvalue, maxvalue)

  def setPWMFreq(self, freq):
    "Sets the PWM frequency"
    prescaleval = 25000000.0    # 25MHz
    prescaleval /= 4096.0       # 12-bit
    prescaleval /= float(freq)
    prescaleval -= 1.0
    if (self.debug):
      print("Setting PWM frequency to %d Hz" % freq)
      print("Estimated pre-scale: %d" % prescaleval)
    prescale = math.floor(prescaleval + 0.5)
    if (self.debug):
      print("Final pre-scale: %d" % prescale)

    oldmode = self.read(self.__MODE1);
    newmode = (oldmode & 0x7F) | 0x10        # sleep
    self.write(self.__MODE1, newmode)        # go to sleep
    self.write(self.__PRESCALE, int(math.floor(prescale)))
    self.write(self.__MODE1, oldmode)
    time.sleep(0.005)
    self.write(self.__MODE1, oldmode | 0x80)

  def setPWM(self, channel, on, off):
    "Sets a single PWM channel"
    self.write(self.__LED0_ON_L+4*channel, on & 0xFF)
    self.write(self.__LED0_ON_H+4*channel, on >> 8)
    self.write(self.__LED0_OFF_L+4*channel, off & 0xFF)
    self.write(self.__LED0_OFF_H+4*channel, off >> 8)
    if (self.debug):
      print("channel: %d  LED_ON: %d LED_OFF: %d" % (channel,on,off))

  def setServoPulse(self, channel, pulse):
    if channel in self.pwrange:
        minv,maxv = self.pwrange[channel]
        if pulse<minv:
            pulse = minv
        elif pulse>maxv:
            pulse = maxv
    "Sets the Servo Pulse,The PWM frequency must be 50HZ"
    pulse = pulse*4096/20000        #PWM frequency is 50HZ,the period is 20000us
    self.setPWM(channel, 0, int(pulse))



# ========== Hardware abstraction (same as before, trimmed for brevity) =========
class OutputDriver:
    def set_steer_throttle(self, steer: float, throttle: float) -> None: ...
    def arm(self, seconds: float) -> None: ...
    def neutral(self) -> None: ...

class ServoHatDriver(OutputDriver):
    def __init__(
        self,
        steer_channel: int = 0,
        esc_channel: int = 3,
        i2c_address: int = 0x40,
        frequency_hz: int = 50,
        steer_center_us: int = 1500,
        steer_range_us: int = 300,
        esc_neutral_us: int = 1500,
        esc_min_us: int = 1300,
        esc_max_us: int = 1650,
        dry_run: bool = False,
    ):
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
                self.pwm.setPulseWidthRange(self.steer_ch, self.steer_center - self.steer_range, self.steer_center + self.steer_range)
                self.pwm.setPulseWidthRange(self.esc_ch, self.esc_min, self.esc_max)
            except Exception as e:
                print(f"[WARN] PWM control is not available, simulation mode...")
                self._dry = True
                self.pwm = None
        else:
            self.pwm = None

    def _write_us(self, channel: int, us: int) -> None:
        if self._dry:  # simulate
            return
        self.pwm.setServoPulse(channel, us)

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

    def arm(self, seconds: float) -> None:
        print(f"[INFO] Arming ESC for {seconds:.1f}s at neutral...")
        t0 = time.time()
        while time.time() - t0 < seconds:
            self.neutral()
            time.sleep(0.05)
        print("[INFO] Arming complete.")

# ------------------- UDP control server ---------------------------------------
class RCProtocol(asyncio.DatagramProtocol):
    def __init__(self, driver: OutputDriver, failsafe_s: float, steer_dead: float, thr_dead: float):
        self.driver = driver
        self.last_rx = time.time()
        self.failsafe_s = failsafe_s
        self.steer_dead = steer_dead
        self.thr_dead = thr_dead

    def connection_made(self, transport):
        addr = transport.get_extra_info('sockname')
        print(f"[INFO] UDP control listening on {addr}")

    def datagram_received(self, data: bytes, addr):
        self.last_rx = time.time()
        try:
            msg = data.decode('utf-8', errors='ignore').strip()
        except Exception:
            return
        steer, throttle = 0.0, 0.0
        if msg.startswith('{'):
            try:
                o = json.loads(msg)
                steer = float(o.get('steer', 0.0))
                throttle = float(o.get('throttle', 0.0))
            except Exception:
                pass
        elif msg.startswith('ST,'):
            try:
                _, s, t = msg.split(',', 2)
                steer, throttle = float(s), float(t)
            except Exception:
                pass
        else:
            try:
                parts = dict(p.split('=') for p in msg.split(','))
                steer = float(parts.get('s', 0.0))
                throttle = float(parts.get('t', 0.0))
            except Exception:
                pass
        if abs(steer) < self.steer_dead: steer = 0.0
        if abs(throttle) < self.thr_dead: throttle = 0.0
        self.driver.set_steer_throttle(steer, throttle)

async def failsafe_task(proto: RCProtocol, driver: OutputDriver):
    while True:
        await asyncio.sleep(0.05)
        if time.time() - proto.last_rx > proto.failsafe_s:
            driver.neutral()

# --------------- aiortc + PyAV camera → VP8 --------------------
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer
from aiortc.sdp import candidate_from_sdp

class WebRTCServerAV:
    """
    Uses aiortc.contrib.media.MediaPlayer (PyAV) to grab frames from V4L2 and encode VP8.
    """
    def __init__(self, device: str, w: int, h: int, fps: int, input_format: str, stun: Optional[str]):
        self.device = device
        self.w, self.h, self.fps = w, h, fps
        self.input_format = input_format  # 'mjpeg' or 'yuyv422'
        self.stun = stun
        self.pc: Optional[RTCPeerConnection] = None
        self.player: Optional[MediaPlayer] = None

    async def _cleanup(self):
    # Stop video/audio tracks if they exist
        if self.player:
           p = self.player
           self.player = None
           for tr in (getattr(p, "video", None), getattr(p, "audio", None)):
               try:
                   if tr and hasattr(tr, "stop"):
                      tr.stop()
               except Exception:
                   pass
           # Newer aiortc has MediaPlayer.stop(); older doesn't.
           try:
               if hasattr(p, "stop"):
                   p.stop()  # type: ignore[attr-defined]
           except Exception:
               pass
           # Some builds expose a close(); safe no-op if absent.
           try:
               if hasattr(p, "close"):
                   p.close()  # type: ignore[attr-defined]
           except Exception:
               pass
        if self.pc:
           try:
               await self.pc.close()
           except Exception:
               pass
           self.pc = None
#        if self.player:
#            self.player.stop()
#            self.player = None
#        if self.pc:
#            await self.pc.close()
#            self.pc = None

    def _make_player(self) -> MediaPlayer:
        # Open V4L2 device via PyAV with capture options
        # (aiortc MediaPlayer passes these to av.open())
        opts = {
            "video_size": f"{self.w}x{self.h}",
            "framerate": str(self.fps),
            "input_format": self.input_format,  # 'mjpeg' (recommended) or 'yuyv422'
        }
        # Note: format name can be 'v4l2' or 'video4linux2'
        return MediaPlayer(self.device, format="v4l2", options=opts)

    async def handler(self, ws):
        # Single viewer policy for simplicity: clean previous
        await self._cleanup()

        ice_servers = [RTCIceServer(urls=[self.stun])] if self.stun else []
        self.pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ice_servers))

        # Create camera player and add its video track
        self.player = self._make_player()
        if not self.player.video:
            raise RuntimeError("Camera did not provide a video track (check --camera path and format)")
        self.pc.addTrack(self.player.video)

        @self.pc.on("icecandidate")
        async def on_icecandidate(candidate):
            if candidate:
                await ws.send(json.dumps({
                    "type": "webrtc_ice",
                    "candidate": {
                        "candidate": candidate.to_sdp(),
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex
                    }
                }))

        try:
            async for msg in ws:
                try:
                    obj = json.loads(msg)
                except Exception:
                    continue

                if obj.get("type") == "webrtc_offer":
                    offer = RTCSessionDescription(sdp=obj.get("sdp",""), type="offer")
                    await self.pc.setRemoteDescription(offer)
                    # Browser will offer VP8 by default; aiortc will pick it.
                    answer = await self.pc.createAnswer()
                    await self.pc.setLocalDescription(answer)
                    await ws.send(json.dumps({"type": "webrtc_answer", "sdp": self.pc.localDescription.sdp}))

                elif obj.get("type") == "webrtc_ice":
                    c = obj.get("candidate", {})
                    cand_line = c.get("candidate")
                    if cand_line:
                        cand = candidate_from_sdp(cand_line)
                        cand.sdpMid = c.get("sdpMid")
                        cand.sdpMLineIndex = c.get("sdpMLineIndex")
                        await self.pc.addIceCandidate(cand)

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await self._cleanup()

# ------------------------------ main --------------------------------
async def main():
    ap = argparse.ArgumentParser()
    # UDP control
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--steer-channel", type=int, default=0)
    ap.add_argument("--esc-channel", type=int, default=3)
    ap.add_argument("--i2c-address", type=lambda x: int(x, 0), default=0x40)
    ap.add_argument("--arm-s", type=float, default=2.0)
    ap.add_argument("--failsafe-s", type=float, default=0.5)
    ap.add_argument("--steer-center-us", type=int, default=1500)
    ap.add_argument("--steer-range-us", type=int, default=350)
    ap.add_argument("--esc-neutral-us", type=int, default=1500)
    ap.add_argument("--esc-min-us", type=int, default=1350)
    ap.add_argument("--esc-max-us", type=int, default=1600)
    ap.add_argument("--steer-deadzone", type=float, default=0.03)
    ap.add_argument("--throttle-deadzone", type=float, default=0.02)
    ap.add_argument("--dry-run", action="store_true")
    # Video + signaling
    ap.add_argument("--camera", default="/dev/video0", help="V4L2 device path (e.g. /dev/video0)")
    ap.add_argument("--w", type=int, default=640)
    ap.add_argument("--h", type=int, default=480)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--input-format", default="yuyv422", choices=["mjpeg","yuyv422"],
                    help="Capture format to request from camera via V4L2")
    ap.add_argument("--stun", default="stun:stun.l.google.com:19302")
    ap.add_argument("--signal-host", default="0.0.0.0")
    ap.add_argument("--signal-port", type=int, default=8770)
    args = ap.parse_args()

    # Hardware
    driver = ServoHatDriver(
        steer_channel=args.steer_channel, esc_channel=args.esc_channel, i2c_address=args.i2c_address,
        steer_center_us=args.steer_center_us, steer_range_us=args.steer_range_us,
        esc_neutral_us=args.esc_neutral_us, esc_min_us=args.esc_min_us, esc_max_us=args.esc_max_us,
        dry_run=args.dry_run
    )
    driver.arm(args.arm_s)

    # UDP control server
    loop = asyncio.get_running_loop()
    proto = RCProtocol(driver, args.failsafe_s, args.steer_deadzone, args.throttle_deadzone)
    transport, _ = await loop.create_datagram_endpoint(lambda: proto, local_addr=(args.host, args.port))

    # WebRTC signaling (WebSocket)
    rtc = WebRTCServerAV(args.camera, args.w, args.h, args.fps, args.input_format, args.stun)

    async def ws_handler(ws):
        print(f"[SIG] Client connected: {ws.remote_address}")
        try:
            await rtc.handler(ws)
        finally:
            print(f"[SIG] Client disconnected: {ws.remote_address}")

    print(f"[SIG] WebRTC signaling on ws://{args.signal_host}:{args.signal_port}")
    async with websockets.serve(ws_handler, args.signal_host, args.signal_port,
                                ping_interval=10, ping_timeout=10, max_size=2**20):
        try:
            await asyncio.gather(
                failsafe_task(proto, driver),
            )
        finally:
            transport.close()

if __name__ == "__main__":
    asyncio.run(main())
