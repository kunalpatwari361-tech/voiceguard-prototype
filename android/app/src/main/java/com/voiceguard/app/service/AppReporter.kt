package com.voiceguard.app.service

import android.Manifest
import android.annotation.SuppressLint
import android.app.KeyguardManager
import android.app.NotificationManager
import android.app.role.RoleManager
import android.content.Context
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.Debug
import android.os.PowerManager
import android.provider.Settings
import android.telephony.SubscriptionManager
import android.telephony.TelephonyManager
import com.voiceguard.app.VgApp
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.AppIdentity
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.json
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID

/**
 * Activity counts and app crashes kept on the phone until the next report. Counts only - never numbers, contacts,
 * messages or recordings.
 */
object AppStats {
    private val prefs by lazy { VgApp.instance.getSharedPreferences("vg_stats", Context.MODE_PRIVATE) }

    val installId: String
        get() = prefs.getString("install_id", null) ?: UUID.randomUUID().toString().also { prefs.edit().putString("install_id", it).apply() }

    @Synchronized
    fun inc(key: String, by: Int = 1) {
        runCatching { prefs.edit().putInt("c_$key", prefs.getInt("c_$key", 0) + by).apply() }
    }

    @Synchronized
    fun takeCounts(): Map<String, Int> {
        val out = prefs.all.filterKeys { it.startsWith("c_") }.mapNotNull { (k, v) -> (v as? Int)?.let { k.removePrefix("c_") to it } }.toMap()
        prefs.edit().apply { out.keys.forEach { remove("c_$it") } }.apply()
        return out
    }

    fun putBack(counts: Map<String, Int>) = counts.forEach { (k, v) -> inc(k, v) }

    /** Saves an app crash (error type, message, where in the code) and lets Android handle it as before. */
    fun installCrashHandler() {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { t, e ->
            runCatching {
                val stack = e.stackTrace.take(15).joinToString("\n") { "${it.className}.${it.methodName}(${it.fileName}:${it.lineNumber})" }
                val arr = runCatching { JSONArray(prefs.getString("crashes", "[]")) }.getOrDefault(JSONArray())
                if (arr.length() < 5) arr.put(JSONObject().put("at", System.currentTimeMillis()).put("exception", e.javaClass.name)
                    .put("message", (e.message ?: "").take(300)).put("stack", "[${t.name}]\n$stack"))
                prefs.edit().putString("crashes", arr.toString()).commit()
            }
            previous?.uncaughtException(t, e)
        }
    }

    fun clearCrashes() = prefs.edit().remove("crashes").apply()

    fun peekCrashes(): List<Map<String, Any?>> {
        val arr = runCatching { JSONArray(prefs.getString("crashes", "[]")) }.getOrDefault(JSONArray())
        return (0 until arr.length()).map { i -> arr.getJSONObject(i).let { o -> o.keys().asSequence().associateWith { o.get(it) } } }
    }
}

/** Sends the phone's status, protection, security signals, counts, crashes and mobile network to the server
 * (Admin Console, Team Portal). */
object AppReporter {
    @Volatile private var lastSent = 0L

    private fun granted(ctx: Context, p: String) = ctx.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED

    fun protection(ctx: Context): Map<String, Boolean> {
        val roles = ctx.getSystemService(RoleManager::class.java)
        val notifications = if (Build.VERSION.SDK_INT >= 33) granted(ctx, Manifest.permission.POST_NOTIFICATIONS)
            else ctx.getSystemService(NotificationManager::class.java).areNotificationsEnabled()
        val access = Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES).orEmpty()
        return mapOf(
            "call_screening" to roles.isRoleHeld(RoleManager.ROLE_CALL_SCREENING),
            "default_dialer" to roles.isRoleHeld(RoleManager.ROLE_DIALER),
            "microphone" to granted(ctx, Manifest.permission.RECORD_AUDIO),
            "phone" to granted(ctx, Manifest.permission.READ_PHONE_STATE),
            "contacts" to granted(ctx, Manifest.permission.READ_CONTACTS),
            "sms" to granted(ctx, Manifest.permission.RECEIVE_SMS),
            "location" to (granted(ctx, Manifest.permission.ACCESS_FINE_LOCATION) || granted(ctx, Manifest.permission.ACCESS_COARSE_LOCATION)),
            "notifications" to notifications,
            "overlay" to Settings.canDrawOverlays(ctx),
            "accessibility" to access.contains(ctx.packageName),
            "battery_unrestricted" to ctx.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(ctx.packageName),
        )
    }

    fun security(ctx: Context): Map<String, Boolean> {
        val su = listOf("/system/bin/su", "/system/xbin/su", "/sbin/su", "/su/bin/su", "/data/local/xbin/su", "/data/local/bin/su")
        val fp = Build.FINGERPRINT.lowercase()
        return mapOf(
            "rooted" to (su.any { File(it).exists() } || Build.TAGS?.contains("test-keys") == true),
            "debugger_attached" to Debug.isDebuggerConnected(),
            "debuggable" to ((ctx.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0),
            "emulator" to (fp.startsWith("generic") || "emulator" in fp || Build.HARDWARE in setOf("goldfish", "ranchu") || "sdk_gphone" in Build.MODEL.lowercase()),
            "adb_enabled" to (Settings.Global.getInt(ctx.contentResolver, Settings.Global.ADB_ENABLED, 0) == 1),
            "screen_lock" to ctx.getSystemService(KeyguardManager::class.java).isDeviceSecure,
        )
    }

    /** The mobile network (Team Portal: Network page). Operator names, network type and signal bars only - no numbers. */
    @SuppressLint("MissingPermission")
    fun network(ctx: Context): Map<String, Any?> {
        val tm = ctx.getSystemService(TelephonyManager::class.java)
        val cm = ctx.getSystemService(ConnectivityManager::class.java)
        val caps = runCatching { cm.getNetworkCapabilities(cm.activeNetwork) }.getOrNull()
        val transport = when {
            caps == null -> "none"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
            else -> "other"
        }
        val phone = granted(ctx, Manifest.permission.READ_PHONE_STATE)
        val dataSub = SubscriptionManager.getDefaultDataSubscriptionId()
        val sims = if (!phone) emptyList() else runCatching {
            ctx.getSystemService(SubscriptionManager::class.java).activeSubscriptionInfoList.orEmpty().map { s ->
                mapOf("carrier" to s.carrierName?.toString(), "mcc_mnc" to s.mccString.orEmpty() + s.mncString.orEmpty(),
                    "slot" to s.simSlotIndex, "data" to (s.subscriptionId == dataSub))
            }
        }.getOrDefault(emptyList())
        return mapOf(
            "operator" to runCatching { tm.networkOperatorName }.getOrNull(),
            "mcc_mnc" to runCatching { tm.networkOperator }.getOrNull(),
            "sims" to sims, "transport" to transport,
            "gen" to if (phone) runCatching { generation(tm.dataNetworkType) }.getOrNull() else null,
            "signal" to runCatching { tm.signalStrength?.level }.getOrNull(),
            "roaming" to runCatching { tm.isNetworkRoaming }.getOrDefault(false),
            "country" to runCatching { tm.networkCountryIso }.getOrNull(),
            "downlink_kbps" to caps?.linkDownstreamBandwidthKbps,
        )
    }

    private fun generation(type: Int): String? = when (type) {
        TelephonyManager.NETWORK_TYPE_NR -> "5G"
        TelephonyManager.NETWORK_TYPE_LTE, TelephonyManager.NETWORK_TYPE_IWLAN -> "4G"
        TelephonyManager.NETWORK_TYPE_UMTS, TelephonyManager.NETWORK_TYPE_HSDPA, TelephonyManager.NETWORK_TYPE_HSUPA,
        TelephonyManager.NETWORK_TYPE_HSPA, TelephonyManager.NETWORK_TYPE_HSPAP, TelephonyManager.NETWORK_TYPE_EVDO_0,
        TelephonyManager.NETWORK_TYPE_EVDO_A, TelephonyManager.NETWORK_TYPE_EVDO_B, TelephonyManager.NETWORK_TYPE_EHRPD,
        TelephonyManager.NETWORK_TYPE_TD_SCDMA -> "3G"
        TelephonyManager.NETWORK_TYPE_GPRS, TelephonyManager.NETWORK_TYPE_EDGE, TelephonyManager.NETWORK_TYPE_CDMA,
        TelephonyManager.NETWORK_TYPE_1xRTT, TelephonyManager.NETWORK_TYPE_IDEN, TelephonyManager.NETWORK_TYPE_GSM -> "2G"
        else -> null
    }

    /** At most every 10 minutes unless [force]. Counts and crashes are put back if the server cannot be reached. */
    suspend fun send(ctx: Context, force: Boolean = false) {
        if (!Prefs.registered || (!force && System.currentTimeMillis() - lastSent < 10 * 60_000L)) return
        val counts = AppStats.takeCounts()
        val crashes = AppStats.peekCrashes()
        val id = AppIdentity.header.split(";")
        val body = json(
            "install_id" to AppStats.installId, "app_version" to id.getOrNull(0), "cert" to id.getOrNull(1),
            "model" to "${Build.MANUFACTURER} ${Build.MODEL}", "android" to "${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})",
            "security" to runCatching { security(ctx) }.getOrDefault(emptyMap()),
            "protection" to runCatching { protection(ctx) }.getOrDefault(emptyMap()),
            "settings" to mapOf("auto_block" to Prefs.autoBlockScam, "floating_card" to Prefs.floatingCard, "share_location" to Prefs.shareLocation,
                "caller_id" to Prefs.callerIdEnabled, "panic_pause" to Prefs.panicPause, "auto_check" to Prefs.autoCheck,
                "language" to Prefs.lang, "theme" to (Prefs.theme ?: "system")),
            "counters" to counts, "crashes" to crashes,
            "network" to runCatching { network(ctx) }.getOrDefault(emptyMap()),
        )
        runCatching { Api.post("/api/app/report", body) }
            .onSuccess { lastSent = System.currentTimeMillis(); AppStats.clearCrashes() }
            .onFailure { AppStats.putBack(counts) }   // nothing is lost: counts and crashes go with the next report
    }
}
