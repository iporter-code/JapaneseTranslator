import asyncio
from pathlib import Path

import pytest

from public_tunnel import QuickTunnel, extract_origin


def test_only_expected_https_tunnel_hostnames_are_accepted():
    assert extract_origin('Visit https://my-test-link.trycloudflare.com to connect') == 'https://my-test-link.trycloudflare.com'
    for line in ('https://my.trycloudflare.com.evil.example', 'http://my.trycloudflare.com',
                 'https://unrelated.example', 'no address yet'):
        assert extract_origin(line) is None


class FakeProcess:
    def __init__(self, line):
        self.returncode = None
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(line.encode())
        self.stdout.feed_eof()
        self.exited = asyncio.Event()
        self.terminated = False
    def terminate(self):
        self.terminated = True
        self.returncode = 0
        self.exited.set()
    async def wait(self):
        await self.exited.wait()
        return self.returncode


@pytest.mark.parametrize('valid', [True, False])
def test_owned_tunnel_process_is_cleaned_on_success_and_startup_error(tmp_path, monkeypatch, valid):
    async def run():
        process = FakeProcess('Address: https://test-one.trycloudflare.com\n' if valid else 'startup failed\n')
        executable = tmp_path / 'cloudflared.exe'
        executable.touch()
        async def spawn(*args, **kwargs):
            assert '--url' in args and 'http://127.0.0.1:9876' in args
            assert '--allowed-mail' not in args  # Native app must not require browser login.
            return process
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
        tunnel = QuickTunnel(executable, 9876)
        if valid:
            assert await tunnel.start() == 'https://test-one.trycloudflare.com'
            await tunnel.close()
        else:
            with pytest.raises(RuntimeError, match='before issuing'):
                await tunnel.start()
        assert process.terminated
        assert tunnel.reader.done()
    asyncio.run(run())
