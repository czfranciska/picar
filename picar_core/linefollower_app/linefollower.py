import asyncio
import json
import cv2
import numpy as np
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp

BACKEND_URL = "ws://mono.inf.elte.hu:3333"


async def process_video(track, ws):
    print("[Line Follower] Video processing started.")

    while True:
        try:
            # 1. Grab frame from the incoming WebRTC stream
            frame = await track.recv()
            img = frame.to_ndarray(format="bgr24")
            height, width, _ = img.shape

            # 2. Convert to grayscale, use Gaussian blur, and threshold to find the white line
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            blur = cv2.GaussianBlur(gray, (5, 5), 0)
            ret, thresh = cv2.threshold(blur, 60, 255, cv2.THRESH_BINARY_INV)

            # 3. Find white pixels and fit a line
            white_pixels = cv2.findNonZero(thresh)

            steer_value = 0.0
            throttle_value = 0.0

            if white_pixels is not None and len(white_pixels) > 100:
                [vx, vy, x, y] = cv2.fitLine(white_pixels, cv2.DIST_HUBER, 0, 0.01, 0.01)
                vx, vy, x, y = float(vx), float(vy), float(x), float(y)

                if vy != 0:
                    # Green line represents the detected path
                    bottom_x = int(x + (height - y) * (vx / vy))
                    top_x = int(x + (0 - y) * (vx / vy))
                    cv2.line(img, (bottom_x, height), (top_x, 0), (0, 255, 0), 2)

                    # Calculate target point at the middle of the image height
                    target_y = height // 2
                    target_x = int(x + (target_y - y) * (vx / vy))
                    target_x = max(0, min(width, target_x))

                    # Blue line represents the path from the car to the target point
                    car_center_x = width // 2
                    cv2.line(img, (car_center_x, height), (target_x, target_y), (255, 0, 0), 3)

                    # Calculate steering based on the horizontal error from the center
                    error = (target_x - car_center_x) / car_center_x
                    steer_value = error * 0.5  # Soften steering
                    throttle_value = 0.15

            # 4. Send the control command to the backend
            '''
            command = {
                "type": "control",
                "steer": steer_value,
                "throttle": throttle_value
            }
            await ws.send(json.dumps(command))
            '''
            # Display the processed video feed with detected lines
            cv2.imshow("Line follower view", img)

            # Press 'q' to quit the window
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        except Exception as e:
            print(f"[Line Follower] Stream ended or error: {e}")
            break

    cv2.destroyAllWindows()


async def main():
    print(f"[CLIENT] Connecting to backend at {BACKEND_URL}...")

    async with websockets.connect(BACKEND_URL) as ws:
        # Identify as a client to the backend
        await ws.send(json.dumps({"role": "client"}))

        pc = RTCPeerConnection()

        # When the Pi sends us video tracks, start processing them
        @pc.on("track")
        def on_track(track):
            if track.kind == "video":
                asyncio.create_task(process_video(track, ws))

        # Handle outgoing ICE candidates
        @pc.on("icecandidate")
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

        # Create the WebRTC offer and send it to the car
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await ws.send(json.dumps({
            "type": "webrtc_offer",
            "sdp": pc.localDescription.sdp
        }))

        # Listen for the answer and ICE candidates from the Pi
        async for msg in ws:
            try:
                data = json.loads(msg)
                mtype = data.get("type")

                if mtype == "webrtc_answer":
                    answer = RTCSessionDescription(sdp=data["sdp"], type="answer")
                    await pc.setRemoteDescription(answer)
                elif mtype == "webrtc_ice":
                    c = data.get("candidate", {})
                    cand_line = c.get("candidate")
                    if cand_line:
                        cand = candidate_from_sdp(cand_line)
                        cand.sdpMid = c.get("sdpMid")
                        cand.sdpMLineIndex = c.get("sdpMLineIndex")
                        await pc.addIceCandidate(cand)
            except Exception:
                continue


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[CLIENT] Shutting down.")