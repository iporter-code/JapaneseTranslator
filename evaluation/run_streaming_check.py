"""Own startup, real network replay and graceful shutdown in one reproducible run."""
import argparse
import asyncio
import json
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from run_local import ascii_workspace, prepare_environment
import streaming_smoke


async def check(args):
    import uvicorn
    # Refuse an existing backend so a test never uses an unrelated process.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    server = uvicorn.Server(uvicorn.Config('app.main:create_app', factory=True,
        host='127.0.0.1', port=args.port, ws_max_size=16384, access_log=False,
        log_level='warning', timeout_graceful_shutdown=15))
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(120):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError('Local backend did not start')
                await asyncio.sleep(.1)
        print('Real model backend ready; replaying public audio at playback speed.', flush=True)
        await streaming_smoke.main(argparse.Namespace(base=f'http://127.0.0.1:{args.port}',
            samples=args.samples, repeat=args.repeat, output=args.output))
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 25)
    from app.config import Settings
    for port in (args.port, Settings.from_env().local_llama_port):
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', port)) != 0, 'Owned service did not release its port'
    print(json.dumps({'graceful_shutdown': 'passed', 'owned_ports_released': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--samples', type=int, default=10)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--output', default='streaming-local-stable.json')
    args = parser.parse_args()
    with ascii_workspace() as root:
        prepare_environment(root, 'cuda')
        asyncio.run(check(args))
