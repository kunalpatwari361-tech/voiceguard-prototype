package com.voiceguard.app.telecom

import android.telecom.Call
import android.telecom.CallScreeningService
import android.telecom.CallScreeningService.CallResponse
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Contacts
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.num
import com.voiceguard.app.data.objs
import com.voiceguard.app.data.str
import com.voiceguard.app.service.Notify
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Runs on every real incoming call from a number not in contacts (Android call-screening role),
 * or on every incoming call when VoiceGuard is the default phone app (saved contacts are then skipped).
 * Block Numbers (25) and Community Scam List (26) are decided instantly from the offline cache;
 * Number Info (15) + Spam Warning (24) come from the server a moment later as a notification.
 */
class ScreeningService : CallScreeningService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onScreenCall(details: Call.Details) {
        if (details.callDirection != Call.Details.DIRECTION_INCOMING) {
            respondToCall(details, CallResponse.Builder().build())
            return
        }
        val n = Numbers.normalize(details.handle?.schemeSpecificPart.orEmpty())
        val resp = CallResponse.Builder()
        // As the default phone app we also screen calls from saved contacts; those are never spam-flagged.
        val saved = n !in Prefs.blockedNumbers && Contacts.nameFor(this, n) != null
        when {
            saved -> Unit
            n in Prefs.blockedNumbers -> {
                resp.setDisallowCall(true).setRejectCall(true).setSkipNotification(false)
                Notify.spamWarning(this, n, "Blocked by your family. VoiceGuard rejected this call.")
            }
            n in Prefs.scamNumbers ->
                Notify.spamWarning(this, n, "Reported as SCAM by the VoiceGuard community. Do not share OTP or send money.")
        }
        respondToCall(details, resp.build())
        RecentCalls.add(n)
        if (!saved && n !in Prefs.blockedNumbers) scope.launch {
            val info = withTimeoutOrNull(4000) { runCatching { Api.get("/api/numbers/$n?user_id=${Prefs.userId}").asObj() }.getOrNull() }
                ?: return@launch
            RecentCalls.setInfo(n, info)
            val score = info.num("spam_score") ?: 0.0
            if (score >= 0.5 && n !in Prefs.scamNumbers) {
                val flags = info.objs("flags").joinToString(" ") { it.str("en").orEmpty() }
                Notify.spamWarning(this@ScreeningService, n, "Risk ${(score * 100).toInt()}%. $flags")
            }
        }
    }
}

/** In-memory list of recently screened callers (shown on the dialer screen). */
object RecentCalls {
    data class Entry(val number: String, val at: Long, var info: kotlinx.serialization.json.JsonObject? = null)

    val items = mutableListOf<Entry>()

    @Synchronized
    fun add(n: String) {
        items.add(0, Entry(n, System.currentTimeMillis()))
        if (items.size > 30) items.removeAt(items.size - 1)
    }

    @Synchronized
    fun setInfo(n: String, info: kotlinx.serialization.json.JsonObject) {
        items.firstOrNull { it.number == n }?.info = info
    }
}
