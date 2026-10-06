package com.voiceguard.app.telecom

import android.app.role.RoleManager
import android.telecom.Call
import android.telecom.CallScreeningService
import android.telecom.CallScreeningService.CallResponse
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Contacts
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.int
import com.voiceguard.app.data.json
import com.voiceguard.app.data.num
import com.voiceguard.app.data.objs
import com.voiceguard.app.data.str
import com.voiceguard.app.service.Notify
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonObject

/**
 * Runs on every real incoming call (Android call-screening role).
 * Block Numbers (25) and Community Scam List (26) are decided instantly from the offline cache;
 * Number Info (15) + Spam Warning (24) arrive from the server a moment later, shown as a Truecaller-style
 * caller ID card, and the family is alerted automatically when a reported scam number calls (17).
 */
class ScreeningService : CallScreeningService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onScreenCall(details: Call.Details) {
        if (details.callDirection != Call.Details.DIRECTION_INCOMING) {
            respondToCall(details, CallResponse.Builder().build())
            return
        }
        val n = Numbers.normalize(details.handle?.schemeSpecificPart.orEmpty())
        val blocked = n in Prefs.blockedNumbers
        val reported = n in Prefs.scamNumbers
        val resp = CallResponse.Builder()
        if (blocked) {
            resp.setDisallowCall(true).setRejectCall(true).setSkipNotification(false)
            Notify.spamWarning(this, n, "Blocked by your family. VoiceGuard rejected this call.")
        }
        respondToCall(details, resp.build())
        RecentCalls.add(n)
        if (blocked) return

        // As the default Phone app our own call screen shows all of this; otherwise float a caller ID card.
        val isDialer = getSystemService(RoleManager::class.java).isRoleHeld(RoleManager.ROLE_DIALER)
        val family = Sync.memberByPhone(n)
        val contact = Contacts.nameFor(this, n)
        if (!isDialer) when {
            family != null -> CallerIdOverlay.show(this, family.str("name").orEmpty(), "Family · verified number", CallerIdOverlay.GREEN)
            reported -> CallerIdOverlay.show(this, "⚠ Reported scam", "${Numbers.pretty(n)} · reported by the VoiceGuard community", CallerIdOverlay.RED)
            contact != null -> CallerIdOverlay.show(this, contact, Numbers.pretty(n), CallerIdOverlay.GREEN)
            else -> CallerIdOverlay.show(this, Numbers.pretty(n), "Unknown number · checking…", CallerIdOverlay.GREY)
        }
        if (reported) {
            Notify.spamWarning(this, n, "Reported as SCAM by the VoiceGuard community. Do not share OTP or send money.")
            alertFamily(n, "is getting a call from a reported scam number")
        }
        if (family != null || contact != null) return
        scope.launch {
            val info = withTimeoutOrNull(4000) { runCatching { Api.get("/api/numbers/$n?user_id=${Prefs.userId}").asObj() }.getOrNull() }
                ?: return@launch
            RecentCalls.setInfo(n, info)
            val score = info.num("spam_score") ?: 0.0
            val flags = info.objs("flags").joinToString(" ") { it.str("en").orEmpty() }
            if (!isDialer) CallerIdOverlay.show(this@ScreeningService,
                if (score >= 0.5) "⚠ Likely spam · ${(score * 100).toInt()}%" else Numbers.pretty(n),
                listOfNotNull(info.str("country"), flags.ifBlank { null }).joinToString(" · ").ifBlank { "Unknown number" },
                when { score >= 0.6 -> CallerIdOverlay.RED; score >= 0.35 -> CallerIdOverlay.AMBER; else -> CallerIdOverlay.GREY })
            if (score >= 0.5 && !reported) {
                Notify.spamWarning(this@ScreeningService, n, "Risk ${(score * 100).toInt()}%. $flags")
                if (score >= 0.7) alertFamily(n, "is getting a call from a high-risk number")
            }
        }
    }

    private fun alertFamily(n: String, what: String) {
        if (Prefs.familyId == null || !RecentCalls.shouldAlert(n)) return
        scope.launch {
            runCatching {
                Api.post("/api/alerts", json("from_user_id" to Prefs.userId, "kind" to "scam_call",
                    "title" to "${Prefs.name} $what", "body" to "Caller ${Numbers.pretty(n)}. Call ${Prefs.name} and check they are safe.",
                    "payload" to mapOf("number" to n, "victim_phone" to Prefs.phone)))
            }
        }
    }
}

/** In-memory list of recently screened callers (shown on the dialer screen). */
object RecentCalls {
    data class Entry(val number: String, val at: Long, var info: JsonObject? = null)

    val items = mutableListOf<Entry>()
    private val alerted = HashMap<String, Long>()

    @Synchronized
    fun add(n: String) {
        items.add(0, Entry(n, System.currentTimeMillis()))
        if (items.size > 30) items.removeAt(items.size - 1)
    }

    @Synchronized
    fun setInfo(n: String, info: JsonObject) {
        items.firstOrNull { it.number == n }?.info = info
    }

    /** At most one family alert per number every 10 minutes. */
    @Synchronized
    fun shouldAlert(n: String): Boolean {
        val now = System.currentTimeMillis()
        if (now - (alerted[n] ?: 0L) < 10 * 60_000L) return false
        alerted[n] = now
        return true
    }
}
