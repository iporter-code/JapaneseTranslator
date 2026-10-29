import asyncio
import struct
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.local_runtime import LocalRuntime
from app.main import create_app
from app.providers import ProviderError
from app.streaming import BLOCK, LocalRecognizer, OnlineSegmenter

TOKEN = 'local-unit-test-token-1234567890'
VOICE = struct.pack('<h', 2000) * BLOCK
QUIET = b'\0' * (BLOCK * 2)


def vad(pcm):
    return .9 if pcm[:2] != b'\0\0' else .01


def test_silence_and_short_noise_do_not_transcribe():
    segmenter = OnlineSegmenter(vad)
    assert not segmenter.push(QUIET * 600)
    assert len(segmenter.pre) <= 12800
    assert not segmenter.push(VOICE * 2 + QUIET * 30)
    assert not segmenter.audio


def test_chunk_boundaries_do_not_change_utterances():
    audio = QUIET * 20 + VOICE * 70 + QUIET * 25
    whole = OnlineSegmenter(vad).push(audio)
    segmenter = OnlineSegmenter(vad)
    split = []
    for start in range(0, len(audio), 3200):
        split.extend(segmenter.push(audio[start:start + 3200]))
    assert split == whole
    assert whole[-1].final
    assert all(job.key == whole[0].key for job in whole)
    assert [job.revision for job in whole] == list(range(1, len(whole) + 1))
    assert whole[0].start >= 0
    assert whole[-1].end <= len(audio) // 2


def test_continuous_speech_is_bounded_and_uses_new_ids():
    jobs = OnlineSegmenter(vad, max_seconds=2).push(VOICE * 200)
    finals = [job for job in jobs if job.final]
    assert len(finals) == 3
    assert len({job.key for job in finals}) == 3
    assert all(len(job.pcm) <= (32000 + BLOCK) * 2 for job in jobs)
    assert all(a.end == b.start for a, b in zip(finals, finals[1:]))


class FakeRuntime:
    ready = False
    def __init__(self, settings=None):
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.closed = False

    async def start(self):
        self.ready = True

    def new_vad(self):
        return vad

    async def transcribe(self, pcm, final=True):
        self.calls.append(pcm)
        self.started.set()
        await self.release.wait()
        return '今日は晴れです。'

    async def translate(self, text):
        return '今天是晴天。'

    async def close(self):
        self.closed = True
        self.ready = False


def test_pending_partials_coalesce_and_only_latest_is_emitted():
    async def run():
        runtime = FakeRuntime()
        output = asyncio.Queue()
        recognizer = LocalRecognizer(runtime, output.put_nowait)
        await recognizer.start()
        await recognizer.push(VOICE * 100 + QUIET * 25)
        result = await asyncio.wait_for(output.get(), 1)
        assert result.final and result.text == '今日は晴れです。'
        assert len(runtime.calls) == 1
        await recognizer.close()
    asyncio.run(run())


def test_inflight_partial_is_discarded_when_final_arrives():
    async def run():
        runtime = FakeRuntime()
        runtime.release.clear()
        output = asyncio.Queue()
        recognizer = LocalRecognizer(runtime, output.put_nowait)
        await recognizer.start()
        await recognizer.push(VOICE * 45)
        await asyncio.wait_for(runtime.started.wait(), 1)
        await recognizer.push(VOICE * 25 + QUIET * 25)
        runtime.release.set()
        result = await asyncio.wait_for(output.get(), 1)
        assert result.final
        assert output.empty()
        await recognizer.close()
    asyncio.run(run())


def test_partial_requires_agreement_but_final_can_correct_it():
    async def run():
        runtime = FakeRuntime()
        hypotheses = iter(['木村さんは', '木村さんは写真を', '木村先生は写真を見せた。'])
        decoded = asyncio.Queue()
        async def transcribe(pcm, final=True):
            text = next(hypotheses)
            decoded.put_nowait(text)
            return text
        runtime.transcribe = transcribe
        output = asyncio.Queue()
        recognizer = LocalRecognizer(runtime, output.put_nowait)
        await recognizer.start()
        await recognizer.push(VOICE * 40)
        await asyncio.wait_for(decoded.get(), 1)
        assert output.empty()  # An unconfirmed first decode is never displayed.
        await recognizer.push(VOICE * 40)
        partial = await asyncio.wait_for(output.get(), 1)
        assert partial.text == '木村さんは' and not partial.final
        await recognizer.push(QUIET * 25)
        final = await asyncio.wait_for(output.get(), 1)
        assert final.text == '木村先生は写真を見せた。' and final.final
        await recognizer.close()
    asyncio.run(run())


def test_close_discards_buffers_and_inflight_results():
    async def run():
        runtime = FakeRuntime()
        runtime.release.clear()
        output = []
        recognizer = LocalRecognizer(runtime, output.append)
        await recognizer.start()
        await recognizer.push(VOICE * 45)
        await asyncio.wait_for(runtime.started.wait(), 1)
        await recognizer.close()
        runtime.release.set()
        assert not output and not recognizer.pending and not recognizer.segmenter.audio
    asyncio.run(run())


def test_overload_fails_explicitly_instead_of_growing_audio_queue():
    async def run():
        recognizer = LocalRecognizer(FakeRuntime(), lambda event: None)
        await recognizer.start()
        with pytest.raises(ProviderError, match='provider_overloaded'):
            await recognizer.push((VOICE * 45 + QUIET * 25) * 5)
        await recognizer.close()
    asyncio.run(run())


def test_local_provider_routes_audio_and_closes_shared_runtime():
    runtime = FakeRuntime()
    with TestClient(create_app(Settings(TOKEN, provider='local'), lambda settings: runtime)) as client:
        assert client.get('/health').json() == {
            'status': 'ok', 'provider': 'local', 'demo': False, 'ready': True, 'max_sessions': 1}
        auth = {'Authorization': 'Bearer ' + TOKEN}
        info = client.post('/v1/sessions', headers=auth, json={}).json()
        assert info['demo'] is False
        assert client.post('/v1/sessions', headers=auth, json={}).status_code == 429
        with client.websocket_connect('/v1/sessions/' + info['session_id'] + '/stream',
                headers={'Authorization': 'Bearer ' + info['stream_token']}) as ws:
            from app.protocol import AudioFrame
            ws.send_json({'type': 'stream.start', 'version': 1, 'epoch': 4})
            assert ws.receive_json()['demo'] is False
            pcm = VOICE * 45 + QUIET * 25
            first = 0
            for sequence, start in enumerate(range(0, len(pcm), 3200)):
                data = pcm[start:start + 3200]
                ws.send_bytes(AudioFrame(sequence, first, len(data) // 2, data).encode())
                first += len(data) // 2
            while True:
                event = ws.receive_json()
                assert event['type'] != 'session.error'
                if event['type'] == 'translation.final':
                    assert event['epoch'] == 4
                    assert event['translated_text'] == '今天是晴天。'
                    break
            ws.send_json({'type': 'stream.stop'})
            from starlette.websockets import WebSocketDisconnect
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()  # Wait for cleanup/close before TestClient cancels its task.
    assert runtime.closed


@pytest.mark.parametrize('finish,content,code', [
    ('length', '截断', 'provider_truncated_result'),
    ('stop', '', 'provider_empty_result'), ('stop', '正常译文', None)])
def test_local_translation_rejects_truncation_and_empty_output(finish, content, code):
    async def run():
        runtime = LocalRuntime(Settings(TOKEN))
        def respond(request):
            assert '保留原文' in request.content.decode('utf-8')
            return httpx.Response(200, json={'choices': [
                {'finish_reason': finish, 'message': {'content': content}}]})
        runtime.client = httpx.AsyncClient(base_url='http://test', transport=httpx.MockTransport(respond))
        try:
            if code:
                with pytest.raises(ProviderError, match=code):
                    await runtime.translate('こんにちは。')
            else:
                assert await runtime.translate('こんにちは。') == content
        finally:
            await runtime.close()
    asyncio.run(run())


def test_cancelled_native_inference_holds_gate_and_never_overlaps():
    pytest.importorskip('numpy')
    async def run():
        entered = threading.Event()
        release = threading.Event()
        count = 0
        class Model:
            def transcribe(self, *args, **kwargs):
                nonlocal count
                count += 1
                entered.set()
                assert release.wait(timeout=3)
                return [SimpleNamespace(text='日語', no_speech_prob=0, avg_logprob=0)], None
        runtime = LocalRuntime(Settings(TOKEN))
        runtime.model = Model()
        first = asyncio.create_task(runtime.transcribe(VOICE))
        second = None
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            first.cancel()
            second = asyncio.create_task(runtime.transcribe(VOICE))
            await asyncio.sleep(.02)
            assert runtime.asr_gate.locked() and count == 1
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert await asyncio.wait_for(second, 1) == '日語'
            assert count == 2
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
            await runtime.close()
    asyncio.run(run())


def test_startup_cancellation_waits_for_native_loader_before_cleanup():
    async def run():
        entered, release = threading.Event(), threading.Event()
        initialized = False
        cleaned = []
        runtime = LocalRuntime(Settings(TOKEN))
        def load():
            nonlocal initialized
            entered.set()
            assert release.wait(timeout=3)
            initialized = True
        async def close():
            cleaned.append(initialized)
        runtime._load_models = load
        runtime.close = close
        task = asyncio.create_task(runtime.start())
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            await asyncio.sleep(.02)
            assert not cleaned
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cleaned == [True]
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())
