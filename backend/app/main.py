import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
import hmac
import json
import secrets
import time
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict
from .config import Settings
from .protocol import AudioFrame, FrameTracker, ProtocolError
from .providers import (AzureRecognizer, MockRecognizer, ProviderError, Recognition,
                        TranslationScheduler, Translator)

class AudioConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    encoding: Literal['pcm_s16le'] = 'pcm_s16le'
    sample_rate: Literal[16000] = 16000
    channels: Literal[1] = 1

class SessionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_language: Literal['ja-JP'] = 'ja-JP'
    target_language: Literal['zh-Hans'] = 'zh-Hans'
    audio: AudioConfig = AudioConfig()
    translation_style: Literal['faithful'] = 'faithful'
    save_history: Literal[False] = False

@dataclass
class Session:
    token: str
    created: float = field(default_factory=time.monotonic)
    connected: bool = False
    stop: asyncio.Event = field(default_factory=asyncio.Event)

def create_app(settings=None):
    settings = settings or Settings.from_env()
    sessions = {}

    @asynccontextmanager
    async def lifespan(app):
        async def reap():
            while True:
                await asyncio.sleep(2)
                for sid, entry in list(sessions.items()):
                    limit = settings.max_session_seconds if entry.connected else 120
                    if time.monotonic() - entry.created > limit:
                        entry.stop.set()
                        sessions.pop(sid, None)
        reaper = asyncio.create_task(reap())
        yield
        reaper.cancel()
        for entry in list(sessions.values()):
            entry.stop.set()
        with suppress(asyncio.CancelledError):
            await reaper

    app = FastAPI(title='日语实时字幕服务', lifespan=lifespan)
    app.state.sessions = sessions

    def authorize(value):
        if not hmac.compare_digest(value or '', 'Bearer ' + settings.access_token):
            raise HTTPException(401, 'unauthorized')

    @app.get('/health')
    def health():
        return {'status': 'ok', 'provider': settings.provider, 'demo': settings.provider == 'mock'}

    @app.post('/v1/sessions')
    async def create(body: SessionRequest, request: Request, authorization: str = Header(default='')):
        authorize(authorization)
        if len(sessions) >= settings.max_sessions:
            raise HTTPException(429, 'session_limit')
        sid, token = secrets.token_urlsafe(18), secrets.token_urlsafe(32)
        sessions[sid] = Session(token)
        base = str(request.base_url).rstrip('/')
        ws = ('wss' + base[5:]) if base.startswith('https:') else ('ws' + base[4:])
        return {'session_id': sid, 'stream_url': f'{ws}/v1/sessions/{sid}/stream',
                'stream_token': token, 'expires_in': settings.max_session_seconds,
                'connect_within_seconds': 120, 'provider': settings.provider,
                'demo': settings.provider == 'mock', 'audio': body.audio.model_dump()}

    @app.delete('/v1/sessions/{sid}')
    async def delete(sid: str, authorization: str = Header(default='')):
        authorize(authorization)
        entry = sessions.pop(sid, None)
        if entry:
            entry.stop.set()
        return {'closed': True}

    @app.websocket('/v1/sessions/{sid}/stream')
    async def stream(ws: WebSocket, sid: str):
        entry = sessions.get(sid)
        auth = ws.headers.get('authorization', '')
        if not entry or entry.connected or not hmac.compare_digest(auth, 'Bearer ' + entry.token):
            await ws.close(code=1008)
            return
        entry.connected = True
        await ws.accept()
        write_lock = asyncio.Lock()
        events = asyncio.Queue(maxsize=128)
        overflow = asyncio.Event()
        loop = asyncio.get_running_loop()
        epoch = 0
        scheduler = recognizer = translator = None
        workers = []

        async def publish(event):
            async with write_lock:
                await ws.send_json({'session_id': sid, 'epoch': epoch, **event})

        def emit(event):
            def insert():
                if events.full():
                    overflow.set()
                else:
                    events.put_nowait(event)
            loop.call_soon_threadsafe(insert)

        try:
            first = await asyncio.wait_for(ws.receive_json(), 10)
            epoch = first.get('epoch', 0)
            if (first.get('type') != 'stream.start' or first.get('version') != 1
                    or type(epoch) is not int or epoch < 1):
                raise ProtocolError('invalid_start')
            translator = Translator(settings)
            scheduler = TranslationScheduler(translator.translate, publish)
            recognizer = MockRecognizer(emit) if settings.provider == 'mock' else AzureRecognizer(settings, emit)
            await asyncio.wait_for(recognizer.start(), 20)
            await publish({'type': 'stream.ready', 'demo': settings.provider == 'mock'})
            tracker = FrameTracker()
            versions = {}
            begun = time.monotonic()

            async def consume():
                while True:
                    result = await events.get()
                    if isinstance(result, ProviderError):
                        raise result
                    key = result.segment_id
                    previous = versions.get(key)
                    if previous and (previous[1] or previous[2] == result.text and not result.final):
                        continue
                    version = previous[0] + 1 if previous else 1
                    versions[key] = (version, result.final, result.text)
                    if len(versions) > 128:
                        versions.pop(next(iter(versions)))
                    event = {'type': 'asr.final' if result.final else 'asr.partial',
                             'segment_id': key, 'source_revision': version,
                             'source_text': result.text, 'start_audio_ms': result.start_ms,
                             'end_audio_ms': result.end_ms, 'asr_final': result.final}
                    await publish(event)
                    scheduler.submit(event)

            async def receive():
                while True:
                    packet = await asyncio.wait_for(ws.receive(), 30)
                    if packet['type'] == 'websocket.disconnect':
                        return
                    if packet.get('bytes') is not None:
                        frame = AudioFrame.decode(packet['bytes'])
                        tracker.accept(frame)
                        if tracker.next_sample / 16000 > time.monotonic() - begun + 3:
                            raise ProtocolError('audio_rate_exceeded')
                        await asyncio.wait_for(recognizer.push(frame.pcm), 5)
                    elif packet.get('text') is not None:
                        if len(packet['text']) > 2048:
                            raise ProtocolError('control_too_large')
                        control = json.loads(packet['text'])
                        if control.get('type') == 'ping':
                            await publish({'type': 'pong'})
                        elif control.get('type') == 'stream.stop':
                            return
                        else:
                            raise ProtocolError('unsupported_control_reopen_for_new_epoch')

            workers = [asyncio.create_task(receive()), asyncio.create_task(consume()),
                       asyncio.create_task(entry.stop.wait()), asyncio.create_task(overflow.wait())]
            done, _ = await asyncio.wait(workers, timeout=settings.max_session_seconds,
                                         return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if overflow.is_set():
                raise ProviderError('provider_overloaded')
        except ProviderError as exc:
            with suppress(Exception):
                await publish({'type': 'session.error', 'code': exc.code, 'fatal': True})
        except (WebSocketDisconnect, RuntimeError):
            pass
        except (ProtocolError, ValueError, TypeError, AttributeError, asyncio.TimeoutError):
            with suppress(Exception):
                await publish({'type': 'session.error', 'code': 'protocol_or_timeout', 'fatal': True})
        except Exception:
            with suppress(Exception):
                await publish({'type': 'session.error', 'code': 'provider_unavailable', 'fatal': True})
        finally:
            entry.stop.set()
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            if scheduler:
                await scheduler.close()
            if recognizer:
                with suppress(Exception):
                    await asyncio.wait_for(recognizer.close(), 10)
            if translator:
                await translator.close()
            sessions.pop(sid, None)
            with suppress(Exception):
                await ws.close(code=1000)

    return app
