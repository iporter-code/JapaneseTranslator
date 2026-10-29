"""Download the pinned portable client; verify the official release SHA256."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / '.tools/cloudflared'
VERSION = '2026.10.5'
EXPECTED = 'db4e4c413612a0916f3e1679d3caaf376f94911952d163028fb726fb2e0516a9'


def download(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'jtranslate-setup'})
    return urllib.request.urlopen(request, timeout=90)


def main():
    FOLDER.mkdir(parents=True, exist_ok=True)
    name = 'cloudflared-windows-amd64.exe'
    url = f'https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/{name}'
    expected = EXPECTED  # Published at the corresponding official GitHub release page.
    target = FOLDER / 'cloudflared.exe'
    valid = False
    if target.is_file():
        with target.open('rb') as source:
            valid = hashlib.file_digest(source, 'sha256').hexdigest() == expected
    if not valid:
        part = FOLDER / 'cloudflared.exe.part'
        with download(url) as response, part.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        with part.open('rb') as source:
            actual = hashlib.file_digest(source, 'sha256').hexdigest()
        if actual != expected:
            raise RuntimeError('Tunnel client SHA256 mismatch')
        part.replace(target)
    metadata = {'version': VERSION, 'asset': name, 'source': url, 'sha256': expected}
    (FOLDER / 'manifest.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print(json.dumps(metadata))


if __name__ == '__main__':
    main()
