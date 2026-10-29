package cn.jtranslate.live.capture

import android.annotation.SuppressLint
import android.media.*
import android.media.projection.MediaProjection
import kotlinx.coroutines.*
import kotlin.math.*

/** Stateful 48kHz -> 16kHz decimator; FIR prevents simple dropped-sample aliasing. */
internal class PcmNormalizer(private val inputRate: Int) {
    private val taps = 63
    private val coefficients = DoubleArray(taps) { index ->
        val x = index - (taps - 1) / 2.0
        val cutoff = 7000.0 / 48000
        val sinc = if (x == 0.0) 2 * cutoff else sin(2 * PI * cutoff * x) / (PI * x)
        sinc * (.54 - .46 * cos(2 * PI * index / (taps - 1)))
    }.let { raw -> val total = raw.sum(); raw.map { it / total }.toDoubleArray() }
    private val history = DoubleArray(taps)
    private var position = 0
    private var phase = 0
    private var output = ByteArray(3200)
    private var used = 0

    fun push(samples: ShortArray, length: Int, emit: (ByteArray) -> Unit) {
        for (i in 0 until length) {
            val value = if (inputRate == 16000) samples[i].toInt() else {
                history[position] = samples[i].toDouble()
                position = (position + 1) % taps
                phase = (phase + 1) % 3
                if (phase != 0) continue
                var filtered = 0.0
                for (j in 0 until taps) filtered += coefficients[j] * history[(position - 1 - j + taps) % taps]
                filtered.roundToInt().coerceIn(-32768, 32767)
            }
            output[used++] = (value and 255).toByte()
            output[used++] = (value shr 8).toByte()
            if (used == output.size) { emit(output); output = ByteArray(3200); used = 0 }
        }
    }
}

class AudioCapture(private val projection: MediaProjection) {
    private var record: AudioRecord? = null
    private var reader: Job? = null
    @Volatile private var running = false

    @SuppressLint("MissingPermission")
    fun start(scope: CoroutineScope, frame: (ByteArray) -> Unit, volume: (Float) -> Unit,
        failure: (String) -> Unit) {
        val config = AudioPlaybackCaptureConfiguration.Builder(projection)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN).build()
        var chosen = 16000
        for (rate in listOf(16000, 48000)) {
            val minimum = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            if (minimum <= 0) continue
            val candidate = runCatching { AudioRecord.Builder().setAudioPlaybackCaptureConfig(config)
                .setAudioFormat(AudioFormat.Builder().setSampleRate(rate).setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                .setBufferSizeInBytes(max(minimum * 2, rate / 5 * 2)).build() }.getOrNull() ?: continue
            if (candidate.state == AudioRecord.STATE_INITIALIZED) { record = candidate; chosen = rate; break }
            candidate.release()
        }
        val audio = record ?: error("设备无法初始化内部音频采集")
        audio.startRecording()
        check(audio.recordingState == AudioRecord.RECORDSTATE_RECORDING) { "音频采集未启动" }
        running = true
        reader = scope.launch(Dispatchers.IO) {
            val buffer = ShortArray(chosen / 10)
            val normalizer = PcmNormalizer(chosen)
            try {
                while (isActive && running) {
                    val count = audio.read(buffer, 0, buffer.size, AudioRecord.READ_BLOCKING)
                    if (!running) break
                    if (count < 0) error("内部音频读取失败（$count）")
                    if (count == 0) continue
                    var energy = 0.0
                    for (i in 0 until count) energy += buffer[i].toDouble() * buffer[i]
                    volume((sqrt(energy / count) / 32768).toFloat())
                    normalizer.push(buffer, count, frame)
                }
            } catch (error: Exception) {
                if (running && isActive) failure("内部音频读取中断，请重新授权开始")
            } finally { runCatching { audio.release() } }
        }
    }

    suspend fun close() {
        running = false
        runCatching { record?.stop() }
        reader?.cancelAndJoin()
        if (reader == null) runCatching { record?.release() }
        record = null
    }
}
