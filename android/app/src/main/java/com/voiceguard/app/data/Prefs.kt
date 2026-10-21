package com.voiceguard.app.data

import android.content.Context
import android.content.SharedPreferences
import kotlin.properties.ReadWriteProperty
import kotlin.reflect.KProperty

/** Small persistent settings + offline caches (scam list, blocked numbers, family). */
object Prefs {
    private lateinit var sp: SharedPreferences

    fun init(ctx: Context) {
        sp = ctx.getSharedPreferences("voiceguard", Context.MODE_PRIVATE)
    }

    private fun str(key: String, def: String? = null) = object : ReadWriteProperty<Any, String?> {
        override fun getValue(thisRef: Any, property: KProperty<*>) = sp.getString(key, def)
        override fun setValue(thisRef: Any, property: KProperty<*>, value: String?) =
            sp.edit().putString(key, value).apply()
    }

    private fun bool(key: String, def: Boolean) = object : ReadWriteProperty<Any, Boolean> {
        override fun getValue(thisRef: Any, property: KProperty<*>) = sp.getBoolean(key, def)
        override fun setValue(thisRef: Any, property: KProperty<*>, value: Boolean) =
            sp.edit().putBoolean(key, value).apply()
    }

    private fun long(key: String) = object : ReadWriteProperty<Any, Long> {
        override fun getValue(thisRef: Any, property: KProperty<*>) = sp.getLong(key, 0L)
        override fun setValue(thisRef: Any, property: KProperty<*>, value: Long) =
            sp.edit().putLong(key, value).apply()
    }

    private fun set(key: String) = object : ReadWriteProperty<Any, Set<String>> {
        override fun getValue(thisRef: Any, property: KProperty<*>): Set<String> =
            sp.getStringSet(key, emptySet())!!.toSet()
        override fun setValue(thisRef: Any, property: KProperty<*>, value: Set<String>) =
            sp.edit().putStringSet(key, value).apply()
    }

    var serverUrlRaw by str("server", "http://127.0.0.1:8000")
    val serverUrl: String get() = (serverUrlRaw ?: "http://127.0.0.1:8000").trimEnd('/')

    var userId by str("user_id")
    /** Login token from OTP sign-up; sent with every request. */
    var token by str("token")
    var name by str("name")
    var phone by str("phone")
    var role by str("role", "member")
    var familyId by str("family_id")
    var familyJson by str("family_json")
    /** Firebase settings handed out by our server (public values) and its push status text. */
    var pushConfig by str("push_config")
    var pushStatus by str("push_status")

    private var hindiOld by bool("hindi", false)
    private var langCode by str("lang")
    /** App language code (I18n.ALL). Older installs only had the Hindi switch. */
    var lang: String
        get() = langCode ?: if (hindiOld) "hi" else "en"
        set(v) { langCode = v }
    var hindi: Boolean
        get() = lang == "hi"
        set(v) { I18n.select(if (v) "hi" else "en") }
    /** "system", "light" or "dark". */
    var theme by str("theme", "system")
    var autoCheck by bool("auto_check", true)
    var panicPause by bool("panic_pause", true)
    /** Auto-Block Spam: decline numbers the VoiceGuard community reported as scam (family-blocked ones always are). */
    var autoBlockScam by bool("auto_block_scam", true)
    /** Caller ID: show my name (with the Verified badge) to people I call. Mirrors /api/me/caller-id. */
    var showMyName by bool("show_my_name", true)
    /** Share this phone's location with the family circle (every 10 min, on request, and the live map). */
    var shareLocation by bool("share_location", true)
    /** Caller Identity & Verification (callerid/): show identities, match contacts on the phone, online lookups. */
    var callerIdEnabled by bool("cid_enabled", true)
    /** Floating caller card over any screen while a call rings (needs "Display over other apps"). */
    var floatingCard by bool("floating_card", true)
    var callerIdContacts by bool("cid_contacts", true)
    var callerIdOnline by bool("cid_online", true)
    var setupDone by bool("setup_done", false)

    var scamNumbers by set("scam_numbers")
    var blockedNumbers by set("blocked_numbers")

    /** Risky-call window: drives Panic Pause and the Call-Back Alert. */
    var lastRiskyAt by long("last_risky_at")
    var lastRiskyNumber by str("last_risky_number")
    var lastRiskyClaimedId by str("last_risky_claimed")
    var lastRiskyScore by str("last_risky_score")

    val registered get() = userId != null && !token.isNullOrBlank()

    fun markRisky(number: String?, score: Int, claimedId: String?) {
        com.voiceguard.app.service.AppStats.inc("danger_results")
        lastRiskyAt = System.currentTimeMillis()
        lastRiskyNumber = number
        lastRiskyScore = score.toString()
        lastRiskyClaimedId = claimedId
    }

    fun inRiskyWindow(minutes: Int = 15) =
        System.currentTimeMillis() - lastRiskyAt < minutes * 60_000L
}
