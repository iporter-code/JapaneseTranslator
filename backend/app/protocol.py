"""JTR1: network-order header followed by signed little-endian mono PCM."""
from dataclasses import dataclass
import struct

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
        if (magic, version, flags, size) != (b'JTR1', 1, 0, 24):
            raise ProtocolError('unsupported_header')
        if not 0 < count <= MAX_SAMPLES or len(raw) != 24 + count * 2:
            raise ProtocolError('invalid_payload_size')
        return cls(seq, first, count, raw[24:])

    def encode(self):
        return HEADER.pack(b'JTR1', 1, 0, 24, self.sequence,
                           self.first_sample_index, self.sample_count) + self.pcm

class FrameTracker:
    def __init__(self):
        self.sequence = 0
        self.next_sample = 0

    def accept(self, frame: AudioFrame):
        if frame.sequence != self.sequence or frame.first_sample_index != self.next_sample:
            raise ProtocolError('audio_gap_or_duplicate')
        self.sequence += 1
        self.next_sample += frame.sample_count
