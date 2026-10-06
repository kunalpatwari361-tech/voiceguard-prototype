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

    var hindi by bool("hindi", false)
    var autoCheck by bool("auto_check", true)
    var panicPause by bool("panic_pause", true)
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
        lastRiskyAt = System.currentTimeMillis()
        lastRiskyNumber = number
        lastRiskyScore = score.toString()
        lastRiskyClaimedId = claimedId
    }

    fun inRiskyWindow(minutes: Int = 15) =
        System.currentTimeMillis() - lastRiskyAt < minutes * 60_000L
}
