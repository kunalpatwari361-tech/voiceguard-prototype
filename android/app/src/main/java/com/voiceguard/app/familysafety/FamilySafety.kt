package com.voiceguard.app.familysafety

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.location.Location
import android.os.BatteryManager
import androidx.core.content.ContextCompat
import com.voiceguard.app.R
import com.voiceguard.app.VgApp
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.ApiException
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.json
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.str
import com.voiceguard.app.ui.MainActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import java.util.UUID

/**
 * Family Safety, this phone's side (server: /api/family-safety, docs/family-safety.md).
 * The server decides who may see what; this object only remembers the member's own switches, sends SOS,
 * confirms received safety alerts and starts / stops the sharing service.
 */
object FamilySafety {
    const val BASE = "/api/family-safety"
    const val CH = "family_safety"
    const val ID_SHARING = 40
    const val ID_INFO = 41
    const val NAV = "familysafety"

    private val prefs by lazy { VgApp.instance.getSharedPreferences("vg_family_safety", Context.MODE_PRIVATE) }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    val status = MutableStateFlow<JsonObject?>(null)

    /** The member switched sharing on in this app (the server stays the authority: 409 switches it off here). */
    var wantSharing: Boolean
        get() = prefs.getBoolean("want_sharing", false)
        set(v) = prefs.edit().putBoolean("want_sharing", v).apply()

    var shareBattery: Boolean
        get() = prefs.getBoolean("share_battery", false)
        set(v) = prefs.edit().putBoolean("share_battery", v).apply()

    suspend fun refresh(): JsonObject? = runCatching { Api.get("$BASE/status").asObj() }.getOrNull()?.also {
        status.value = it
        shareBattery = it.obj("scope")?.str("battery") == "true"
    }

    fun enabledOnServer(): Boolean = status.value?.str("enabled") == "true"

    /** Work that must finish even when the screen that asked for it is gone. */
    fun background(block: suspend () -> Unit) {
        scope.launch { runCatching { block() } }
    }

    // ------------------------------------------------------------------ sharing service
    fun startSharing(ctx: Context) {
        wantSharing = true
        ContextCompat.startForegroundService(ctx, Intent(ctx, FamilySafetyService::class.java))
    }

    /** Stops sending on this phone (the server call that ends consent / pauses is made by the caller). */
    fun stopService(ctx: Context) {
        ctx.stopService(Intent(ctx, FamilySafetyService::class.java))
        nm(ctx).cancel(ID_SHARING)
    }

    suspend fun pause(ctx: Context, minutes: Int?) {
        Api.post("$BASE/sharing/pause", json("minutes" to minutes))
        stopService(ctx)
        info(ctx, "Location sharing paused", if (minutes != null) "Paused for ${minutes / 60} h. Open VoiceGuard to resume."
            else "Paused. Open VoiceGuard to resume.")
    }

    suspend fun stopSharing(ctx: Context) {
        wantSharing = false
        stopService(ctx)
        Api.post("$BASE/consent/revoke")
    }

    // ------------------------------------------------------------------ SOS
    /** One idempotency key per SOS press, kept until the server answers, so retries never send a second SOS. */
    suspend fun sos(message: String, loc: Location?): JsonObject {
        val key = prefs.getString("sos_key", null) ?: UUID.randomUUID().toString().also { prefs.edit().putString("sos_key", it).apply() }
        val body = json("client_key" to key, "message" to message, "lat" to loc?.latitude, "lon" to loc?.longitude,
            "accuracy" to loc?.accuracy, "recorded_at" to loc?.time)
        var last: Exception? = null
        repeat(3) { attempt ->
            try {
                val r = Api.post("$BASE/sos", body).asObj() ?: JsonObject(emptyMap())
                prefs.edit().remove("sos_key").apply()
                return r
            } catch (e: ApiException) {
                if (e.code in 400..499) { prefs.edit().remove("sos_key").apply(); throw e }
                last = e
            } catch (e: Exception) {
                last = e
            }
            delay(1500L * (attempt + 1))
        }
        throw last ?: ApiException("SOS could not be sent")
    }

    // ------------------------------------------------------------------ what the server pushes (called from Inbox)
    /** A safety alert reached this phone: tell the server it arrived (stops SOS retries for this phone). */
    fun onAlert(a: JsonObject?) {
        val id = a?.obj("payload")?.str("safety_event_id") ?: return
        scope.launch { runCatching { Api.post("$BASE/events/$id/ack") } }
    }

    fun onEvent(ctx: Context, e: JsonObject) {
        when (e.str("type")) {
            "family_safety_removed" -> {
                wantSharing = false
                stopService(ctx)
                info(ctx, "You were removed from ${e.str("family_name") ?: "the family circle"}", "Location sharing has stopped.")
            }
            "family_safety_event" -> e.obj("event")?.let { ev ->
                if (ev.str("kind") == "sos") info(ctx, "SOS ${ev.str("status")}", "Marked ${ev.str("status")} by ${ev.str("closed_by") ?: "family"}.")
            }
        }
    }

    // ------------------------------------------------------------------ battery and notifications
    /** Battery percent and charging, or (null, null) when the phone does not say (unknown, never "full"). */
    fun battery(ctx: Context): Pair<Int?, Boolean?> {
        val i = ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED)) ?: return null to null
        val pct = FamilySafetyLogic.batteryPercent(i.getIntExtra(BatteryManager.EXTRA_LEVEL, -1), i.getIntExtra(BatteryManager.EXTRA_SCALE, -1))
        val st = i.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
        val charging = when (st) {
            BatteryManager.BATTERY_STATUS_CHARGING, BatteryManager.BATTERY_STATUS_FULL -> true
            BatteryManager.BATTERY_STATUS_DISCHARGING, BatteryManager.BATTERY_STATUS_NOT_CHARGING -> false
            else -> null
        }
        return pct to charging
    }

    fun nm(ctx: Context): NotificationManager = ctx.getSystemService(NotificationManager::class.java)

    fun channel(ctx: Context) {
        nm(ctx).createNotificationChannel(NotificationChannel(CH, "Family location sharing", NotificationManager.IMPORTANCE_LOW).apply {
            description = "Shown all the time while your family circle can see your location"
        })
    }

    fun openApp(ctx: Context, req: Int = 7040): PendingIntent = PendingIntent.getActivity(ctx, req,
        Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP).putExtra("nav", NAV),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    fun info(ctx: Context, title: String, text: String) {
        channel(ctx)
        nm(ctx).notify(ID_INFO, Notification.Builder(ctx, CH).setSmallIcon(R.drawable.ic_shield).setContentTitle(title)
            .setContentText(text).setStyle(Notification.BigTextStyle().bigText(text)).setAutoCancel(true)
            .setContentIntent(openApp(ctx)).build())
    }
}
