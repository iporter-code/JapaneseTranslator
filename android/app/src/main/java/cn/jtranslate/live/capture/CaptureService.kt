package cn.jtranslate.live.capture

import android.app.*
import android.content.*
import android.content.pm.ServiceInfo
import android.content.res.Configuration
import android.media.projection.*
import android.os.*
import androidx.core.content.edit
import cn.jtranslate.live.*
import cn.jtranslate.live.network.*
import cn.jtranslate.live.overlay.OverlayController
import cn.jtranslate.live.settings.AppSettings
import cn.jtranslate.live.settings.Options
import cn.jtranslate.live.subtitle.SubtitleStore
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.selects.select
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlin.math.min

class CaptureService : Service() {
    companion object {
        const val START = "capture.start"
        const val TOGGLE = "capture.toggle"
        const val CLEAR = "capture.clear"
        const val STOP = "capture.stop"
        fun command(context: Context, action: String) { context.startService(Intent(context, CaptureService::class.java).setAction(action)) }
    }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val controls = Mutex()
    private var projection: MediaProjection? = null
    private var overlay: OverlayController? = null
    private lateinit var settings: AppSettings
    private var options = Options()
    private var sessionJob: Job? = null
    private var stopping = false
    private var initialized = false
    private var epoch = 0
    private var wakeLock: PowerManager.WakeLock? = null
    private var wakeJob: Job? = null
    override fun onBind(intent: Intent?) = null

    override fun onCreate() {
        super.onCreate()
        settings = AppSettings(this)
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel("capture", "字幕翻译运行状态", NotificationManager.IMPORTANCE_LOW))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            START -> {
                if (initialized) return START_NOT_STICKY
                initialized = true
                val pending = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
                val stop = PendingIntent.getService(this, 1, Intent(this, CaptureService::class.java).setAction(STOP), PendingIntent.FLAG_IMMUTABLE)
                val notification = Notification.Builder(this, "capture").setSmallIcon(cn.jtranslate.live.R.drawable.ic_translate)
                    .setContentTitle("日语字幕运行中").setContentText("正在采集内部播放音频；可随时停止")
                    .setContentIntent(pending).setOngoing(true).setCategory(Notification.CATEGORY_SERVICE)
                    .setVisibility(Notification.VISIBILITY_PRIVATE)
                    .addAction(Notification.Action.Builder(null, "停止", stop).build()).build()
                startForeground(10, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
                scope.launch {
                    try {
                        options = settings.options.first()
                        @Suppress("DEPRECATION")
                        val data = intent.getParcelableExtra<Intent>("projection_data") ?: error("缺少捕获授权")
                        val code = intent.getIntExtra("projection_code", Activity.RESULT_CANCELED)
                        projection = getSystemService(MediaProjectionManager::class.java).getMediaProjection(code, data)
                        projection!!.registerCallback(object : MediaProjection.Callback() {
                            override fun onStop() {
                                if (!stopping) finish("系统已结束捕获，请重新授权开始")
                            }
                        }, Handler(Looper.getMainLooper()))
                        overlay = OverlayController(this@CaptureService, { command(this@CaptureService, STOP) },
                            { landscape, x, y -> scope.launch { settings.position(landscape, x, y) } })
                        overlay!!.style(options); overlay!!.show()
                        AppState.live.value = LiveState(active = true, status = "启动音频采集", demo = options.localDemo)
                        getSharedPreferences("runtime", MODE_PRIVATE).edit { putBoolean("running", true) }
                        holdCpu()
                        scope.launch { settings.options.collect { options = it; overlay?.style(it) } }
                        scope.launch { AppState.live.collect { overlay?.render(it) } }
                        launchSession()
                    } catch (_: Exception) { finish("无法开始，请检查捕获授权和悬浮窗权限") }
                }
            }
            STOP -> finish()
            TOGGLE, CLEAR -> scope.launch { controls.withLock {
                if (projection == null || stopping) return@withLock
                sessionJob?.cancelAndJoin()
                if (intent.action == TOGGLE && !AppState.live.value.paused) {
                    releaseCpu()
                    AppState.update { it.copy(paused = true, rms = 0f, status = "已暂停") }
                } else {
                    holdCpu()
                    AppState.update { it.copy(paused = false, source = if (intent.action == CLEAR) "" else it.source,
                        translated = if (intent.action == CLEAR) "" else it.translated, error = "") }
                    launchSession()
                }
            } }
            else -> if (!initialized) stopSelf()
        }
        return START_NOT_STICKY
    }

    private fun launchSession() {
        sessionJob = scope.launch {
            var attempt = 0
            while (isActive && !stopping) {
                val current = ++epoch
                var remote: RemoteSession? = null
                var capture: AudioCapture? = null
                val queue = Channel<ByteArray>(20)
                val failures = Channel<SessionFailure>(Channel.CONFLATED)
                val subtitles = SubtitleStore(current)
                var heartbeat: Job? = null
                var sequence = 0
                var firstSample = 0L
                var voicedFrames = 0
                var lastVoice = SystemClock.elapsedRealtime()
                try {
                    AppState.update { it.copy(epoch = current, status = if (options.localDemo) "本机演示 · 等待音频" else "连接后端…", error = "") }
                    if (!options.localDemo) {
                        val connection = RemoteSession(options.server, settings.token(), current, { event ->
                            if (AppState.live.value.epoch == current && !stopping) {
                                if (event.optString("type") == "pong") {
                                    AppState.update { it.copy(networkRttMs = event.optLong("client_rtt_ms")) }
                                } else if (event.optString("type") == "session.error") {
                                    AppState.update { it.copy(error = "服务提示：${event.optString("code")}") }
                                } else subtitles.accept(event)?.let { line ->
                                    AppState.update { it.copy(source = line.source, translated = line.text,
                                        lastSubtitleAt = SystemClock.elapsedRealtime()) }
                                }
                            }
                        }, { failures.trySend(it) })
                        remote = connection
                        connection.open()
                        connection.heartbeat()
                        AppState.update { it.copy(demo = connection.demo, status = if (connection.demo) "后端演示 · 固定测试字幕" else "正在翻译") }
                        heartbeat = launch { while (isActive) { delay(10000); connection.heartbeat() } }
                    } else AppState.update { it.copy(demo = true) }
                    capture = AudioCapture(projection ?: error("授权已结束"))
                    capture.start(this, { pcm ->
                        if (!queue.trySend(pcm).isSuccess) failures.trySend(SessionFailure("音频积压，重新跟随"))
                    }, { rms ->
                        val now = SystemClock.elapsedRealtime()
                        if (rms > .003f) lastVoice = now
                        AppState.update { state ->
                            if (state.epoch != current) state else state.copy(rms = rms,
                                status = if (now - lastVoice > 5000) "暂未检测到可采集声音" else if (state.demo) "演示模式 · 不识别对白" else "正在翻译")
                        }
                    }, { failures.trySend(SessionFailure(it, false)) })
                    while (isActive) {
                        val pcm = withTimeout(8000) { select<ByteArray> {
                            queue.onReceive { it }
                            failures.onReceive { throw it }
                        } }
                        remote?.send(pcm, sequence, firstSample)
                        if (options.localDemo && AppState.live.value.rms > .003f) {
                            voicedFrames++
                            if (voicedFrames % 20 == 0) AppState.update { it.copy(
                                source = "【演示】今日はここで待っていて。",
                                translated = "【演示】今天就在这里等着。（固定测试字幕）",
                                lastSubtitleAt = SystemClock.elapsedRealtime()) }
                        }
                        sequence++; firstSample += pcm.size / 2
                        AppState.update { it.copy(frames = it.frames + 1) }
                        if (sequence >= 100) attempt = 0
                    }
                } catch (cancel: CancellationException) {
                    throw cancel
                } catch (error: Exception) {
                    val retryable = error is SessionFailure && error.retryable && !options.localDemo
                    if (!retryable) {
                        finish((error as? SessionFailure)?.reason ?: "采集或连接失败，请检查权限后重新开始")
                        break
                    }
                    AppState.update { it.copy(status = "连接中断，准备重新跟随", error = (error as SessionFailure).reason) }
                    attempt++
                } finally {
                    withContext(NonCancellable) {
                        heartbeat?.cancelAndJoin()
                        capture?.close()
                        remote?.close()
                        queue.close(); failures.close()
                    }
                }
                delay(min(8000L, 500L * (1 shl min(attempt, 4))) + (0L..200L).random())
            }
        }
    }

    private fun finish(message: String = "") {
        if (stopping) return
        stopping = true
        // Invalidate old callbacks before asynchronous resources finish closing.
        epoch++
        AppState.update { it.copy(active = false, paused = false, epoch = epoch, rms = 0f,
            status = message.ifEmpty { "已停止" }, error = message, source = "", translated = "") }
        sessionJob?.cancel()
        releaseCpu()
        getSharedPreferences("runtime", MODE_PRIVATE).edit {
            putBoolean("running", false); putString("last_stop", message)
        }
        overlay?.close()
        runCatching { projection?.stop() }; projection = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun holdCpu() {
        if (wakeJob?.isActive == true) return
        val lock = getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "jtranslate:audio")
        lock.setReferenceCounted(false)
        wakeLock = lock
        wakeJob = scope.launch {
            while (isActive && !stopping) {
                lock.acquire(10 * 60 * 1000L)
                delay(5 * 60 * 1000L)
            }
        }
    }

    private fun releaseCpu() {
        wakeJob?.cancel(); wakeJob = null
        wakeLock?.let { if (it.isHeld) it.release() }; wakeLock = null
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig); overlay?.style(options)
    }
    override fun onDestroy() {
        if (!stopping) finish()
        scope.cancel()
        super.onDestroy()
    }
}
