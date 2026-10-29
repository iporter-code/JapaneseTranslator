"""Install build tools inside this workspace, never modify the user's shared SDK."""
from pathlib import Path
import hashlib
import shutil
import urllib.request
import zipfile

root = Path(__file__).resolve().parents[1]
tools = root / '.tools'
tools.mkdir(exist_ok=True)
archive = tools / 'gradle-8.13-bin.zip'
url = 'https://downloads.gradle.org/distributions/gradle-8.13-bin.zip'
if not (tools / 'gradle-8.13/bin/gradle.bat').exists():
    print('Downloading Gradle 8.13', flush=True)
    for attempt in range(5):
        existing = archive.stat().st_size if archive.exists() else 0
        request = urllib.request.Request(url, headers={'Range': f'bytes={existing}-'})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                mode = 'ab' if response.status == 206 else 'wb'
                with archive.open(mode) as output:
                    shutil.copyfileobj(response, output)
            expected_size = int(response.headers.get('Content-Length', '0'))
            if archive.stat().st_size == expected_size + (existing if mode == 'ab' else 0):
                break
        except OSError:
            if attempt == 4:
                raise
    expected = urllib.request.urlopen(url + '.sha256', timeout=60).read().decode().strip().split()[0]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise RuntimeError('Gradle checksum mismatch')
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(tools)
sdk = tools / 'android-sdk'
source = Path('D:/Android/android-sdk/cmdline-tools/11.0')
dest = sdk / 'cmdline-tools/11.0'
if not dest.exists():
    shutil.copytree(source, dest)
print('Build tools prepared', flush=True)
