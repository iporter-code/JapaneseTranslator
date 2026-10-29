package cn.jtranslate.live

import cn.jtranslate.live.capture.PcmNormalizer
import cn.jtranslate.live.subtitle.SubtitleStore
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.*

class CoreTest {
    private fun event(revision: Int, text: String, final: Boolean, epoch: Int = 1, start: Long = 0,
        segment: String = "a") = JSONObject().put("type", if (final) "translation.final" else "translation.partial")
        .put("epoch", epoch).put("segment_id", segment).put("source_revision", revision)
        .put("translation_revision", revision).put("translated_text", text).put("source_text", "日语")
        .put("translation_final", final).put("asr_final", final).put("start_audio_ms", start)

    @Test fun staleEpochIgnored() {
        assertNull(SubtitleStore(2).accept(event(1, "旧会话", true)))
    }
    @Test fun finalNotOverwrittenByPartialOrOldRevision() {
        val store = SubtitleStore(1)
        assertEquals("新结果", store.accept(event(3, "新结果", true))!!.text)
        assertNull(store.accept(event(2, "旧结果", true)))
        assertNull(store.accept(event(3, "临时", false)))
    }
    @Test fun lateOldSegmentDoesNotReplaceNewSegment() {
        val store = SubtitleStore(1)
        store.accept(event(1, "后一句", true, start = 2000, segment = "b"))
        assertEquals("后一句", store.accept(event(1, "前一句", true, start = 0))!!.text)
    }
    @Test fun newerSourceClearsOldTranslation() {
        val store = SubtitleStore(1)
        store.accept(event(1, "旧译文", false))
        val revised = event(2, "", false).put("type", "asr.partial")
        assertEquals("", store.accept(revised)!!.text)
    }
    @Test fun directPcmPreservesSamplesAndFrameBoundaries() {
        val frames = mutableListOf<ByteArray>()
        val normalizer = PcmNormalizer(16000)
        repeat(4) { normalizer.push(ShortArray(800) { (-1234).toShort() }, 800, frames::add) }
        assertEquals(2, frames.size)
        assertEquals((-1234).toShort(), ByteBuffer.wrap(frames.first()).order(ByteOrder.LITTLE_ENDIAN).short)
    }
    @Test fun decimatorKeepsDurationAndAttenuatesHighFrequencies() {
        fun amplitude(hz: Double): Double {
            val frames = mutableListOf<ByteArray>()
            val input = ShortArray(48000) { (sin(2 * PI * hz * it / 48000) * 15000).roundToInt().toShort() }
            val normalizer = PcmNormalizer(48000)
            // Exercise state across irregular input blocks.
            var index = 0
            while (index < input.size) {
                val end = min(input.size, index + 733)
                val block = input.copyOfRange(index, end)
                normalizer.push(block, block.size, frames::add)
                index = end
            }
            assertEquals(10, frames.size)
            val output = frames.flatMap { ByteBuffer.wrap(it).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().let { b ->
                List(b.remaining()) { b.get().toDouble() }
            } }.drop(200)
            return sqrt(output.sumOf { it * it } / output.size)
        }
        assertTrue(amplitude(12000.0) < amplitude(1000.0) * .1)
    }
}
