"""JTR1: network-order header followed by signed little-endian mono PCM."""
from dataclasses import dataclass
import struct
import zlib

HEADER = struct.Struct('>4sBBHIQI')
SAMPLE_RATE = 16000
MAX_SAMPLES = 3200

class ProtocolError(ValueError):
    pass

@dataclass(frozen=True)
class AudioFrame:
    sequence: int
    first_sample_index: int
    sample_count: int
    pcm: bytes

    @classmethod
    def decode(cls, raw: bytes):
        if len(raw) < HEADER.size:
            raise ProtocolError('short_header')
        magic, version, flags, size, seq, first, count = HEADER.unpack_from(raw)
        if (magic, version, size) != (b'JTR1', 1, 24) or flags not in (0, 1):
            raise ProtocolError('unsupported_header')
        if not 0 < count <= MAX_SAMPLES or len(raw) > 24 + MAX_SAMPLES * 2 + 64:
            raise ProtocolError('invalid_payload_size')
        pcm = raw[24:]
        if flags == 1:
            try:
                decoder = zlib.decompressobj()
                pcm = decoder.decompress(pcm, count * 2 + 1)
                if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
                    raise ProtocolError('invalid_compressed_audio')
            except zlib.error:
                raise ProtocolError('invalid_compressed_audio') from None
        if len(pcm) != count * 2:
            raise ProtocolError('invalid_payload_size')
        return cls(seq, first, count, pcm)

    def encode(self, compress=False):
        payload = zlib.compress(self.pcm, 1) if compress else self.pcm
        flag = int(compress and len(payload) < len(self.pcm))
        return HEADER.pack(b'JTR1', 1, flag, 24, self.sequence,
                           self.first_sample_index, self.sample_count) + (payload if flag else self.pcm)

class FrameTracker:
    def __init__(self):
        self.sequence = 0
        self.next_sample = 0

    def accept(self, frame: AudioFrame):
        if frame.sequence != self.sequence or frame.first_sample_index != self.next_sample:
            raise ProtocolError('audio_gap_or_duplicate')
        self.sequence += 1
        self.next_sample += frame.sample_count
