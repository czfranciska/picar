import asyncio
import json
import websockets

car_connection = None
client_connections = set()

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
        try:
            async for message in websocket:
                websockets.broadcast(client_connections, message)
        finally:
            car_connection = None
            print("[BACKEND] Car disconnected.")

    elif role == "client":
        client_connections.add(websocket)
        print("[BACKEND] Client connected.")
        try:
            async for message in websocket:
                if car_connection:
                    await car_connection.send(message)
        finally:
            client_connections.remove(websocket)
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


    print(f"[BACKEND] Starting signaling/relay server on ws://{host}:{port}")
    async with websockets.serve(handler, host, port):
        await asyncio.Future()


def start():
    # Synchronous entry point for pyproject.toml scripts.
    import sys
    cfg = sys.argv[1] if len(sys.argv) > 1 else "picar-core/pc/pc_config.json"
    asyncio.run(main(cfg))

if __name__ == "__main__":
    start()