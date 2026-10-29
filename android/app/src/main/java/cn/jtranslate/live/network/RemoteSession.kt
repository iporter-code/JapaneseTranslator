package cn.jtranslate.live.network

import cn.jtranslate.live.BuildConfig
import kotlinx.coroutines.*
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import android.os.SystemClock
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import java.net.UnknownHostException
import java.net.SocketTimeoutException
import java.net.ConnectException
import javax.net.ssl.SSLException
import java.security.cert.CertificateException

class SessionFailure(val reason: String, val retryable: Boolean = true) : Exception(reason)

fun describeConnectionFailure(error: Throwable, stage: String): SessionFailure {
    val causes = generateSequence(error) { it.cause }.take(8).toList()
    val reason = when {
        causes.any { it is UnknownHostException } -> "域名解析失败：手机未能找到服务器。请核对地址，并用手机浏览器打开同一地址的 /health。"
        causes.any { it is SSLException || it is CertificateException } -> "HTTPS 证书连接失败：请检查手机日期时间，以及网络代理是否启用了 HTTPS 解密。"
        causes.any { it is SocketTimeoutException } -> "连接超时：当前手机网络未及时连通服务器，可切换 Wi-Fi/流量对照测试。"
        causes.any { it is ConnectException } -> "连接被拒绝或不可达：请确认电脑模型服务仍在运行。"
        else -> "$stage（${error.javaClass.simpleName}）：请先测试后端连接。"
    }
    return SessionFailure(reason)
}

fun validateServer(raw: String): HttpUrl {
    val url = raw.toHttpUrlOrNull() ?: throw SessionFailure("后端地址格式不正确", false)
    if (url.username.isNotEmpty() || url.password.isNotEmpty() || url.query != null || url.fragment != null)
        throw SessionFailure("后端地址不能包含凭证或查询参数", false)
    if (url.scheme != "https" && !(BuildConfig.DEBUG && url.host in listOf("127.0.0.1", "localhost", "10.0.2.2")))
        throw SessionFailure("远程后端必须使用 HTTPS；USB 调试可用本机地址", false)
    return url
}

private suspend fun Call.awaitResponse(): Response = suspendCancellableCoroutine { continuation ->
    continuation.invokeOnCancellation { cancel() }
    enqueue(object : Callback {
        override fun onFailure(call: Call, e: java.io.IOException) {
            if (!continuation.isCancelled) continuation.resumeWithException(describeConnectionFailure(e, "请求后端失败"))
        }
        override fun onResponse(call: Call, response: Response) {
            if (continuation.isCancelled) response.close() else continuation.resume(response)
        }
    })
}

class RemoteSession(private val base: String, private val accessToken: String, private val epoch: Int,
    private val onEvent: (JSONObject) -> Unit, private val failed: (SessionFailure) -> Unit) {
    private val client = OkHttpClient.Builder().connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS).pingInterval(10, TimeUnit.SECONDS).build()
    private var sid = ""
    private var socket: WebSocket? = null
    private val ready = CompletableDeferred<Unit>()
    @Volatile private var closed = false
    var demo = true; private set
    private val encoder = AudioPacketEncoder()
    private var compressAudio = false
    @Volatile private var pingAt = 0L

    suspend fun open() {
        validateServer(base)
        if (accessToken.length < 24) throw SessionFailure("请填写后端访问令牌", false)
        val body = "{}".toRequestBody("application/json".toMediaType())
        val response = client.newCall(Request.Builder().url("${base.trimEnd('/')}/v1/sessions")
            .header("Authorization", "Bearer $accessToken").post(body).build()).awaitResponse()
        val info = withContext(Dispatchers.IO) { response.use {
            if (!it.isSuccessful) throw SessionFailure(when (it.code) {
                401 -> "后端令牌无效"; 429 -> "后端会话或额度已满"; else -> "后端暂不可用（${it.code}）"
            }, it.code >= 500 || it.code == 429)
            JSONObject(it.body?.string() ?: throw SessionFailure("后端响应为空"))
        } }
        sid = info.getString("session_id")
        demo = info.optBoolean("demo", false)
        compressAudio = info.optJSONArray("audio_compression")?.let { array ->
            (0 until array.length()).any { array.optString(it) == "zlib" }
        } ?: false
        // Derive URL from configured host; never trust an arbitrary server-supplied host.
        val wsUrl = "${base.trimEnd('/')}/v1/sessions/$sid/stream"
        socket = client.newWebSocket(Request.Builder().url(wsUrl)
            .header("Authorization", "Bearer ${info.getString("stream_token")}").build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(JSONObject().put("type", "stream.start").put("version", 1).put("epoch", epoch).toString())
            }
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (closed) return
                try {
                    val event = JSONObject(text)
                    if (event.optInt("epoch") != epoch) return
                    when (event.optString("type")) {
                        "stream.ready" -> ready.complete(Unit)
                        "pong" -> if (pingAt > 0) onEvent(event.put("client_rtt_ms", SystemClock.elapsedRealtime() - pingAt))
                        "session.error" -> if (event.optBoolean("fatal")) {
                            val code = event.optString("code")
                            val error = SessionFailure("服务错误：$code", code in listOf("provider_unavailable", "provider_overloaded"))
                            ready.completeExceptionally(error)
                            failed(error)
                        } else onEvent(event)
                        else -> onEvent(event)
                    }
                } catch (_: Exception) { failed(SessionFailure("收到无效字幕事件", false)) }
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (!closed) {
                    val error = response?.let { SessionFailure("字幕握手失败（HTTP ${it.code}），请测试连接并核对后端地址", it.code >= 500) }
                        ?: describeConnectionFailure(t, "字幕连接中断")
                    ready.completeExceptionally(error)
                    failed(error)
                }
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (!closed) failed(SessionFailure("服务端已关闭连接"))
            }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, null)
                if (!closed) failed(SessionFailure("服务端已关闭连接"))
            }
        })
        withTimeout(25000) { ready.await() }
    }

    fun send(pcm: ByteArray, sequence: Int, first: Long) {
        if (closed) throw SessionFailure("连接已关闭")
        val data = encoder.encode(pcm, sequence, first, compressAudio)
        if ((socket?.queueSize() ?: 0) > 64000 || socket?.send(data.toByteString()) != true)
            throw SessionFailure("上传积压，正在重新跟随")
    }

    fun heartbeat() { pingAt = SystemClock.elapsedRealtime(); socket?.send("{\"type\":\"ping\"}") }

    suspend fun close() {
        if (closed) return
        closed = true
        socket?.send("{\"type\":\"stream.stop\"}")
        socket?.close(1000, "stop")
        if (sid.isNotEmpty()) withContext(NonCancellable) {
            withTimeoutOrNull(3000) {
                runCatching {
                    withContext(Dispatchers.IO) {
                        client.newCall(Request.Builder().url("${base.trimEnd('/')}/v1/sessions/$sid")
                            .header("Authorization", "Bearer $accessToken").delete().build()).awaitResponse().close()
                    }
                }
            }
        }
        socket?.cancel()
        encoder.close()
        client.connectionPool.evictAll()
        client.dispatcher.executorService.shutdown()
    }
}

suspend fun testBackendConnection(base: String, token: String, progress: (String) -> Unit): String {
    val origin = validateServer(base).toString().trimEnd('/')
    if (token.length < 24) throw SessionFailure("请先填写并保存访问令牌", false)
    val client = OkHttpClient.Builder().connectTimeout(12, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS).build()
    var session: RemoteSession? = null
    try {
        progress("1/3 检查 HTTPS 服务地址…")
        val response = client.newCall(Request.Builder().url("$origin/health").build()).awaitResponse()
        withContext(Dispatchers.IO) { response.use {
            if (!it.isSuccessful) throw SessionFailure("服务地址检查失败（HTTP ${it.code}），临时地址可能已失效", false)
            val info = runCatching { JSONObject(it.body?.string().orEmpty()) }.getOrNull()
                ?: throw SessionFailure("此地址没有返回翻译服务，请检查地址，不要填写网页或 /health 路径", false)
            if (!info.optBoolean("ready", true)) throw SessionFailure("电脑模型尚未就绪，请等待模型加载完成")
        } }
        progress("2/3 验证令牌并建立字幕连接…")
        val pong = CompletableDeferred<Unit>()
        val connection = RemoteSession(origin, token, 1, { event ->
            if (event.optString("type") == "pong") pong.complete(Unit)
        }, { error -> pong.completeExceptionally(error) })
        session = connection
        connection.open()
        progress("3/3 检查字幕连接心跳…")
        connection.heartbeat()
        withTimeout(10000) { pong.await() }
        return if (connection.demo) "连接通过：HTTPS、令牌、字幕心跳正常；当前后端为演示模式。"
            else "连接通过：HTTPS、令牌、字幕心跳正常，可以授权并开始。"
    } catch (error: TimeoutCancellationException) {
        throw SessionFailure("字幕连接等待超时：地址能访问，但字幕通道未及时响应。")
    } finally {
        session?.close()
        client.connectionPool.evictAll()
        client.dispatcher.executorService.shutdown()
    }
}
