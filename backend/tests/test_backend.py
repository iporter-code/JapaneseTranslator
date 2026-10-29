import asyncio
import struct
import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.protocol import AudioFrame, FrameTracker, ProtocolError
from app.providers import TranslationScheduler

TOKEN = 'test-only-token-not-for-production-12345'

@pytest.fixture
def client():
    with TestClient(create_app(Settings(TOKEN))) as test_client:
        yield test_client

def create_session(client):
    response = client.post('/v1/sessions', headers={'Authorization': 'Bearer ' + TOKEN}, json={})
    assert response.status_code == 200
    return response.json()

def test_auth_required(client):
    assert client.post('/v1/sessions', json={}).status_code == 401

def test_no_unsupported_audio_or_history(client):
    auth = {'Authorization': 'Bearer ' + TOKEN}
    assert client.post('/v1/sessions', headers=auth, json={'save_history': True}).status_code == 422
    assert client.post('/v1/sessions', headers=auth, json={'audio': {'sample_rate': 48000}}).status_code == 422

def test_quota_and_idempotent_close(client):
    one, two = create_session(client), create_session(client)
    assert client.post('/v1/sessions', headers={'Authorization': 'Bearer ' + TOKEN}, json={}).status_code == 429
    for _ in range(2):
        assert client.delete('/v1/sessions/' + one['session_id'],
                             headers={'Authorization': 'Bearer ' + TOKEN}).status_code == 200

def test_protocol_round_trip():
    frame = AudioFrame(0, 0, 1600, struct.pack('<1600h', *([1000] * 1600)))
    assert AudioFrame.decode(frame.encode()) == frame
    assert len(frame.encode()) == 3224

@pytest.mark.parametrize('data', [b'', b'JTR1', b'X' * 24,
    AudioFrame(0, 0, 1600, b'').encode(), AudioFrame(0, 0, 4000, b'\0' * 8000).encode()])
def test_invalid_frames(data):
    with pytest.raises(ProtocolError):
        AudioFrame.decode(data)

def test_gap_and_duplicate_rejected():
    tracker = FrameTracker()
    frame = AudioFrame(0, 0, 1600, b'\0' * 3200)
    tracker.accept(frame)
    with pytest.raises(ProtocolError):
        tracker.accept(frame)

def test_websocket_demo_chain(client):
    session = create_session(client)
    path = '/v1/sessions/' + session['session_id'] + '/stream'
    with client.websocket_connect(path, headers={'Authorization': 'Bearer ' + session['stream_token']}) as ws:
        ws.send_json({'type': 'stream.start', 'version': 1, 'epoch': 1})
        assert ws.receive_json()['type'] == 'stream.ready'
        pcm = struct.pack('<1600h', *([1000] * 1600))
        for index in range(20):
            ws.send_bytes(AudioFrame(index, index * 1600, 1600, pcm).encode())
        events = []
        while not any(e['type'] == 'translation.final' for e in events):
            events.append(ws.receive_json())
        translated = next(e for e in events if e['type'] == 'translation.final')
        assert '演示' in translated['translated_text']
        assert translated['epoch'] == 1
        assert translated['asr_final'] is True
        ws.send_json({'type': 'stream.stop'})

def test_wrong_stream_token(client):
    from starlette.websockets import WebSocketDisconnect
    session = create_session(client)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/v1/sessions/' + session['session_id'] + '/stream',
                                      headers={'Authorization': 'Bearer incorrect'}):
            pass

def test_malformed_stream_returns_error(client):
    session = create_session(client)
    with client.websocket_connect('/v1/sessions/' + session['session_id'] + '/stream',
                                  headers={'Authorization': 'Bearer ' + session['stream_token']}) as ws:
        ws.send_json({'type': 'stream.start', 'version': 1, 'epoch': 2})
        ws.receive_json()
        ws.send_bytes(b'bad')
        assert ws.receive_json()['fatal'] is True

def test_superseded_translation_never_publishes():
    async def scenario():
        output = []
        async def translate(text):
            await asyncio.sleep(.02)
            return text
        async def publish(event):
            output.append(event)
        scheduler = TranslationScheduler(translate, publish)
        scheduler.submit({'segment_id': 'a', 'source_revision': 1, 'source_text': 'old', 'asr_final': False})
        scheduler.submit({'segment_id': 'a', 'source_revision': 2, 'source_text': 'new', 'asr_final': True})
        await asyncio.sleep(.05)
        assert len(output) == 1 and output[0]['translated_text'] == 'new'
        await scheduler.close()
    asyncio.run(scenario())

def test_stop_cancels_inflight_translation():
    async def scenario():
        output = []
        async def translate(text):
            await asyncio.sleep(10)
        async def publish(event):
            output.append(event)
        scheduler = TranslationScheduler(translate, publish)
        scheduler.submit({'segment_id': 'a', 'source_revision': 1, 'source_text': 'x', 'asr_final': True})
        await scheduler.close()
        assert not output and not scheduler.tasks
    asyncio.run(scenario())
