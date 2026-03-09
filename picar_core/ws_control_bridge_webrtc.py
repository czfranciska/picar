#!/usr/bin/env python3
"""
WebSocket bridge for browser control + WebRTC signaling.

Browser connects here (default ws://<pi>:8765) and sends:
  - {"type":"control","steer":float,"throttle":float}
  - {"type":"webrtc_offer","sdp": "..."}
  - {"type":"webrtc_ice","candidate": {...}}

Bridge:
  - Forwards control to UDP (to rc_server_webrtc.py --port).
  - Opens a signaling WebSocket to the Pi's signaling server (:8770 by default)
    and relays WebRTC SDP/ICE both ways.

Run:
  pip3 install websockets
  python3 ws_control_bridge_webrtc.py --ws-host 0.0.0.0 --ws-port 8765 \
     --udp-host 127.0.0.1 --udp-port 9999 \
     --signal-url ws://127.0.0.1:8770
"""
import argparse
import asyncio
import json
import socket
from typing import Tuple

import websockets

def clamp(x, lo, hi): return lo if x < lo else hi if x > hi else x

def make_udp_sender(dst: Tuple[str,int]):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    def send(steer: float, throttle: float):
        steer = clamp(float(steer), -1.0, 1.0)
        throttle = clamp(float(throttle), -1.0, 1.0)
        payload = f"s={steer:.3f},t={throttle:.3f}\n".encode("utf-8")
        sock.sendto(payload, dst)
    return sock, send

async def browser_handler(ws, udp_send, signal_url: str):
    print(f"[WS] Browser connected: {ws.remote_address}")

    # Connect to server-side signaling WS
    sig_ws = await websockets.connect(signal_url)
    print(f"[WS] Connected to signaling: {signal_url}")

    async def sig_to_browser():
        try:
            async for msg in sig_ws:
                # Expect {"type":"webrtc_answer"} or {"type":"webrtc_ice"}
                await ws.send(msg)
        except Exception:
            pass

    task_sig_to_browser = asyncio.create_task(sig_to_browser())

    try:
        async for msg in ws:
            try:
                o = json.loads(msg)
            except Exception:
                continue

            mtype = o.get("type")
            if mtype == "control":
                udp_send(o.get("steer",0.0), o.get("throttle",0.0))
            elif mtype in ("webrtc_offer","webrtc_ice"):
                await sig_ws.send(json.dumps(o))
            # ignore others
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        task_sig_to_browser.cancel()
        try: await sig_ws.close()
        except Exception: pass
        print(f"[WS] Browser disconnected: {ws.remote_address}")

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws-host", default="0.0.0.0")
    ap.add_argument("--ws-port", type=int, default=8765)
    ap.add_argument("--udp-host", default="127.0.0.1")
    ap.add_argument("--udp-port", type=int, default=9999)
    ap.add_argument("--signal-url", default="ws://127.0.0.1:8770")
    args = ap.parse_args()

    _, udp_send = make_udp_sender((args.udp_host, args.udp_port))

    print(f"[WS] Bridge listening on ws://{args.ws_host}:{args.ws_port} → UDP {args.udp_host}:{args.udp_port}")
    async with websockets.serve(
        lambda ws: browser_handler(ws, udp_send, args.signal_url),
        args.ws_host, args.ws_port, ping_interval=10, ping_timeout=10
    ):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())

