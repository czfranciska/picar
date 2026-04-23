import asyncio
import json
import cv2
import numpy as np
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp


def load_config(path="picar_core/linefollower_app/line_config.json"):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] Config file {path} not found. Using defaults.")
        return {}


async def process_video(track, ws, config):
    # Extract config sections
    vis_cfg = config.get("vision", {})
    pid_cfg = config.get("pid", {})
    ctrl_cfg = config.get("control", {})

    print("[Line Follower] Video processing started.")
    cv2.namedWindow("Line follower view", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Line follower view", vis_cfg.get("window_width", 1280), vis_cfg.get("window_height", 720))
    frame_count = 0
    Kp = pid_cfg.get("kp", 2.0)  # Proportional gain
    Ki = pid_cfg.get("ki", 0.0)  # Integral gain
    Kd = pid_cfg.get("kd", 0.0)  # Derivative gain
    previous_error = 0.0
    integral = 0.0
    i_limit = pid_cfg.get("integral_limit", 10.0)

    while True:
        try:
            steer_value = 0.0
            throttle_value = 0.0

            # Grab frame from the incoming WebRTC stream
            frame = await track.recv()
            img = frame.to_ndarray(format="bgr24")
            height, width, _ = img.shape

            # Convert to grayscale, use Gaussian blur, and threshold to find the line
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            k_size = vis_cfg.get("blur_kernel", 5)
            blur = cv2.GaussianBlur(gray, (k_size, k_size), 0)
            ret, thresh = cv2.threshold(blur, vis_cfg.get("threshold_min", 60), vis_cfg.get("threshold_max", 255),
                                        cv2.THRESH_BINARY_INV)

            # Ignore the upper part of the frame to focus on the area near the car
            area_top = int(height * vis_cfg.get("crop_top_percent", 0.35))
            thresh[0:area_top, 0:width] = 0

            steer_value = 0.0
            frame_count += 1
            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            if len(contours) > 0:
                # Find the largest contour
                largest_contour = max(contours, key=cv2.contourArea)

                if cv2.contourArea(largest_contour) > vis_cfg.get("min_contour_area", 500):
                    [vx, vy, x, y] = cv2.fitLine(largest_contour, cv2.DIST_HUBER, 0, 0.01, 0.01)
                    vx, vy, x, y = float(vx[0]), float(vy[0]), float(x[0]), float(y[0])

                    if vy != 0:
                        # Draw the green directional line based on the contour's angle
                        bottom_x = int(x + (height - y) * (vx / vy))
                        top_x = int(x + (0 - y) * (vx / vy))
                        cv2.line(img, (bottom_x, height), (top_x, 0), (0, 255, 0), 2)

                        # Calculate target point
                        look_ahead = vis_cfg.get("look_ahead_percent", 0.5)
                        target_y = int(height * look_ahead)
                        target_x = int(x + (target_y - y) * (vx / vy))
                        target_x = max(0, min(width, target_x))

                        # Draw the blue steering line
                        car_center_x = width // 2
                        cv2.line(img, (car_center_x, height), (target_x, target_y), (255, 0, 0), 3)

                        # Calculate steering
                        error = (target_x - car_center_x) / car_center_x

                        integral += error
                        integral = max(-i_limit, min(i_limit, integral))

                        derivative = error - previous_error
                        steer_value = (Kp * error) + (Ki * integral) + (Kd * derivative)
                        previous_error = error

                        steer_value = max(-1.0, min(1.0, steer_value))

                        t_high = ctrl_cfg.get("throttle_high", 1.0)
                        t_low = ctrl_cfg.get("throttle_low", 0.0)

                        dc_sharp = ctrl_cfg.get("duty_cycle_sharp", 6)
                        dc_straight = ctrl_cfg.get("duty_cycle_straight", 8)

                        # Sharp turn -> slower speed
                        if abs(steer_value) > ctrl_cfg.get("sharp_turn_threshold", 0.6):
                            if frame_count % 10 < dc_sharp:
                                throttle_value = t_high
                            else:
                                throttle_value = t_low
                        else:
                            if frame_count % 10 < dc_straight:
                                throttle_value = t_high
                            else:
                                throttle_value = t_low

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
                await ws.close()
                break


        except Exception as e:
            print(f"[Line Follower] Stream ended or error: {e}")
            break

    cv2.destroyAllWindows()


async def main():
    # Load the config file at startup
    config = load_config()
    backend_url = config.get("backend_url", "ws://mono.inf.elte.hu:3333")

    print(f"[CLIENT] Connecting to backend at {backend_url}...")

    async with websockets.connect(backend_url) as ws:
        print("[CLIENT] Successfully connected to server")

        # Identify as a client to the backend
        await ws.send(json.dumps({"role": "client"}))
        pc = RTCPeerConnection()

        # When the Pi transmits video tracks, start processing them
        @pc.on("track")
        def on_track(track):
            if track.kind == "video":
                # Pass the config dictionary into the video processor
                asyncio.create_task(process_video(track, ws, config))

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