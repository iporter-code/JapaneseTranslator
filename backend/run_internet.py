"""Start resident models and an owned public HTTPS tunnel with cleanup."""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import socket
import sys

from run_local import ROOT, ascii_workspace, prepare_environment


async def serve(args, root):
    import uvicorn
    from app.config import Settings
    from app.main import create_app
    from public_tunnel import QuickTunnel, wait_public_ready
    with socket.socket() as check:
        try:
            check.bind(('127.0.0.1', args.port))
        except OSError:
            raise RuntimeError('Backend port is busy; close the previous backend first') from None
    tunnel = QuickTunnel(ROOT / '.tools/cloudflared/cloudflared.exe', args.port)
    task = server = None
    state = ROOT / '.tools/internet-endpoint.json'
    try:
        origin = await tunnel.start()
        settings = replace(Settings.from_env(), public_base_url=origin)
        server = uvicorn.Server(uvicorn.Config(create_app(settings), host='127.0.0.1', port=args.port,
            ws_max_size=16384, access_log=False, timeout_graceful_shutdown=15))
        task = asyncio.create_task(server.serve())
        async with asyncio.timeout(120):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError('Model backend startup failed')
                await asyncio.sleep(.1)
        await wait_public_ready(origin)
        state.write_text(json.dumps({'origin': origin, 'provider': 'local', 'ready': True,
            'temporary_address': True, 'phone_extra_app_required': False}, indent=2), encoding='utf-8')
        print(f'Phone server address: {origin}', flush=True)
        print('Ready: use the existing translation APK and your backend app access token. '
              'No Tailscale/VPN app is needed on the phone.', flush=True)
        print('Temporary URL changes on restart. Keep this process and PC running.', flush=True)
        if args.self_test:
            import sys
            sys.path.insert(0, str(ROOT / 'evaluation'))
            import streaming_smoke
            await streaming_smoke.main(argparse.Namespace(base=origin, samples=3, repeat=1,
                output='streaming-internet.json'))
            print('Internet audio translation self-test passed; service remains running.', flush=True)
        if args.check_only:
            return
        closed = asyncio.create_task(tunnel.process.wait())
        try:
            done, _ = await asyncio.wait([task, closed], return_when=asyncio.FIRST_COMPLETED)
            if closed in done and not task.done():
                raise RuntimeError('Internet tunnel stopped; restart to obtain a new address')
            if task in done:
                await task
        finally:
            closed.cancel()
            await asyncio.gather(closed, return_exceptions=True)
    finally:
        # Remove the external route before shutting down native model work.
        await tunnel.close()
        if server:
            server.should_exit = True
        if task:
            await asyncio.wait_for(task, 25)
        if state.exists():
            # The last URL is retained as a non-secret troubleshooting hint, not active.
            record = json.loads(state.read_text(encoding='utf-8'))
            record['ready'] = False
            state.write_text(json.dumps(record, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--device', choices=['cuda', 'cpu'], default='cuda')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    with ascii_workspace() as root:
        prepare_environment(root, args.device)
        asyncio.run(serve(args, root))


if __name__ == '__main__':
    main()
