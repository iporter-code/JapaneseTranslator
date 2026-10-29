package cn.jtranslate.live.network

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.zip.Deflater

/** Optional lossless frame compression, negotiated with the backend. */
class AudioPacketEncoder {
    private val compressor = Deflater(1)
    fun encode(pcm: ByteArray, sequence: Int, first: Long, compress: Boolean): ByteArray {
        var flags = 0
        var payload = pcm
        if (compress) {
            compressor.reset(); compressor.setInput(pcm); compressor.finish()
            val buffer = ByteArray(pcm.size + 64)
            val length = compressor.deflate(buffer)
            if (compressor.finished() && length < pcm.size) {
                flags = 1; payload = buffer.copyOf(length)
            }
        }
        return ByteBuffer.allocate(24 + payload.size).order(ByteOrder.BIG_ENDIAN)
            .put("JTR1".toByteArray()).put(1.toByte()).put(flags.toByte()).putShort(24.toShort())
            .putInt(sequence).putLong(first).putInt(pcm.size / 2).put(payload).array()
    }
    fun close() { compressor.end() }
}
