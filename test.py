token = "d72a4424073f930c"


import asyncio
import json
import websockets

async def subscribe(stream_id: str, access_token: str):
    url = f"ws://d72a4424073f930c"
    async with websockets.connect(url) as websocket:
        async for message in websocket:
            yield json.loads(message)