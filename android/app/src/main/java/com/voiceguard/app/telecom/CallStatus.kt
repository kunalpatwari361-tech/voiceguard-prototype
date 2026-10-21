package com.voiceguard.app.telecom

import android.content.Context
import com.voiceguard.app.data.Contacts
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.num
import com.voiceguard.app.data.str
import kotlinx.serialization.json.JsonObject
import java.util.concurrent.ConcurrentHashMap

/**
 * One clear answer for an incoming call - family, saved contact, VoiceGuard member, unknown, suspicious or spam -
 * shared by the floating caller card and the incoming-call notification so both always say the same thing.
 */
object CallStatus {
    enum class Kind { FAMILY, SAVED, MEMBER, UNKNOWN, SUSPICIOUS, SPAM }
    data class Status(val kind: Kind, val badge: String, val title: String, val detail: String, val color: Int)

    /** The server's number info for recent callers (spam score, label, VoiceGuard member name). */
    private val server = ConcurrentHashMap<String, JsonObject>()

    fun remember(number: String, info: JsonObject) {
        server[Numbers.normalize(number)] = info
        if (server.size > 50) server.keys.firstOrNull()?.let { server.remove(it) }
    }

    fun of(ctx: Context, number: String): Status {
        val n = Numbers.normalize(number)
        val pretty = Numbers.pretty(n)
        val family = Sync.memberByPhone(n)?.str("name")
        val contact = family ?: Contacts.nameFor(ctx, n)
        val info = server[n]
        val score = info.num("spam_score") ?: 0.0
        val pct = (score * 100).toInt()
        val label = info.str("label") ?: info.str("country")
        return when {
            family != null -> Status(Kind.FAMILY, "👪 FAMILY · SAVED", family, "Saved in your family circle", GREEN)
            n in Prefs.scamNumbers -> Status(Kind.SPAM, "⚠ SPAM CALL", contact ?: pretty,
                "Reported as scam by the VoiceGuard community. Never share OTP or send money.", RED)
            n in Prefs.blockedNumbers -> Status(Kind.SPAM, "⛔ BLOCKED NUMBER", contact ?: pretty, "Blocked by your family", RED)
            contact != null -> Status(Kind.SAVED, "✅ SAVED CONTACT", contact, pretty, GREEN)
            score >= 0.6 -> Status(Kind.SPAM, "⚠ LIKELY SPAM · $pct%", info.str("name") ?: pretty,
                label ?: "High-risk number. Do not share OTP.", RED)
            score >= 0.35 -> Status(Kind.SUSPICIOUS, "⚠ SUSPICIOUS · $pct%", info.str("name") ?: pretty,
                label ?: "Be careful with this caller", AMBER)
            info.str("name_source") == "voiceguard" && info.str("name") != null ->
                Status(Kind.MEMBER, "✓ VOICEGUARD MEMBER", info.str("name")!!, "$pretty · number confirmed by OTP, not in your contacts", BLUE)
            else -> Status(Kind.UNKNOWN, "❓ UNKNOWN NUMBER", pretty,
                (label ?: "Not in your contacts") + if (info == null) " · checking…" else "", GREY)
        }
    }

    const val GREEN = 0xFF22C55E.toInt()
    const val BLUE = 0xFF38BDF8.toInt()
    const val AMBER = 0xFFF59E0B.toInt()
    const val RED = 0xFFEF4444.toInt()
    const val GREY = 0xFF94A3B8.toInt()
}
