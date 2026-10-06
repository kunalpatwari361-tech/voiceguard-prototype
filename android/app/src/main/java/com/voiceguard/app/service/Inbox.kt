package com.voiceguard.app.service

import android.content.Context
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.str
import kotlinx.serialization.json.JsonObject

/**
 * Everything the server pushes to this phone, whether it came over the live link (WebSocket) or as a
 * Firebase push while the app was closed. Alerts, verify questions and HD rings come both ways, so each
 * one is shown only the first time it arrives.
 */
object Inbox {
    private val seen = LinkedHashSet<String>()

    private fun key(e: JsonObject): String? = when (e.str("type")) {
        "alert" -> "alert:" + e.obj("alert").str("id")
        "verify_request" -> "verify:" + e.str("request_id")
        "hd_incoming" -> "hd:" + e.str("call_id")
        "family_invite" -> "invite:" + e.obj("invite").str("id")
        else -> null
    }

    @Synchronized
    private fun firstTime(k: String): Boolean {
        if (!seen.add(k)) return false
        if (seen.size > 200) seen.remove(seen.first())
        return true
    }

    suspend fun handle(ctx: Context, e: JsonObject) {
        key(e)?.let { if (!firstTime(it)) return }
        when (e.str("type")) {
            "verify_request" -> Notify.verifyRequest(ctx, e.str("request_id")!!, e.obj("from").str("name") ?: "Family",
                e.str("number"))
            "hd_incoming" -> Notify.hdIncoming(ctx, e.str("call_id")!!, e.obj("from").str("name") ?: "Family")
            "location_request" -> runCatching { Loc.send(ctx, e.str("request_id")) }
            "family_invite" -> e.obj("invite").let { i ->
                Notify.familyInvite(ctx, i.str("invited_by") ?: "Family", i.str("family_name") ?: "a family circle")
            }
            "scamlist_updated" -> Sync.lists()
            "family_updated" -> runCatching { Sync.family() }
            "alert" -> {
                val a = e.obj("alert")
                val p = a.obj("payload")
                when (a.str("kind")) {
                    "impersonation" -> Notify.alert(ctx, "⚠ " + (a.str("title") ?: ""), a.str("body") ?: "",
                        p.str("asker_phone"), "Call ${p.str("asker_name") ?: "them"} now")
                    "cyber_cell" -> Notify.alert(ctx, a.str("title") ?: "", a.str("body") ?: "", "1930", "Call 1930")
                    "scam_call", "panic", "bank_hold" -> {
                        val victim = Sync.member(a.str("from_user_id"))
                        Notify.alert(ctx, "⚠ " + (a.str("title") ?: ""), a.str("body") ?: "",
                            victim?.str("phone") ?: p.str("victim_phone"), "Call ${victim?.str("name") ?: p.str("from_name") ?: "them"} now")
                    }
                    else -> Notify.alert(ctx, a.str("title") ?: "VoiceGuard alert", a.str("body") ?: "")
                }
            }
        }
    }
}
