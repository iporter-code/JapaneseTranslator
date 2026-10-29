"""Replay PUBLIC test WAVs in real time over the same protocol used by Android.

Writes public sample text to an explicit evaluation report, not backend logs.
Never scans personal files and never reads the access token into stdout.
"""
import argparse
import asyncio
import json
import math
from pathlib import Path
import statistics
import sys
import time
import wave
from datetime import datetime, timezone

import httpx
from dotenv import dotenv_values
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.protocol import AudioFrame
from app.streaming import (PRE_ROLL_SECONDS, END_SILENCE_SECONDS,
                           PARTIAL_INTERVAL_SECONDS, MAX_UTTERANCE_SECONDS, MIN_VOICE_SECONDS)


async def stream(http, base, auth, pcm, epoch, expect_text=True, transport_stats=None):
    response = await http.post(base + '/v1/sessions', headers=auth, json={})
    response.raise_for_status()
    info = response.json()
    if info['provider'] != 'local' or info['demo']:
        await http.delete(base + '/v1/sessions/' + info['session_id'], headers=auth)
        raise RuntimeError('This client requires the real local provider')
    events = []
    compress_audio = 'zlib' in info.get('audio_compression', [])
    sent_bytes = 0
    try:
        async with connect(info['stream_url'], additional_headers={
                'Authorization': 'Bearer ' + info['stream_token']}, open_timeout=10) as ws:
            await ws.send(json.dumps({'type': 'stream.start', 'version': 1, 'epoch': epoch}))
            ready = json.loads(await asyncio.wait_for(ws.recv(), 25))
            assert ready['type'] == 'stream.ready' and not ready['demo']
            began = time.perf_counter()
            async def upload():
                nonlocal sent_bytes
                for sequence, start in enumerate(range(0, len(pcm), 3200)):
                    data = pcm[start:start + 3200]
                    # Send only after this entire 100 ms frame would have been captured.
                    await asyncio.sleep(max(0, began + (start + len(data)) / 32000 - time.perf_counter()))
                    packet = AudioFrame(sequence, start // 2, len(data) // 2, data).encode(compress=compress_audio)
                    sent_bytes += len(packet)
                    await ws.send(packet)
                # Let the final audio play, then allow native work + translations to settle.
                await asyncio.sleep(max(0, began + len(pcm) / 32000 - time.perf_counter()) + 3)
                await ws.send(json.dumps({'type': 'ping'}))
            uploader = asyncio.create_task(upload())
            try:
                while True:
                    event = json.loads(await asyncio.wait_for(ws.recv(), len(pcm) / 32000 + 15))
                    elapsed = time.perf_counter() - began
                    assert event['epoch'] == epoch
                    if event['type'] == 'session.error':
                        raise RuntimeError(event['code'])
                    if event['type'] == 'pong':
                        break
                    event.pop('session_id', None)
                    event['received_seconds'] = elapsed
                    event['lag_after_event_audio_end_seconds'] = elapsed - event.get('end_audio_ms', 0) / 1000
                    events.append(event)
                await uploader
                finals = [event for event in events if event['type'] == 'translation.final']
                assert bool(finals) == expect_text, 'Unexpected presence/absence of final subtitles'
                await ws.send(json.dumps({'type': 'stream.stop'}))
                await asyncio.wait_for(ws.wait_closed(), 12)
            finally:
                uploader.cancel()
                await asyncio.gather(uploader, return_exceptions=True)
    finally:
        await http.delete(base + '/v1/sessions/' + info['session_id'], headers=auth)
    if transport_stats is not None:
        transport_stats.update({'zlib_negotiated': compress_audio, 'application_packet_bytes': sent_bytes,
            'raw_pcm_packet_bytes': len(pcm) + ((len(pcm) + 3199) // 3200) * 24,
            'note': 'Application packet size only; excludes WebSocket/TLS and their compression.'})
    return events


async def main(args):
    auth = {'Authorization': 'Bearer ' + dotenv_values(ROOT / 'backend/.env')['APP_ACCESS_TOKEN']}
    paths = sorted((ROOT / 'evaluation/samples').glob('ja_*.wav'))[:args.samples]
    if not paths:
        raise RuntimeError('Prepare public evaluation WAVs first')
    pcm = bytearray(b'\0' * 12800)  # 400 ms initial silence.
    clips = []
    for _ in range(args.repeat):
        for path in paths:
            with wave.open(str(path), 'rb') as audio:
                assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
                data = audio.readframes(audio.getnframes())
            clips.append({'file': path.name, 'start_seconds': len(pcm) / 32000,
                          'end_seconds': (len(pcm) + len(data)) / 32000})
            pcm.extend(data)
            pcm.extend(b'\0' * 38400)  # 1.2 seconds between clips.
    async with httpx.AsyncClient(trust_env=False, timeout=15) as http:
        health = (await http.get(args.base + '/health')).json()
        transport_stats = {}
        first = await stream(http, args.base, auth, bytes(pcm), epoch=1, transport_stats=transport_stats)
        # A new session after stop must not receive old text; silence must not invent dialogue.
        silence = await stream(http, args.base, auth, b'\0' * (32000 * 6), epoch=2, expect_text=False)
    finals = [event for event in first if event['type'] == 'translation.final']
    lags = sorted(event['lag_after_event_audio_end_seconds'] for event in finals)
    coverage = []
    for clip in clips:
        matches = [event for event in finals
                   if event['start_audio_ms'] / 1000 < clip['end_seconds']
                   and event['end_audio_ms'] / 1000 > clip['start_seconds']]
        coverage.append({**clip, 'final_segments': len(matches),
            'last_translation_after_clip_end_seconds': max((event['received_seconds'] - clip['end_seconds']
                                                          for event in matches), default=None)})
    assert all(clip['final_segments'] for clip in coverage), 'A replayed public clip received no final subtitle'
    manifest = json.loads((ROOT / 'evaluation/model-manifest.json').read_text(encoding='utf-8'))
    report = {'tested_at_utc': datetime.now(timezone.utc).isoformat(),
        'provider_health': health, 'audio_seconds': len(pcm) / 32000, 'clips': coverage,
        'transport': transport_stats,
        'frame_pacing': 'send_after_full_frame_capture',
        'streaming_parameters': {'pre_roll_seconds': PRE_ROLL_SECONDS,
            'end_silence_seconds': END_SILENCE_SECONDS, 'partial_interval_seconds': PARTIAL_INTERVAL_SECONDS,
            'max_utterance_seconds': MAX_UTTERANCE_SECONDS, 'min_voice_seconds': MIN_VOICE_SECONDS,
            'stable_prefix_min_characters': 4, 'confirmed_short_interjections_allowed': True},
        'models': {'asr': manifest['asr'], 'translation': manifest['translation']},
        'translation_final_count': len(finals), 'events': first,
        'silence_new_epoch_events': silence, 'phone_tested': False,
        'median_lag_after_event_audio_end_seconds': statistics.median(lags),
        'p95_lag_after_event_audio_end_seconds': lags[math.ceil(len(lags) * .95) - 1],
        'p95_method': 'nearest_rank',
        'note': 'Real-time loopback replay; end_audio_ms includes VAD trailing silence. '
                'Not phone latency, semantic accuracy, or adult-film validation.'}
    output = ROOT / 'evaluation/results' / args.output
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('audio_seconds', 'translation_final_count',
        'median_lag_after_event_audio_end_seconds', 'p95_lag_after_event_audio_end_seconds', 'phone_tested')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8765')
    parser.add_argument('--samples', type=int, default=10)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--output', default='streaming-local.json')
    asyncio.run(main(parser.parse_args()))
