"""Check the current configured public HTTPS/WSS endpoint, never log tokens."""
import asyncio
import json
from pathlib import Path
import sys

import httpx
from dotenv import dotenv_values
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]


async def main():
    state = json.loads((ROOT / '.tools/internet-endpoint.json').read_text(encoding='utf-8'))
    origin = state['origin']
    auth = {'Authorization': 'Bearer ' + dotenv_values(ROOT / 'backend/.env')['APP_ACCESS_TOKEN']}
    async with httpx.AsyncClient(timeout=12, trust_env=False) as http:
        health = await http.get(origin + '/health')
        health.raise_for_status()
        response = await http.post(origin + '/v1/sessions', headers=auth, json={})
        response.raise_for_status()
        info = response.json()
        try:
            async with connect(info['stream_url'], additional_headers={
                    'Authorization': 'Bearer ' + info['stream_token']}, open_timeout=12, compression=None) as ws:
                await ws.send(json.dumps({'type': 'stream.start', 'version': 1, 'epoch': 1}))
                ready = json.loads(await asyncio.wait_for(ws.recv(), 10))
                assert ready['type'] == 'stream.ready'
                await ws.send(json.dumps({'type': 'ping'}))
                assert json.loads(await asyncio.wait_for(ws.recv(), 10))['type'] == 'pong'
                await ws.send(json.dumps({'type': 'stream.stop'}))
                await asyncio.wait_for(ws.wait_closed(), 10)
        finally:
            await http.delete(origin + '/v1/sessions/' + info['session_id'], headers=auth)
    report = {'origin': origin, 'health': health.status_code, 'session': response.status_code,
              'stream_ready': True, 'heartbeat': 'pong', 'phone_tested': False}
    (ROOT / 'evaluation/results/connection-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    asyncio.run(main())
