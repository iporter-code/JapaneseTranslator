package cn.jtranslate.live.network

import cn.jtranslate.live.BuildConfig
import kotlinx.coroutines.*
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

class SessionFailure(val reason: String, val retryable: Boolean = true) : Exception(reason)

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
            if (!continuation.isCancelled) continuation.resumeWithException(SessionFailure("无法连接后端"))
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

    suspend fun open() {
        validateServer(base)
        if (accessToken.length < 24) throw SessionFailure("请填写后端访问令牌", false)
        val body = "{}".toRequestBody("application/json".toMediaType())
        val response = client.newCall(Request.Builder().url("${base.trimEnd('/')}/v1/sessions")
            .header("Authorization", "Bearer $accessToken").post(body).build()).awaitResponse()
        val info = response.use {
            if (!it.isSuccessful) throw SessionFailure(when (it.code) {
                401 -> "后端令牌无效"; 429 -> "后端会话或额度已满"; else -> "后端暂不可用（${it.code}）"
            }, it.code >= 500)
            JSONObject(it.body?.string() ?: throw SessionFailure("后端响应为空"))
        }
        sid = info.getString("session_id")
        demo = info.optBoolean("demo", false)
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
                        "session.error" -> if (event.optBoolean("fatal")) {
                            val error = SessionFailure("服务错误：${event.optString("code")}", false)
                            ready.completeExceptionally(error)
                            failed(error)
                        } else onEvent(event)
                        else -> onEvent(event)
                    }
                } catch (_: Exception) { failed(SessionFailure("收到无效字幕事件", false)) }
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (!closed) {
                    ready.completeExceptionally(SessionFailure("字幕连接中断"))
                    failed(SessionFailure("字幕连接中断"))
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
        val data = ByteBuffer.allocate(24 + pcm.size).order(ByteOrder.BIG_ENDIAN)
            .put("JTR1".toByteArray()).put(1.toByte()).put(0.toByte()).putShort(24.toShort())
            .putInt(sequence).putLong(first).putInt(pcm.size / 2).put(pcm).array()
        if ((socket?.queueSize() ?: 0) > 64000 || socket?.send(data.toByteString()) != true)
            throw SessionFailure("上传积压，正在重新跟随")
    }

    fun heartbeat() { socket?.send("{\"type\":\"ping\"}") }

    suspend fun close() {
        if (closed) return
        closed = true
        socket?.send("{\"type\":\"stream.stop\"}")
        socket?.close(1000, "stop")
        if (sid.isNotEmpty()) withContext(NonCancellable) {
            withTimeoutOrNull(3000) {
                runCatching {
                    client.newCall(Request.Builder().url("${base.trimEnd('/')}/v1/sessions/$sid")
                        .header("Authorization", "Bearer $accessToken").delete().build()).awaitResponse().close()
                }
            }
        }
        socket?.cancel()
        client.connectionPool.evictAll()
        client.dispatcher.executorService.shutdown()
    }
}
