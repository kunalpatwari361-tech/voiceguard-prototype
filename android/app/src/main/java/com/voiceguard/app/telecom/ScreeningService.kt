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
import com.voiceguard.app.callerid.CallerIdentity
import com.voiceguard.app.data.bool
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
        CallerIdentity.noteVerification(n, details)   // carrier number check for the Caller Identity card
        com.voiceguard.app.service.AppStats.inc("calls_screened")
        val reported = n in Prefs.scamNumbers
        val familyBlocked = n in Prefs.blockedNumbers
        val autoBlocked = !familyBlocked && reported && Prefs.autoBlockScam   // Auto-Block Spam
        val resp = CallResponse.Builder()
        if (familyBlocked || autoBlocked) {
            resp.setDisallowCall(true).setRejectCall(true).setSkipNotification(false)
            Notify.spamWarning(this, n, if (familyBlocked) "Blocked by your family. VoiceGuard rejected this call."
                else "Reported as SCAM by the VoiceGuard community. VoiceGuard declined this call for you.")
        }
        respondToCall(details, resp.build())
        RecentCalls.add(n)
        if (autoBlocked) alertFamily(n, "got a call from a reported scam number - VoiceGuard blocked it")
        if (familyBlocked || autoBlocked) com.voiceguard.app.service.AppStats.inc("auto_blocked")
        if (familyBlocked || autoBlocked) return

        // Floating caller card: family / saved contact / unknown / suspicious / spam, over any screen while it rings
        // (not over VoiceGuard's own call screen, which shows all of this itself).
        val family = Sync.memberByPhone(n)
        val contact = Contacts.nameFor(this, n)
        val floating = { Prefs.floatingCard && !CallNotifier.uiVisible }
        if (floating()) CallerIdOverlay.show(this, CallStatus.of(this, n))
        if (reported) {
            Notify.spamWarning(this, n, "Reported as SCAM by the VoiceGuard community. Do not share OTP or send money.")
            alertFamily(n, "is getting a call from a reported scam number")
        }
        if (family != null || contact != null) return
        scope.launch {
            val info = withTimeoutOrNull(4000) { runCatching { Api.get("/api/numbers/$n?user_id=${Prefs.userId}").asObj() }.getOrNull() }
                ?: return@launch
            RecentCalls.setInfo(n, info)
            CallStatus.remember(n, info)
            val score = info.num("spam_score") ?: 0.0
            val flags = info.objs("flags").joinToString(" ") { it.str("en").orEmpty() }
            // the server's answer (spam risk, VoiceGuard member name) updates the floating card and the call notification
            if (floating()) CallerIdOverlay.show(this@ScreeningService, CallStatus.of(this@ScreeningService, n))
            CallNotifier.update(this@ScreeningService)
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
