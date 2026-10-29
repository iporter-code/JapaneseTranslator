import asyncio
from dataclasses import dataclass
import math
import struct
from typing import Callable
import httpx
from .config import Settings

@dataclass
class Recognition:
    segment_id: str
    text: str
    start_ms: int
    end_ms: int
    final: bool

class ProviderError(RuntimeError):
    def __init__(self, code='provider_unavailable'):
        super().__init__(code)
        self.code = code

class MockRecognizer:
    """Fixed DEMO output driven by non-silent input; never transcribes audio."""
    def __init__(self, emit):
        self.emit = emit
        self.samples = 0
        self.voiced = 0
        self.number = 0

    async def start(self):
        pass

    async def push(self, pcm):
        samples = struct.unpack('<' + 'h' * (len(pcm) // 2), pcm)
        self.samples += len(samples)
        rms = math.sqrt(sum(x*x for x in samples) / max(1, len(samples)))
        if rms < 80:
            return
        self.voiced += len(samples)
        final = self.voiced >= 32000
        if self.voiced == len(samples) or final:
            self.emit(Recognition(f'mock_{self.number}', '【演示】今日はここで待っていて。',
                                  max(0, self.samples // 16 - self.voiced // 16),
                                  self.samples // 16, final))
        if final:
            self.number += 1
            self.voiced = 0

    async def close(self):
        pass

class AzureRecognizer:
    def __init__(self, settings, emit):
        self.settings, self.emit = settings, emit
        self.recognizer = self.stream = None
        self.closed = False

    async def start(self):
        import azure.cognitiveservices.speech as sdk
        def create():
            config = sdk.SpeechConfig(subscription=self.settings.speech_key,
                                      region=self.settings.speech_region)
            config.speech_recognition_language = 'ja-JP'
            config.set_profanity(sdk.ProfanityOption.Raw)
            fmt = sdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
            self.stream = sdk.audio.PushAudioInputStream(stream_format=fmt)
            self.recognizer = sdk.SpeechRecognizer(speech_config=config,
                audio_config=sdk.audio.AudioConfig(stream=self.stream))
            def received(event, final):
                result = event.result
                if self.closed or not result.text:
                    return
                # Result offset remains stable across partials for one utterance.
                start = int(result.offset // 10000)
                self.emit(Recognition(f'azure_{start}', result.text, start,
                                      start + int(result.duration // 10000), final))
            self.recognizer.recognizing.connect(lambda e: received(e, False))
            self.recognizer.recognized.connect(lambda e: received(e, True))
            def canceled(event):
                if not self.closed and event.reason == sdk.CancellationReason.Error:
                    self.emit(ProviderError('provider_unavailable'))
            self.recognizer.canceled.connect(canceled)
            self.recognizer.start_continuous_recognition_async().get()
        await asyncio.to_thread(create)

    async def push(self, pcm):
        await asyncio.to_thread(self.stream.write, pcm)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        def stop():
            if self.stream:
                self.stream.close()
            if self.recognizer:
                self.recognizer.stop_continuous_recognition_async().get()
        await asyncio.to_thread(stop)

class Translator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=12)

    async def translate(self, text):
        if self.settings.provider == 'mock':
            return '【演示】今天就在这里等着。（固定测试字幕）'
        try:
            response = await self.client.post(
                self.settings.translator_endpoint.rstrip('/') + '/translate',
                params={'api-version': '3.0', 'from': 'ja', 'to': 'zh-Hans',
                        'profanityAction': 'NoAction'},
                headers={'Ocp-Apim-Subscription-Key': self.settings.translator_key,
                         'Ocp-Apim-Subscription-Region': self.settings.translator_region},
                json=[{'Text': text}])
            if response.status_code in (401, 403):
                raise ProviderError('provider_auth_failed')
            if response.status_code == 429:
                raise ProviderError('provider_rate_limited')
            response.raise_for_status()
            result = response.json()[0]['translations'][0]['text']
            if not isinstance(result, str) or not result:
                raise ProviderError('provider_empty_result')
            return result
        except ProviderError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError):
            raise ProviderError() from None

    async def close(self):
        await self.client.aclose()

class TranslationScheduler:
    """Cancel superseded work; never publish a translation for a stale source."""
    def __init__(self, translate, publish):
        self.translate, self.publish = translate, publish
        self.tasks = {}
        self.revisions = {}
        self.gate = asyncio.Semaphore(2)
        self.closed = False

    def submit(self, event):
        key = event['segment_id']
        previous = self.tasks.pop(key, None)
        if previous:
            previous.cancel()
        if len(self.tasks) >= 32:
            raise ProviderError('provider_overloaded')
        self.revisions[key] = event['source_revision']
        async def work():
            try:
                if not event['asr_final']:
                    await asyncio.sleep(.7)
                async with self.gate:
                    translated = await self.translate(event['source_text'])
                if self.closed or self.revisions.get(key) != event['source_revision']:
                    return
                await self.publish({**event, 'type': 'translation.final' if event['asr_final'] else 'translation.partial',
                                    'translated_text': translated, 'translation_revision': event['source_revision'],
                                    'translation_final': event['asr_final']})
            except ProviderError as exc:
                if not self.closed:
                    await self.publish({'type': 'session.error', 'code': exc.code, 'fatal': False})
            finally:
                if self.tasks.get(key) is asyncio.current_task():
                    self.tasks.pop(key, None)
                    self.revisions.pop(key, None)
        task = asyncio.create_task(work())
        self.tasks[key] = task

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.revisions.clear()
