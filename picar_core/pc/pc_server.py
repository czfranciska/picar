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


async def main():
    print("[BACKEND] Starting signaling/relay server on port 3333")
    async with websockets.serve(handler, "0.0.0.0", 3333):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())