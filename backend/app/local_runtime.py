"""Resident faster-whisper + a private, owned llama.cpp subprocess."""
import asyncio
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

import httpx

from .providers import ProviderError

FAITHFUL_PROMPT = (
    '请将下面的日语对白忠实翻译为自然的简体中文。'
    '准确保留动作执行者、接受者、借入和借出方向、否定与时间关系；'
    '不要添加原文没有的对象。保留原文的粗口和性相关用语，'
    '不主动打码或委婉化。只输出译文，不要解释。\n\n'
)


class LocalRuntime:
    def __init__(self, settings):
        self.settings = settings
        self.model = self.vad_session = self.process = self.client = None
        self.dll_handles = []
        self.asr_gate = asyncio.Lock()
        self.translation_gate = asyncio.Lock()
        self.ready = False

    async def start(self):
        try:
            loader = asyncio.create_task(asyncio.to_thread(self._load_models))
            try:
                await asyncio.shield(loader)
            except asyncio.CancelledError:
                # Finish native initialization before cleanup, so interruption cannot
                # spawn an unowned llama process after close() has already returned.
                try:
                    await loader
                finally:
                    raise
            self.client = httpx.AsyncClient(base_url=f'http://127.0.0.1:{self.settings.local_llama_port}',
                timeout=15, trust_env=False, headers={'Authorization': 'Bearer ' + self.internal_token})
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError('Local translation process exited during startup')
                try:
                    response = await self.client.get('/health', timeout=2)
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(.25)
            else:
                raise RuntimeError('Local translation startup timed out')
            # Warm both engines before accepting phone sessions.
            await self.transcribe(b'\0' * 16000)
            await self.translate('こんにちは。')
            self.ready = True
        except BaseException:
            await self.close()
            raise

    def _load_models(self):
        settings = self.settings
        for item in (settings.local_asr_path, settings.local_llama_path, settings.local_translation_path):
            if not Path(item).exists():
                raise RuntimeError('Local model/runtime is missing; see docs/本地模型联调.md')
        if os.name == 'nt':
            bins = list((Path(sys.prefix) / 'Lib/site-packages/nvidia').rglob('bin'))
            for folder in bins:
                self.dll_handles.append(os.add_dll_directory(str(folder)))
            os.environ['PATH'] = os.pathsep.join(str(p) for p in bins) + os.pathsep + os.environ['PATH']
        from faster_whisper import WhisperModel
        from faster_whisper.vad import get_vad_model
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        if settings.local_device == 'cuda':
            devices = subprocess.run([settings.local_llama_path, '--list-devices'], capture_output=True,
                timeout=20, creationflags=flags)
            if b'CUDA0' not in devices.stdout + devices.stderr:
                raise RuntimeError('llama.cpp CUDA backend unavailable; refusing silent CPU fallback')
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', settings.local_llama_port))
            except OSError:
                raise RuntimeError('Local translation port is busy; refusing to reuse an unrelated process') from None
        self.model = WhisperModel(settings.local_asr_path, device=settings.local_device,
            compute_type='int8_float16' if settings.local_device == 'cuda' else 'int8',
            cpu_threads=8, num_workers=1, local_files_only=True)
        # Reuse the pinned Silero v6 ONNX session; hidden state stays per recognizer.
        self.vad_session = get_vad_model().session
        self.internal_token = secrets.token_urlsafe(32)
        env = os.environ.copy()
        env['LLAMA_API_KEY'] = self.internal_token
        self.process = subprocess.Popen([settings.local_llama_path, '-m', settings.local_translation_path,
            '--host', '127.0.0.1', '--port', str(settings.local_llama_port),
            '-ngl', '99' if settings.local_device == 'cuda' else '0', '-c', '2048',
            '--parallel', '1', '--threads', '8', '-lv', '0'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, creationflags=flags)

    def new_vad(self):
        import numpy as np
        h = np.zeros((1, 1, 128), dtype='float32')
        c = np.zeros((1, 1, 128), dtype='float32')
        context = np.zeros((1, 64), dtype='float32')
        def probability(pcm):
            nonlocal h, c, context
            audio = np.frombuffer(pcm, dtype='<i2').astype('float32') / 32768.0
            values = np.concatenate((context, audio.reshape(1, -1)), axis=1)
            output, h, c = self.vad_session.run(None, {'input': values, 'h': h, 'c': c})
            context = audio[-64:].reshape(1, 64).copy()
            return float(output.reshape(-1)[0])
        return probability

    async def transcribe(self, pcm):
        import numpy as np
        def infer():
            audio = np.frombuffer(pcm, dtype='<i2').astype('float32') / 32768.0
            segments, _ = self.model.transcribe(audio, language='ja', task='transcribe', beam_size=5,
                condition_on_previous_text=False, vad_filter=False, temperature=0)
            return ''.join(segment.text for segment in segments
                           if segment.no_speech_prob < .6 and segment.avg_logprob > -1.0).strip()
        async with self.asr_gate:
            task = asyncio.create_task(asyncio.to_thread(infer))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # CTranslate2 cannot cancel a native call. Keep the gate until it exits;
                # otherwise repeated reconnects could create an unbounded GPU work queue.
                try:
                    await task
                finally:
                    raise

    async def translate(self, text):
        try:
            async with self.translation_gate:
                response = await self.client.post('/v1/chat/completions', json={
                    'messages': [{'role': 'user', 'content': FAITHFUL_PROMPT + text}],
                    'temperature': 0, 'max_tokens': 256, 'stream': False, 'cache_prompt': False})
            response.raise_for_status()
            choice = response.json()['choices'][0]
            if choice.get('finish_reason') != 'stop':
                raise ProviderError('provider_truncated_result')
            result = choice['message']['content'].strip()
            if not result:
                raise ProviderError('provider_empty_result')
            return result
        except ProviderError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError, AttributeError):
            raise ProviderError() from None

    async def close(self):
        self.ready = False
        if self.client:
            await self.client.aclose()
            self.client = None
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    await asyncio.to_thread(self.process.wait, 10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    await asyncio.to_thread(self.process.wait)
            self.process = None
        self.model = self.vad_session = None
        for handle in self.dll_handles:
            handle.close()
        self.dll_handles.clear()
