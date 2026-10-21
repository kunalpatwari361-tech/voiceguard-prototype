package com.voiceguard.app.callerid

import android.content.Context
import android.net.Uri
import android.os.Build
import android.telecom.Call
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Contacts
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.bool
import com.voiceguard.app.data.num
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.str
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonObject
import java.util.concurrent.ConcurrentHashMap

/**
 * Caller Identity & Verification (isolated module). Who is calling, where that name came from, and how far it can be
 * trusted - shown separately from the spam risk and from the AI voice check, which answer different questions.
 */
enum class IdSource { FAMILY, CONTACT, VERIFIED_BUSINESS, TELECOM, VOICEGUARD_USER, COMMUNITY, UNKNOWN }

/** The carrier's caller-number check (STIR/SHAKEN) from Call.Details.getCallerNumberVerificationStatus, Android 11+. */
enum class NumberCheck { PASSED, FAILED, NOT_VERIFIED, UNAVAILABLE }

data class CallerId(
    val number: String,
    val name: String?,
    val source: IdSource,
    val status: String,        // verified / saved / number_confirmed / unverified / conflicting / unknown
    val confidence: String,    // high / medium / low / none
    val detail: String,
    val numberCheck: NumberCheck,
    val spamScore: Double? = null,
    val spamLabel: String? = null,
    val telecomNote: String? = null,
)

/** Pure decision rules (no Android calls), so they are unit-tested on the computer. */
object CallerIdResolver {
    /** android.telecom.Connection: VERIFICATION_STATUS_NOT_VERIFIED = 0, PASSED = 1, FAILED = 2. */
    fun numberCheck(status: Int?): NumberCheck = when (status) {
        1 -> NumberCheck.PASSED
        2 -> NumberCheck.FAILED
        0 -> NumberCheck.NOT_VERIFIED
        else -> NumberCheck.UNAVAILABLE
    }

    /** The user's own family and contacts first (offline), then the server's allowed sources, else unknown. */
    fun resolve(number: String, familyName: String?, contactName: String?, server: JsonObject?, check: NumberCheck): CallerId {
        val spam = server.obj("spam")
        val telecom = server.obj("telecom")?.takeIf { it.bool("available") != true }?.str("reason")
        fun make(name: String?, source: IdSource, status: String, confidence: String, detail: String) =
            CallerId(number, name, source, status, confidence, detail, check, spam.num("score"), spam.str("label"), telecom)
        if (!familyName.isNullOrBlank()) return make(familyName, IdSource.FAMILY, "saved", "high", "Your family member in VoiceGuard.")
        if (!contactName.isNullOrBlank()) return make(contactName, IdSource.CONTACT, "saved", "high", "The name you saved in your contacts.")
        val id = server.obj("identity")
        val name = id.str("name")
        if (id != null && !name.isNullOrBlank()) {
            val source = when (id.str("source")) {
                "verified_business" -> IdSource.VERIFIED_BUSINESS
                "telecom" -> IdSource.TELECOM
                "voiceguard_user" -> IdSource.VOICEGUARD_USER
                else -> IdSource.COMMUNITY
            }
            // a community or VoiceGuard-member name can never be shown as verified, whatever arrives
            val status = id.str("status") ?: "unverified"
            val safeStatus = if (status == "verified" && source != IdSource.VERIFIED_BUSINESS && source != IdSource.TELECOM) "unverified" else status
            val confidence = if (source == IdSource.COMMUNITY) "low" else id.str("confidence") ?: "low"
            return make(name, source, safeStatus, confidence, id.str("detail").orEmpty())
        }
        return make(null, IdSource.UNKNOWN, "unknown", "none", "No reliable identity for this number.")
    }
}

/** The phone side: offline matching, the optional online lookup, and the carrier check of the ringing call. */
object CallerIdentity {
    private val checks = ConcurrentHashMap<String, NumberCheck>()

    /** Called by the call-screening service for every incoming call (Android 11+ gives a carrier result). */
    fun noteVerification(number: String, details: Call.Details) {
        if (Build.VERSION.SDK_INT >= 30 && number.isNotBlank())
            checks[Numbers.normalize(number)] = CallerIdResolver.numberCheck(details.callerNumberVerificationStatus)
    }

    fun numberCheck(number: String, details: Call.Details?): NumberCheck =
        if (details != null && Build.VERSION.SDK_INT >= 30) CallerIdResolver.numberCheck(details.callerNumberVerificationStatus)
        else checks[Numbers.normalize(number)] ?: NumberCheck.UNAVAILABLE

    /** Never throws and never blocks a call: a failed or slow lookup simply gives "unknown". */
    suspend fun lookup(ctx: Context, number: String, check: NumberCheck = NumberCheck.UNAVAILABLE): CallerId {
        val family = if (Prefs.callerIdContacts) Sync.memberByPhone(number)?.str("name") else null
        val contact = if (Prefs.callerIdContacts && family == null) Contacts.nameFor(ctx, number) else null
        // the user's own contacts are never sent anywhere; online lookups only for numbers they have not saved
        val server = if (Prefs.callerIdOnline && family == null && contact == null) withTimeoutOrNull(3_500) {
            runCatching { Api.get("/api/caller-identity/lookup/${Uri.encode(number)}").asObj() }.getOrNull()
        } else null
        return CallerIdResolver.resolve(number, family, contact, server, check)
    }
}
