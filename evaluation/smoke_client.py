"""Local mock end-to-end smoke test; synthesizes tone, never uploads private media."""
import asyncio
import json
import math
from pathlib import Path
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import httpx
from dotenv import dotenv_values
from websockets.asyncio.client import connect
from app.protocol import AudioFrame

async def main():
    root = Path(__file__).resolve().parents[1]
    token = dotenv_values(root / 'backend/.env')['APP_ACCESS_TOKEN']
    auth = {'Authorization': 'Bearer ' + token}
    async with httpx.AsyncClient(trust_env=False) as http:
        response = await http.post('http://127.0.0.1:8765/v1/sessions', headers=auth, json={})
        response.raise_for_status()
        session = response.json()
        if not session['demo']:
            await http.delete('http://127.0.0.1:8765/v1/sessions/' + session['session_id'], headers=auth)
            raise RuntimeError('Smoke client only runs against the mock provider')
        started = time.monotonic()
        try:
            async with connect(session['stream_url'], additional_headers={
                    'Authorization': 'Bearer ' + session['stream_token']}) as ws:
                await ws.send(json.dumps({'type': 'stream.start', 'version': 1, 'epoch': 1}))
                assert json.loads(await ws.recv())['type'] == 'stream.ready'
                async def upload():
                    for frame in range(30):
                        samples = [int(math.sin(2 * math.pi * 440 * (frame * 1600 + n) / 16000) * 5000) for n in range(1600)]
                        await ws.send(AudioFrame(frame, frame * 1600, 1600, struct.pack('<1600h', *samples)).encode())
                        await asyncio.sleep(.1)
                task = asyncio.create_task(upload())
                try:
                    while True:
                        event = json.loads(await asyncio.wait_for(ws.recv(), 8))
                        if event['type'] == 'translation.final':
                            assert '演示' in event['translated_text']
                            print(json.dumps({'mock_chain': 'passed', 'elapsed_seconds': round(time.monotonic() - started, 2),
                                              'real_translation_tested': False}))
                            break
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await ws.send(json.dumps({'type': 'stream.stop'}))
        finally:
            await http.delete('http://127.0.0.1:8765/v1/sessions/' + session['session_id'], headers=auth)

if __name__ == '__main__':
    asyncio.run(main())
