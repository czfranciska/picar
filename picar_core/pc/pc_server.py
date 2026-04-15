import asyncio
import json
import websockets
from collections import defaultdict

car_connection = None
client_connections = set()
subscriptions = defaultdict(set)

async def update_car_subscriptions():
    # When a client subscribes to a sensor, we need to inform the car of all currently requested sensors.
    if car_connection:
        all_requested = list(subscriptions.keys())
        await car_connection.send(json.dumps({
            "type": "subscribe_sensors",
            "sensors": all_requested
        }))

async def handler(websocket):
    global car_connection

    init_msg = await websocket.recv()
    try:
        data = json.loads(init_msg)
    except Exception:
        return

    role = data.get("role")
    if role == "car":
        car_connection = websocket
        print("[BACKEND] Car connected.")
        await update_car_subscriptions()
        try:
            async for message in websocket:
                try:
                    msg_obj = json.loads(message)
                    if msg_obj.get("type") == "sensor":

                        # Selective forwarding, only send data to clients that subscribed to this sensor
                        for sensor_name, sensor_data in msg_obj.get("data", {}).items():
                            subscribers = subscriptions.get(sensor_name, [])
                            if subscribers:
                                payload = json.dumps({"type": "sensor", "data": {sensor_name: sensor_data}})
                                websockets.broadcast(subscribers, payload)
                    else:

                        # Broadcast other types (like WebRTC signals) to all clients
                        websockets.broadcast(client_connections, message)
                except Exception:
                    continue
        finally:
            car_connection = None
            print("[BACKEND] Car disconnected.")

    elif role == "client":
        client_connections.add(websocket)
        print("[BACKEND] Client connected.")
        try:
            async for message in websocket:
                try:
                    msg_obj = json.loads(message)
                    if msg_obj.get("type") == "subscribe_sensors":
                        requested = msg_obj.get("sensors", [])

                        # Clean up subscriptions for this client
                        for s_set in subscriptions.values():
                            s_set.discard(websocket)

                        # Update subscriptions
                        for s_name in requested:
                            subscriptions[s_name].add(websocket)

                        # Remove any sensors from the keyset that have no subscribers
                        for s_name in list(subscriptions.keys()):
                            if not subscriptions[s_name]:
                                del subscriptions[s_name]

                        await update_car_subscriptions()
                    elif car_connection:
                        await car_connection.send(message)
                except Exception:
                    continue
        finally:
            client_connections.remove(websocket)

            # When a client disconnects, clear its subscriptions
            for s_set in subscriptions.values():
                s_set.discard(websocket)

            for s_name in list(subscriptions.keys()):
                if not subscriptions[s_name]:
                    del subscriptions[s_name]

            await update_car_subscriptions()
            print("[BACKEND] Client disconnected.")


async def main(config_path="pc_config.json"):
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        print(f"[INFO] Loaded configuration from {config_path}")
    except FileNotFoundError:
        print(f"[ERROR] Could not find {config_path}. Using default 0.0.0.0:3333")
        config = {"server": {"host": "0.0.0.0", "port": 3333}}

    host = config.get("server", {}).get("host", "0.0.0.0")
    port = config.get("server", {}).get("port", 3333)

    # Start the WebSocket server
    print(f"[BACKEND] Starting signaling/relay server on ws://{host}:{port}")
    async with websockets.serve(handler, host, port):
        await asyncio.Future()


def start():
    # Synchronous entry point for pyproject.toml scripts.
    import sys
    cfg = sys.argv[1] if len(sys.argv) > 1 else "picar_core/pc/pc_config.json"
    try:
        asyncio.run(main(cfg))
    except KeyboardInterrupt:
        print("\n[BACKEND] Server stopped by user.")

if __name__ == "__main__":
    start()