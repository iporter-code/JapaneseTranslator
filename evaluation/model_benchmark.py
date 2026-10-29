"""Reproducible offline ASR/translation evaluation on this computer."""
import argparse
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import re
import statistics
import socket
import subprocess
import sys
import time
import unicodedata
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / 'evaluation/results'
RESULTS.mkdir(exist_ok=True)
DLL_HANDLES = []
RUN_LABEL = ''
ASR_BEAM_SIZE = 1

def write_json(name, data):
    if RUN_LABEL:
        path = Path(name)
        name = f'{path.stem}-{RUN_LABEL}{path.suffix}'
    (RESULTS / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).lower()
                   if not unicodedata.category(c).startswith(('P', 'Z', 'S')))

def edit_distance(reference, hypothesis):
    row = list(range(len(hypothesis) + 1))
    for i, char in enumerate(reference, 1):
        next_row = [i]
        for j, candidate in enumerate(hypothesis, 1):
            next_row.append(min(row[j] + 1, next_row[j - 1] + 1, row[j - 1] + (char != candidate)))
        row = next_row
    return row[-1]

def gpu_snapshot():
    output = subprocess.check_output(['nvidia-smi', '--query-gpu=name,memory.total,memory.used,memory.free',
                                     '--format=csv,noheader'], text=True)
    return output.strip()

def configure_cuda(root):
    nvidia = root / 'evaluation/.venv-models/Lib/site-packages/nvidia'
    folders = [p for p in nvidia.rglob('bin') if p.is_dir()]
    for folder in folders:
        DLL_HANDLES.append(os.add_dll_directory(str(folder)))
    os.environ['PATH'] = os.pathsep.join(str(p) for p in folders) + os.pathsep + os.environ['PATH']
    return [str(p) for p in folders]

@contextmanager
def ascii_root():
    if str(ROOT).isascii():
        yield ROOT
        return
    for letter in ['Q', 'R', 'S', 'T', 'U', 'V']:
        drive = letter + ':'
        if not Path(drive + '/').exists():
            subprocess.run(['subst', drive, str(ROOT)], check=True)
            try:
                yield Path(drive + '/')
            finally:
                subprocess.run(['subst', drive, '/D'], check=False)
            return
    raise RuntimeError('No free drive for the ASCII model path')

def prepare_samples(root, count):
    import pyarrow.parquet as pq
    import soundfile as sf
    from faster_whisper.audio import decode_audio
    parquet = root / 'evaluation/samples/common_voice_8_ja.parquet'
    records = []
    index = 0
    for batch in pq.ParquetFile(parquet).iter_batches(batch_size=24):
        for row in batch.to_pylist():
            source_index = index
            index += 1
            reference = next((row.get(key) for key in ['text', 'sentence', 'transcription', 'transcript'] if row.get(key)), None)
            audio = row.get('audio')
            if not reference or not audio or not audio.get('bytes'):
                continue
            decoded = decode_audio(io.BytesIO(audio['bytes']), sampling_rate=16000)
            duration = len(decoded) / 16000
            if not 2 <= duration <= 15:
                continue
            filename = f'ja_{source_index:04d}.wav'
            sf.write(root / 'evaluation/samples' / filename, decoded, 16000, subtype='PCM_16')
            records.append({'id': source_index, 'file': filename, 'reference': reference, 'duration_seconds': duration})
            if len(records) >= count:
                write_json('samples.json', {'dataset': 'japanese-asr/ja_asr.common_voice_8_0',
                    'split': 'test', 'selection': 'first rows with audio duration 2..15 seconds', 'rows': records})
                return records
    if not records:
        raise RuntimeError(f'No samples: columns={pq.ParquetFile(parquet).schema.names}')
    return records

def run_asr(root, samples, device='cuda'):
    from faster_whisper import WhisperModel
    started = time.perf_counter()
    model = WhisperModel(str(root / 'evaluation/models/whisper-turbo'), device=device,
        compute_type='int8_float16' if device == 'cuda' else 'int8', cpu_threads=8, num_workers=1,
        local_files_only=True)
    load_seconds = time.perf_counter() - started
    print(f'ASR loaded in {load_seconds:.2f}s; {gpu_snapshot()}', flush=True)
    output = []
    # Warmup is deliberately timed and reported separately.
    started = time.perf_counter()
    warm, _ = model.transcribe(str(root / 'evaluation/samples' / samples[0]['file']),
        language='ja', task='transcribe', beam_size=ASR_BEAM_SIZE, condition_on_previous_text=False, vad_filter=False)
    list(warm)
    warmup = time.perf_counter() - started
    for sample in samples:
        started = time.perf_counter()
        segments, info = model.transcribe(str(root / 'evaluation/samples' / sample['file']),
            language='ja', task='transcribe', beam_size=ASR_BEAM_SIZE, condition_on_previous_text=False,
            vad_filter=False, temperature=0)
        text = ''.join(segment.text for segment in segments).strip()
        elapsed = time.perf_counter() - started
        ref, hyp = normalize(sample['reference']), normalize(text)
        result = {**sample, 'hypothesis': text, 'seconds': elapsed, 'rtf': elapsed / sample['duration_seconds'],
                  'edit_distance': edit_distance(ref, hyp), 'reference_characters': len(ref)}
        output.append(result)
        print(f'ASR {sample["id"]}: {elapsed:.3f}s / {sample["duration_seconds"]:.2f}s audio', flush=True)
    report = {'model': 'Whisper large-v3-turbo / CTranslate2', 'device': device,
              'compute_type': 'int8_float16' if device == 'cuda' else 'int8',
              'load_seconds': load_seconds, 'warmup_seconds': warmup, 'beam_size': ASR_BEAM_SIZE, 'rows': output,
              'weighted_cer': sum(r['edit_distance'] for r in output) / sum(r['reference_characters'] for r in output),
              'aggregate_rtf': sum(r['seconds'] for r in output) / sum(r['duration_seconds'] for r in output),
              'gpu_after': gpu_snapshot()}
    write_json('asr.json', report)
    return model, report

TRANSLATION_CASES = [
    ('simple', '今日はここで待っていて。', '今天就在这里等着。'),
    ('negation', '明日は来なくてもいいですが、電話だけはしてください。', '明天可以不来，但请务必打个电话。'),
    ('not_yet', '彼女はまだ来ていない。', '她还没来。'),
    ('colloquial', 'ちょっと待って、それは聞いてないよ。', '等一下，这件事我可没听说过。'),
    ('profanity', 'くそっ、また失敗した。', '可恶，又失败了。'),
    ('adult_label', 'この動画には成人向けの表現が含まれています。', '这个视频包含成人向的表达。'),
    ('consent', '嫌なら、無理に続けなくていい。', '不愿意的话，不用勉强继续。'),
    ('sexual_health', '避妊具を正しく使用してください。', '请正确使用避孕用具。'),
    ('romance', '彼は彼女の手をそっと握った。', '他轻轻握住了她的手。'),
    ('numbers', '午後三時半までに二千五百円を払ってください。', '请在下午三点半之前支付两千五百日元。'),
]

def completion(prompt, port=8788):
    data = json.dumps({'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0,
                       'max_tokens': 256, 'stream': False, 'cache_prompt': False}).encode()
    request = urllib.request.Request(f'http://127.0.0.1:{port}/v1/chat/completions', data=data,
                                     headers={'Content-Type': 'application/json'})
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    return result, time.perf_counter() - started

@contextmanager
def translation_server(root, log_name='translation-server.log', gpu_layers=99):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        try:
            reservation.bind(('127.0.0.1', 8788))
        except OSError:
            raise RuntimeError('Port 8788 is in use; refusing to test an unrelated local service') from None
    runtime = next((root / '.tools/llama-b11979').rglob('llama-server.exe'))
    model = root / 'evaluation/models/hy-mt2/Hy-MT2-1.8B-Q4_K_M.gguf'
    if RUN_LABEL:
        log_name = f'translation-server-{RUN_LABEL}.log'
    log = (RESULTS / log_name).open('w', encoding='utf-8')
    env = os.environ.copy()
    devices = subprocess.run([str(runtime), '--list-devices'], capture_output=True,
        encoding='utf-8', errors='replace', env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    device_text = devices.stdout + devices.stderr
    if gpu_layers > 0 and 'CUDA0' not in device_text:
        log.close()
        raise RuntimeError('CUDA backend is not available: ' + device_text[-1200:])
    # CUDA runtime DLLs are kept in the isolated test environment.
    args = [str(runtime), '-m', str(model), '--host', '127.0.0.1', '--port', '8788',
            '-ngl', str(gpu_layers), '-c', '2048', '--parallel', '1', '--threads', '8', '-lv', '4']
    process = subprocess.Popen(args, stdout=log, stderr=log, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    start = time.perf_counter()
    try:
        while time.perf_counter() - start < 120:
            if process.poll() is not None:
                raise RuntimeError(f'Translation server exited ({process.returncode}); see {log_name}')
            try:
                with urllib.request.urlopen('http://127.0.0.1:8788/health', timeout=2) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(.5)
        else:
            raise TimeoutError('Translation model startup timed out')
        yield {'load_seconds': time.perf_counter()-start, 'pid': process.pid, 'command': args,
               'available_devices': device_text, 'requested_gpu_layers': gpu_layers}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
        log.close()

def run_translation():
    rows = []
    warmup, warm_seconds = completion('将以下文本翻译成中文，注意只需要输出翻译后的结果，不要额外解释：\nこんにちは。')
    for case, source, reference in TRANSLATION_CASES:
        result, elapsed = completion('将以下文本翻译成中文，注意只需要输出翻译后的结果，不要额外解释：\n' + source)
        content = result['choices'][0]['message'].get('content', '')
        rows.append({'case': case, 'source': source, 'reference': reference, 'translation': content,
                     'seconds': elapsed, 'usage': result.get('usage'), 'timings': result.get('timings'),
                     'finish_reason': result['choices'][0].get('finish_reason')})
        print(f'Translation {case}: {elapsed:.3f}s', flush=True)
    report = {'model': 'tencent/Hy-MT2-1.8B-GGUF', 'quantization': 'Q4_K_M',
              'warmup_seconds': warm_seconds, 'rows': rows,
              'median_seconds': statistics.median(r['seconds'] for r in rows),
              'max_seconds': max(r['seconds'] for r in rows), 'gpu_after': gpu_snapshot()}
    write_json('translation.json', report)
    return report

def run_pipeline(root, model, samples):
    rows = []
    for sample in samples[:5]:
        start = time.perf_counter()
        segments, _ = model.transcribe(str(root / 'evaluation/samples' / sample['file']),
            language='ja', beam_size=ASR_BEAM_SIZE, condition_on_previous_text=False, vad_filter=False, temperature=0)
        text = ''.join(segment.text for segment in segments).strip()
        asr_seconds = time.perf_counter() - start
        translated, translation_seconds = completion('将以下文本翻译成中文，注意只需要输出翻译后的结果，不要额外解释：\n' + text)
        rows.append({**sample, 'recognized': text, 'translation': translated['choices'][0]['message'].get('content', ''),
                     'asr_seconds': asr_seconds, 'translation_seconds': translation_seconds,
                     'total_seconds_after_audio_ready': time.perf_counter() - start})
        print(f'Pipeline {sample["id"]} ready', flush=True)
    report = {'both_models_resident': True, 'gpu': gpu_snapshot(), 'rows': rows,
              'note': 'These are whole-clip tests; total does not include buffering, live endpointing or phone network.'}
    write_json('pipeline.json', report)
    return report

def main():
    global RUN_LABEL, ASR_BEAM_SIZE
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=['all', 'asr', 'translation'], default='all')
    parser.add_argument('--samples', type=int, default=10)
    parser.add_argument('--device', choices=['cuda', 'cpu'], default='cuda')
    parser.add_argument('--translation-gpu-layers', type=int, default=99)
    parser.add_argument('--label', default='')
    parser.add_argument('--beam-size', type=int, default=1)
    args = parser.parse_args()
    RUN_LABEL = args.label
    ASR_BEAM_SIZE = args.beam_size
    write_json('environment.json', {'python': sys.version, 'gpu_before': gpu_snapshot(), 'time': time.strftime('%Y-%m-%d %H:%M:%S')})
    with ascii_root() as root:
        print('CUDA paths:', configure_cuda(root), flush=True)
        model = None
        if args.stage in ('all', 'asr'):
            samples = prepare_samples(root, args.samples)
            model, asr = run_asr(root, samples, args.device)
        if args.stage in ('all', 'translation'):
            with translation_server(root, gpu_layers=args.translation_gpu_layers) as server:
                write_json('translation_startup.json', server)
                run_translation()
                if model is not None:
                    run_pipeline(root, model, samples)

if __name__ == '__main__':
    main()
