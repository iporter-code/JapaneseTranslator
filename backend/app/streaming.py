"""Bounded online utterances. PCM and subtitle text remain in memory only."""
import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import time

from .providers import ProviderError, Recognition
from .interjections import interjection_translation

RATE = 16000
BLOCK = 512  # Silero v6 expects 32 ms at 16 kHz.
PRE_ROLL_SECONDS = .4
END_SILENCE_SECONDS = .32
MIN_VOICE_SECONDS = .096
PARTIAL_INTERVAL_SECONDS = .8
MAX_UTTERANCE_SECONDS = 8.0


@dataclass
class AudioJob:
    key: str
    revision: int
    pcm: bytes
    start: int
    end: int
    final: bool


class OnlineSegmenter:
    """Injected VAD keeps segmentation tests independent of models/GPU."""
    def __init__(self, vad, partial_seconds=PARTIAL_INTERVAL_SECONDS, max_seconds=MAX_UTTERANCE_SECONDS):
        self.vad = vad
        self.partial_samples = int(partial_seconds * RATE)
        self.max_samples = int(max_seconds * RATE)
        self.remainder = bytearray()
        self.pre = bytearray()
        self.audio = bytearray()
        self.cursor = self.start = self.voice_samples = self.silence = 0
        self.next_partial = self.partial_samples
        self.number = self.revision = 0
        self.active = False

    def push(self, pcm):
        self.remainder.extend(pcm)
        jobs = []
        while len(self.remainder) >= BLOCK * 2:
            block = bytes(self.remainder[:BLOCK * 2])
            del self.remainder[:BLOCK * 2]
            probability = self.vad(block)
            voiced = probability >= (0.25 if self.active else 0.4)
            if not self.active:
                if not voiced:
                    self.pre.extend(block)
                    del self.pre[:-int(PRE_ROLL_SECONDS * RATE) * 2]
                    self.cursor += BLOCK
                    continue
                self.active = True
                self.start = self.cursor - len(self.pre) // 2
                self.audio.extend(self.pre)
                self.pre.clear()
            self.audio.extend(block)
            self.cursor += BLOCK
            self.voice_samples += BLOCK if voiced else 0
            self.silence = 0 if voiced else self.silence + BLOCK
            count = len(self.audio) // 2
            final = self.silence >= int(END_SILENCE_SECONDS * RATE) or count >= self.max_samples
            if self.voice_samples >= int(MIN_VOICE_SECONDS * RATE) and (final or count >= self.next_partial):
                self.revision += 1
                jobs.append(AudioJob(f'local_{self.number}', self.revision, bytes(self.audio),
                                     self.start, self.cursor, final))
                self.next_partial = count + self.partial_samples
            if final:
                # No overlap on forced splits: avoids publishing duplicate dialogue.
                # An uninterrupted word at the boundary can still lose context.
                self.audio.clear()
                self.active = False
                self.voice_samples = self.silence = self.revision = 0
                self.next_partial = self.partial_samples
                self.number += 1
        return jobs

    def clear(self):
        self.audio.clear()
        self.pre.clear()
        self.remainder.clear()


class LocalRecognizer:
    def __init__(self, runtime, emit):
        self.runtime, self.emit = runtime, emit
        self.segmenter = None
        self.pending = OrderedDict()
        self.latest = {}
        self.previous_text = {}
        self.wake = asyncio.Event()
        self.closed = False
        self.worker = None

    async def start(self):
        self.segmenter = OnlineSegmenter(self.runtime.new_vad())
        self.worker = asyncio.create_task(self._work())

    async def push(self, pcm):
        if self.closed:
            return
        if self.worker.done():
            raise ProviderError()
        for job in self.segmenter.push(pcm):
            self.latest[job.key] = job.revision
            if job.key not in self.pending and len(self.pending) >= 4:
                raise ProviderError('provider_overloaded')
            # Replace an unprocessed partial/final for the same utterance, not FIFO audio.
            self.pending[job.key] = job
            self.wake.set()

    async def _work(self):
        try:
            while True:
                await self.wake.wait()
                while self.pending:
                    key, job = self.pending.popitem(last=False)
                    started = time.perf_counter()
                    text = await self.runtime.transcribe(job.pcm, final=job.final)
                    processing_ms = round((time.perf_counter() - started) * 1000)
                    if self.closed:
                        return
                    if self.latest.get(key) != job.revision:
                        continue
                    previous = self.previous_text.get(key, '')
                    self.previous_text[key] = text
                    if not job.final:
                        # One very short Whisper decode often invents words. Publish only
                        # a prefix agreed by two successive decodes of this utterance.
                        stable = 0
                        for before, after in zip(previous, text):
                            if before != after:
                                break
                            stable += 1
                        confirmed_short = stable == len(text) and interjection_translation(text) is not None
                        text = text[:stable].strip() if stable >= 4 or confirmed_short else ''
                    if text:
                        self.emit(Recognition(key, text, job.start // 16, job.end // 16, job.final, processing_ms))
                    if job.final:
                        self.latest.pop(key, None)
                        self.previous_text.pop(key, None)
                self.wake.clear()
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self.closed:
                self.emit(ProviderError())

    async def close(self):
        self.closed = True  # Invalidate callbacks before awaiting native inference.
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        self.pending.clear()
        self.latest.clear()
        self.previous_text.clear()
        if self.segmenter:
            self.segmenter.clear()
