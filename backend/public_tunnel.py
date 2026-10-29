"""Own one temporary HTTPS tunnel. No client-side VPN or external account."""
import asyncio
from collections import deque
import json
import os
from pathlib import Path
import re
import subprocess
import time

URL_PATTERN = re.compile(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com(?![a-z0-9.-])')


def extract_origin(line):
    match = URL_PATTERN.search(line)
    return match.group(0) if match else None


class QuickTunnel:
    def __init__(self, executable, backend_port=8765):
        self.executable = str(executable)
        self.backend_port = backend_port
        self.process = self.reader = None
        self.origin = None
        self.lines = deque(maxlen=20)
        self.available = asyncio.Event()

    async def start(self):
        if not Path(self.executable).is_file():
            raise RuntimeError('Run scripts/prepare-cloudflared.py first')
        config = Path(self.executable).parent / 'quick-empty.yml'
        config.write_text('{}\n', encoding='utf-8')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        self.process = await asyncio.create_subprocess_exec(self.executable,
            'tunnel', '--config', str(config), '--no-autoupdate',
            '--url', f'http://127.0.0.1:{self.backend_port}', stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, creationflags=flags)
        self.reader = asyncio.create_task(self._read())
        try:
            await asyncio.wait_for(self.available.wait(), 60)
            if not self.origin:
                raise RuntimeError('Tunnel exited before issuing an HTTPS address: ' + '\n'.join(self.lines))
            return self.origin
        except BaseException:
            await self.close()
            raise

    async def _read(self):
        while line := await self.process.stdout.readline():
            text = line.decode('utf-8', errors='replace').strip()
            self.lines.append(text)
            origin = extract_origin(text)
            if origin:
                self.origin = origin
                self.available.set()
        self.available.set()

    async def close(self):
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), 8)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader:
            await self.reader


async def wait_public_ready(origin, expected_provider='local', seconds=45):
    import httpx
    deadline = time.monotonic() + seconds
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(origin + '/health')
                if response.status_code == 200:
                    body = response.json()
                    if body.get('provider') == expected_provider and body.get('ready'):
                        return body
            except (httpx.HTTPError, ValueError):
                pass
            await asyncio.sleep(1)
    raise RuntimeError('HTTPS tunnel did not reach the model backend; check Internet connectivity')
