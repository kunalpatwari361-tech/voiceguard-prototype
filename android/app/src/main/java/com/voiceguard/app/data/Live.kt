package com.voiceguard.app.data

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/** The phone's always-on link to the server: presence, call state, pushes from family. */
object Live {
    val events = MutableSharedFlow<JsonObject>(extraBufferCapacity = 64)
    val connected = MutableStateFlow(false)

    private var ws: WebSocket? = null
    private var wanted = false
    private var retry: Job? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    @Synchronized
    fun start() {
        wanted = true
        if (ws == null) connect()
    }

    @Synchronized
    fun restart() {
        ws?.cancel()
        ws = null
        connected.value = false
        if (wanted) connect()
    }

    fun stop() {
        wanted = false
        ws?.close(1000, null)
        ws = null
    }

    fun send(o: JsonObject): Boolean = ws?.send(o.toString()) ?: false

    @Synchronized
    private fun connect() {
        val uid = Prefs.userId ?: return
        val url = Prefs.serverUrl.replaceFirst("http", "ws") + "/ws/$uid"
        ws = Api.client.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                connected.value = true
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                runCatching { VgJson.parseToJsonElement(text).asObj() }.getOrNull()?.let { events.tryEmit(it) }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = dropped(webSocket)
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = dropped(webSocket)
        })
    }

    private fun dropped(socket: WebSocket) {
        synchronized(this) {
            if (ws !== socket) return
            ws = null
            connected.value = false
        }
        if (wanted) {
            retry?.cancel()
            retry = scope.launch {
                delay(3000)
                synchronized(this@Live) { if (ws == null && wanted) connect() }
            }
        }
    }
}
