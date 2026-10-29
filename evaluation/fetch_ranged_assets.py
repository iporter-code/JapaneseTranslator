"""Resumable, hash-checked ranged downloads for constrained model networks."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import hashlib
import json
import os
import shutil
import threading
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CHUNK = 8 * 1024 * 1024
PRINT_LOCK = threading.Lock()
MANIFEST = json.loads((ROOT / 'evaluation/model-manifest.json').read_text(encoding='utf-8'))

def say(value):
    with PRINT_LOCK:
        print(value, flush=True)

def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(CHUNK), b''):
            result.update(block)
    return result.hexdigest()

def fetch(url, target, size=None, expected_sha=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    if size is None:
        probe = urllib.request.Request(url, headers={'Range': 'bytes=0-0'})
        with urllib.request.urlopen(probe, timeout=30) as response:
            content_range = response.headers.get('Content-Range', '')
            size = int(content_range.rsplit('/', 1)[-1]) if '/' in content_range else int(response.headers['Content-Length'])
    if target.exists() and target.stat().st_size == size:
        if not expected_sha or digest(target) == expected_sha:
            say(f'Cached {target.name}')
            return
    parts = ROOT / '.tools/model-parts' / hashlib.sha256(url.encode()).hexdigest()[:16]
    parts.mkdir(parents=True, exist_ok=True)
    ranges = [(start, min(size - 1, start + CHUNK - 1)) for start in range(0, size, CHUNK)]
    completed = 0
    def part(span):
        start, end = span
        path = parts / str(start)
        length = end - start + 1
        if path.exists() and path.stat().st_size == length:
            return
        for attempt in range(6):
            try:
                # Distinct query keys prevent stale CDN redirects for a different range.
                separator = '&' if '?' in url else '?'
                request = urllib.request.Request(url + separator + f'eval_part={start}',
                    headers={'Range': f'bytes={start}-{end}', 'User-Agent': 'LocalModelEvaluation/0.1'})
                with urllib.request.urlopen(request, timeout=90) as response:
                    if response.status != 206 or not response.headers.get('Content-Range', '').startswith(f'bytes {start}-'):
                        raise OSError('Server did not honor requested range')
                    with path.open('wb') as output:
                        shutil.copyfileobj(response, output, 1024 * 1024)
                if path.stat().st_size != length:
                    raise OSError('Truncated range')
                return
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(1 + attempt)
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs = [pool.submit(part, span) for span in ranges]
        for job in as_completed(jobs):
            job.result()
            completed += 1
            if completed % 10 == 0 or completed == len(ranges):
                say(f'{target.name}: {completed}/{len(ranges)} parts')
    assembled = target.with_suffix(target.suffix + '.assembling')
    with assembled.open('wb') as output:
        for start, _ in ranges:
            with (parts / str(start)).open('rb') as source:
                shutil.copyfileobj(source, output)
    if expected_sha and digest(assembled) != expected_sha:
        raise ValueError(f'Checksum mismatch: {target.name}')
    os.replace(assembled, target)
    say(f'Ready {target.name}: {size} bytes, SHA256={digest(target)}')

def whisper():
    original = MANIFEST['asr']
    repo = original['repository']
    names = ['config.json', 'model.bin', 'preprocessor_config.json', 'tokenizer.json', 'vocabulary.json']
    for name in names:
        url = f'https://huggingface.co/{repo}/resolve/{original["revision"]}/{name}'
        path = ROOT / 'evaluation/models/whisper-turbo' / name
        if name == 'model.bin':
            fetch(url, path, original['weight_bytes'], original['weight_sha256'])
        elif not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(url, timeout=60) as response:
                path.write_bytes(response.read())

def translation():
    model = MANIFEST['translation']
    fetch(model['mirror'], ROOT / 'evaluation/models/hy-mt2' / model['weight_file'],
          model['weight_bytes'], model['weight_sha256'])

def runtime():
    archive = ROOT / '.tools/llama-b11979-win-cuda12.zip'
    model = MANIFEST['runtime']
    fetch(model['url'], archive, model['archive_bytes'], model['downloaded_archive_sha256'])
    dest = ROOT / '.tools/llama-b11979'
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip():
            raise ValueError('Corrupt runtime archive')
        for item in bundle.namelist():
            if not (dest / item).resolve().is_relative_to(dest.resolve()):
                raise ValueError('Unsafe archive path')
        bundle.extractall(dest)

def samples():
    data = MANIFEST['dataset']
    fetch(f'https://huggingface.co/datasets/{data["repository"]}/resolve/main/{data["file"]}',
          ROOT / 'evaluation/samples/common_voice_8_ja.parquet', data['bytes'], data['sha256'])

if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(fn) for fn in [whisper, translation, runtime, samples]]
        for job in as_completed(jobs):
            job.result()
