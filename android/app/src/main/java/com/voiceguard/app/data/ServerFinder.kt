package com.voiceguard.app.data

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.SystemClock
import com.voiceguard.app.VgApp
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.joinAll
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.SocketTimeoutException
import java.util.concurrent.TimeUnit

/**
 * Finds the laptop server WITHOUT a cable. Runs by itself whenever the server cannot be reached and tries, in order:
 *  1. the address that worked last time,
 *  2. USB (127.0.0.1 through adb reverse),
 *  3. a Wi-Fi broadcast the server answers (backend app/discovery.py, UDP 8001),
 *  4. the Wi-Fi gateway (the phone is on the laptop's Mobile hotspot),
 *  5. every address of the phone's Wi-Fi network (/24) - for Wi-Fi that blocks broadcasts.
 * The first address whose /api/health answers as VoiceGuard is saved as the server address.
 */
object ServerFinder {
    private const val PORT = 8000
    private const val DISCOVERY_PORT = 8001
    private const val USB = "http://127.0.0.1:$PORT"

    /** The address found by the last search (null while none) - the setup screen shows it. */
    val found = MutableStateFlow<String?>(null)
    private val lock = Mutex()
    private var lastSearch = 0L
    private var bound = false

    private val cm get() = VgApp.instance.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    /** Searches (at most once every 15 s) and returns a working server address, or null. */
    suspend fun find(): String? = lock.withLock {
        val now = SystemClock.elapsedRealtime()
        if (now - lastSearch < 15_000) return found.value?.takeIf { it == Prefs.serverUrl }
        lastSearch = now
        withContext(Dispatchers.IO) {
            if (bound) { runCatching { cm.bindProcessToNetwork(null) }; bound = false }
            val wifi = wifiNetwork()
            val tried = mutableSetOf<String>()
            fun ok(u: String) = tried.add(u) && healthy(u, if (u == USB) null else wifi)
            val current = Prefs.serverUrl
            val url = when {
                ok(current) -> current
                ok(USB) -> USB
                else -> broadcast(wifi).firstOrNull { ok(it) }
                    ?: gateways(wifi).firstOrNull { ok(it) }
                    ?: scan(wifi, tried)
            }
            if (url != null) {
                if (url != current) Prefs.serverUrlRaw = url
                // Wi-Fi without internet (e.g. a hotspot): Android sends traffic over mobile data unless told otherwise
                if (url != USB && wifi != null && cm.activeNetwork != wifi) {
                    bound = runCatching { cm.bindProcessToNetwork(wifi) }.getOrDefault(false)
                }
            }
            found.value = url
            url
        }
    }

    private fun wifiNetwork(): Network? = runCatching {
        @Suppress("DEPRECATION")
        cm.allNetworks.firstOrNull { cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true }
    }.getOrNull()

    private fun client(net: Network?, connectMs: Long): OkHttpClient = Api.client.newBuilder()
        .connectTimeout(connectMs, TimeUnit.MILLISECONDS).readTimeout(2500, TimeUnit.MILLISECONDS)
        .callTimeout(3500, TimeUnit.MILLISECONDS)
        .apply { if (net != null) socketFactory(net.socketFactory) }
        .build()

    /** True if a VoiceGuard server answers at [url]. */
    private fun healthy(url: String, net: Network?, connectMs: Long = 1500): Boolean = runCatching {
        client(net, connectMs).newCall(Request.Builder().url("$url/api/health").build()).execute().use { r ->
            val t = r.body?.string().orEmpty()
            r.isSuccessful && "\"ok\":true" in t.replace(" ", "") && "models" in t
        }
    }.getOrDefault(false)

    private fun myAddress(net: Network?): Pair<Inet4Address, Int>? = runCatching {
        val la = cm.getLinkProperties(net ?: return null)?.linkAddresses ?: return null
        la.firstOrNull { it.address is Inet4Address }?.let { it.address as Inet4Address to it.prefixLength }
    }.getOrNull()

    private fun broadcast(net: Network?): List<String> = runCatching {
        DatagramSocket().use { s ->
            net?.bindSocket(s)
            s.broadcast = true
            s.soTimeout = 400
            val msg = "VOICEGUARD?".toByteArray()
            val targets = mutableListOf("255.255.255.255")
            myAddress(net)?.let { (ip, prefix) ->
                val b = ip.address
                if (prefix in 8..30) {
                    val n = ((b[0].toInt() and 255) shl 24) or ((b[1].toInt() and 255) shl 16) or
                        ((b[2].toInt() and 255) shl 8) or (b[3].toInt() and 255)
                    val bc = n or ((1 shl (32 - prefix)) - 1)
                    targets += "${bc ushr 24 and 255}.${bc ushr 16 and 255}.${bc ushr 8 and 255}.${bc and 255}"
                }
            }
            val out = linkedSetOf<String>()
            repeat(2) {   // a broadcast can be lost on Wi-Fi: send twice
                targets.distinct().forEach { t ->
                    runCatching { s.send(DatagramPacket(msg, msg.size, InetAddress.getByName(t), DISCOVERY_PORT)) }
                }
                val end = SystemClock.elapsedRealtime() + 700
                val buf = ByteArray(512)
                while (SystemClock.elapsedRealtime() < end) {
                    val p = DatagramPacket(buf, buf.size)
                    try { s.receive(p) } catch (e: SocketTimeoutException) { break }
                    val text = String(p.data, 0, p.length)
                    if (text.startsWith("VOICEGUARD ")) {
                        val port = runCatching { VgJson.parseToJsonElement(text.removePrefix("VOICEGUARD ")).asObj().int("port") }
                            .getOrNull() ?: PORT
                        out += "http://${p.address.hostAddress}:$port"
                    }
                }
                if (out.isNotEmpty()) return@use out.toList()
            }
            out.toList()
        }
    }.getOrDefault(emptyList())

    private fun gateways(net: Network?): List<String> = runCatching {
        cm.getLinkProperties(net ?: return emptyList())?.routes.orEmpty()
            .mapNotNull { it.gateway as? Inet4Address }
            .filter { !it.isAnyLocalAddress }
            .map { "http://${it.hostAddress}:$PORT" }.distinct()
    }.getOrDefault(emptyList())

    /** Last resort: ask every address of the phone's Wi-Fi /24 network (64 at a time, ~3 s). */
    private suspend fun scan(net: Network?, skip: Set<String>): String? = coroutineScope {
        val (ip, _) = myAddress(net) ?: return@coroutineScope null
        val b = ip.address.map { it.toInt() and 255 }
        val hosts = (1..254).map { "${b[0]}.${b[1]}.${b[2]}.$it" }.filter { it != ip.hostAddress }
        val sem = Semaphore(64)
        val result = CompletableDeferred<String?>()
        val jobs = hosts.map { h ->
            launch(Dispatchers.IO) {
                sem.withPermit {
                    val u = "http://$h:$PORT"
                    if (!result.isCompleted && u !in skip && healthy(u, net, connectMs = 600)) result.complete(u)
                }
            }
        }
        launch { jobs.joinAll(); result.complete(null) }
        val r = result.await()
        jobs.forEach { it.cancel() }
        r
    }
}
