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
    cv2.namedWindow("Line follower view", cv2.WINDOW_NORMAL)
    frame_count = 0
    while True:
        try:
            steer_value = 0.0
            throttle_value = 0.0

            # Grab frame from the incoming WebRTC stream
            frame = await track.recv()
            img = frame.to_ndarray(format="bgr24")
            height, width, _ = img.shape

            # Convert to grayscale, use Gaussian blur, and threshold to find the white line
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            blur = cv2.GaussianBlur(gray, (5, 5), 0)
            ret, thresh = cv2.threshold(blur, 60, 255, cv2.THRESH_BINARY_INV)

            area_top = int(height * 0.35)
            thresh[0:area_top, 0:width] = 0

            # Find white pixels
            white_pixels = cv2.findNonZero(thresh)

            steer_value = 0.0
            frame_count += 1
            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            steer_value = 0.0
            frame_count += 1

            if len(contours) > 0:
                # Find the largest contour
                largest_contour = max(contours, key=cv2.contourArea)

                if cv2.contourArea(largest_contour) > 500:
                    [vx, vy, x, y] = cv2.fitLine(largest_contour, cv2.DIST_HUBER, 0, 0.01, 0.01)
                    vx, vy, x, y = float(vx[0]), float(vy[0]), float(x[0]), float(y[0])

                    if vy != 0:
                        # Draw the green directional line based on the contour's angle
                        bottom_x = int(x + (height - y) * (vx / vy))
                        top_x = int(x + (0 - y) * (vx / vy))
                        cv2.line(img, (bottom_x, height), (top_x, 0), (0, 255, 0), 2)

                        # Calculate target point (with the center of the screen height)
                        target_y = height // 2
                        target_x = int(x + (target_y - y) * (vx / vy))
                        target_x = max(0, min(width, target_x))

                        # Draw the blue steering line
                        car_center_x = width // 2
                        cv2.line(img, (car_center_x, height), (target_x, target_y), (255, 0, 0), 3)

                        # Calculate steering
                        error = (target_x - car_center_x) / car_center_x
                        steer_value = error * 2.0

                        steer_value = max(-1.0, min(1.0, steer_value))

                        # Sharp turn -> slower speed
                        if abs(steer_value) > 0.6:
                            if frame_count % 10 < 5:
                                throttle_value = 1.0
                            else:
                                throttle_value = 0.0
                        else:
                            if frame_count % 10 < 8:
                                throttle_value = 1.0
                            else:
                                throttle_value = 0.0

                        # Draw the yellow contour of the detected line
                        cv2.drawContours(img, [largest_contour], -1, (0, 255, 255), 2)

            # Send the control command to the server
            command = {
                "type": "control",
                "steer": steer_value,
                "throttle": throttle_value
            }
            await ws.send(json.dumps(command))

            # Display the processed video feed
            cv2.imshow("Line follower view", img)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                await ws.send(json.dumps({"type": "control", "steer": 0, "throttle": 0}))
                print("[SAFETY] Manual stop triggered!")
                break


        except Exception as e:
            print(f"[Line Follower] Stream ended or error: {e}")
            break

    cv2.destroyAllWindows()


async def main():
    print(f"[CLIENT] Connecting to backend at {BACKEND_URL}...")

    async with websockets.connect(BACKEND_URL) as ws:
        print("[CLIENT] Successfully connected to server")

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
        pc.addTransceiver("video", direction="recvonly")
        
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

                print(f"[CLIENT] Received message type: {mtype}")

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