import asyncio
import struct
import zlib

import pytest
from app.protocol import AudioFrame, ProtocolError, HEADER
from app.interjections import interjection_translation
from app.streaming import OnlineSegmenter, BLOCK, LocalRecognizer
from app.local_runtime import LocalRuntime
from app.config import Settings


def test_compressed_audio_round_trip_and_incompressible_fallback():
    pcm = struct.pack('<1600h', *([1234] * 1600))
    frame = AudioFrame(4, 6400, 1600, pcm)
    packet = frame.encode(compress=True)
    assert packet[5] == 1 and len(packet) < 200
    assert AudioFrame.decode(packet) == frame
    assert AudioFrame.decode(frame.encode()) == frame


@pytest.mark.parametrize('payload', [zlib.compress(b'\0' * 1000000),
    zlib.compress(b'\0' * 3200) + b'extra', zlib.compress(b'\0' * 3200)[:-2], b'not zlib'])
def test_compressed_payload_is_bounded_and_malformed_input_is_rejected(payload):
    with pytest.raises(ProtocolError):
        AudioFrame.decode(HEADER.pack(b'JTR1', 1, 1, 24, 0, 0, 1600) + payload)


def test_short_speech_is_not_dropped_but_isolated_click_is():
    quiet = b'\0' * (BLOCK * 2)
    voice = struct.pack('<h', 2000) * BLOCK
    vad = lambda block: .9 if block[:2] != b'\0\0' else 0
    # 128 ms voiced reply, shorter than the old 240 ms cutoff.
    jobs = OnlineSegmenter(vad).push(quiet * 20 + voice * 4 + quiet * 15)
    assert jobs and jobs[-1].final
    assert all(job.key == jobs[-1].key for job in jobs)
    assert not OnlineSegmenter(vad).push(quiet * 20 + voice + quiet * 15)


@pytest.mark.parametrize('source,expected', [('うん。', '嗯。'), ('あっ！', '啊！'),
    ('えっ？', '诶？'), ('ウン', '嗯。'), ('うーん…', '嗯……'), ('今日は晴れです。', None)])
def test_interjections_are_exact_recognized_text_not_invented_audio(source, expected):
    assert interjection_translation(source) == expected


def test_short_recognized_reply_bypasses_llm_without_suppressing_it():
    async def run():
        runtime = LocalRuntime(Settings('test-interjection-token-123456789'))
        # No translation client: this also proves there was no network/LLM call.
        assert await runtime.translate('うん。') == '嗯。'
        assert await runtime.translate('あっ！') == '啊！'
    asyncio.run(run())
