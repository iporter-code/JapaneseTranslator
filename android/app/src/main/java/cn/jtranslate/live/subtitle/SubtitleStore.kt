package cn.jtranslate.live.subtitle

import org.json.JSONObject

data class Subtitle(val segmentId: String, val revision: Int, val translationRevision: Int = 0,
    val source: String, val text: String = "", val asrFinal: Boolean = false,
    val final: Boolean = false, val startMs: Long = 0)

class SubtitleStore(private val epoch: Int) {
    private val lines = linkedMapOf<String, Subtitle>()
    private var displayed: Subtitle? = null
    fun accept(event: JSONObject): Subtitle? {
        if (event.optInt("epoch") != epoch) return null
        val id = event.optString("segment_id")
        if (id.isEmpty()) return null
        val revision = event.optInt("source_revision")
        val old = lines[id]
        if (old != null && revision < old.revision) return null
        val type = event.optString("type")
        val translated = type.startsWith("translation.")
        if (!translated && !type.startsWith("asr.")) return null
        if (old != null && old.final && revision == old.revision) return null
        if (old != null && translated && revision == old.revision &&
            event.optInt("translation_revision") < old.translationRevision) return null
        val sameVersion = old != null && old.revision == revision
        val result = Subtitle(id, revision,
            if (translated) event.optInt("translation_revision") else if (sameVersion) old!!.translationRevision else 0,
            event.optString("source_text", old?.source ?: ""),
            if (translated) event.optString("translated_text") else if (sameVersion) old!!.text else "",
            event.optBoolean("asr_final"), translated && event.optBoolean("translation_final"),
            event.optLong("start_audio_ms"))
        lines[id] = result
        if (lines.size > 32) lines.remove(lines.keys.first())
        // Keep the last complete source/translation pair while the next revision
        // or utterance is being recognized. Never replace it with an empty ASR event.
        if (translated && result.text.isNotBlank() && (displayed == null || result.startMs >= displayed!!.startMs))
            displayed = result
        return displayed
    }
}
