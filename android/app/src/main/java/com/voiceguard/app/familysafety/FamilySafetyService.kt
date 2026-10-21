package com.voiceguard.app.familysafety

import android.annotation.SuppressLint
import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import android.os.IBinder
import android.os.Looper
import androidx.core.app.ServiceCompat
import com.voiceguard.app.R
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.ApiException
import com.voiceguard.app.data.json
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.str
import com.voiceguard.app.service.Loc
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Continuous Family Safety sharing. A foreground service of type "location", started only from the app while the
 * member is using it (so Android's while-in-use location rules apply and no background-location permission is
 * needed). Its notification is always visible, with Pause and Stop. It stops by itself when the location
 * permission is removed or the server says sharing is off. It is not restarted automatically after a reboot or
 * when Android ends the app - the member reopens VoiceGuard (FamilySafetyReceiver reminds them after a reboot).
 */
class FamilySafetyService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var lastSent: Location? = null
    private var lastSentAt: Long? = null
    private val listener = LocationListener { l -> scope.launch { maybeSend(l) } }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        FamilySafety.channel(this)
        if (!Loc.granted(this)) {   // permission removed in Android settings: stop, never work around it
            FamilySafety.info(this, "Location sharing stopped", "VoiceGuard no longer has location permission.")
            stopSelf()
            return START_NOT_STICKY
        }
        try {
            ServiceCompat.startForeground(this, FamilySafety.ID_SHARING, notification(),
                if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION else 0)
        } catch (e: Exception) {   // Android refused (e.g. started from the background): do not track
            FamilySafety.info(this, "Location sharing could not start", "Open VoiceGuard and tap Resume.")
            stopSelf()
            return START_NOT_STICKY
        }
        requestUpdates()
        scope.launch {   // heartbeat: one fix at least every 5 minutes, even when not moving
            while (isActive) {
                delay(FamilySafetyLogic.HEARTBEAT_MS)
                Loc.current(this@FamilySafetyService)?.let { maybeSend(it) }
            }
        }
        scope.launch { Loc.current(this@FamilySafetyService)?.let { maybeSend(it) } }
        return START_NOT_STICKY
    }

    @SuppressLint("MissingPermission")
    private fun requestUpdates() {
        val lm = getSystemService(LocationManager::class.java)
        val provider = when {
            Build.VERSION.SDK_INT >= 31 && lm.hasProvider(LocationManager.FUSED_PROVIDER) -> LocationManager.FUSED_PROVIDER
            lm.isProviderEnabled(LocationManager.GPS_PROVIDER) -> LocationManager.GPS_PROVIDER
            else -> LocationManager.NETWORK_PROVIDER
        }
        runCatching {
            lm.requestLocationUpdates(provider, FamilySafetyLogic.MOVE_INTERVAL_MS, FamilySafetyLogic.MIN_DISTANCE_M, listener, Looper.getMainLooper())
        }
    }

    private suspend fun maybeSend(l: Location) {
        val now = System.currentTimeMillis()
        if (!FamilySafetyLogic.shouldSend(lastSentAt, now, lastSent?.distanceTo(l))) return
        if (!Loc.granted(this)) { stopSelf(); return }
        val (pct, charging) = if (FamilySafety.shareBattery) FamilySafety.battery(this) else null to null
        try {
            Api.post("${FamilySafety.BASE}/location", json("lat" to l.latitude, "lon" to l.longitude, "accuracy" to l.accuracy,
                "recorded_at" to l.time, "battery" to pct, "charging" to charging))
            lastSent = l
            lastSentAt = now
        } catch (e: ApiException) {
            if (e.code == 409 || e.code == 403 || e.code == 404) {   // sharing off / not in the circle / feature off
                FamilySafety.wantSharing = false
                FamilySafety.info(this, "Location sharing is off", "Your family circle can no longer see your location.")
                stopSelf()
            }
        } catch (_: Exception) {
            // no network: try again with the next fix (nothing is stored on the phone)
        }
    }

    private fun notification(): Notification {
        val fam = FamilySafety.status.value?.obj("family")?.str("name") ?: "your family circle"
        fun action(act: String, req: Int) = PendingIntent.getBroadcast(this, req,
            Intent(this, FamilySafetyReceiver::class.java).setAction(act), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, FamilySafety.CH).setSmallIcon(R.drawable.ic_shield)
            .setContentTitle("Sharing your location with $fam")
            .setContentText("Your family circle can see where you are. Pause or stop at any time.")
            .setOngoing(true).setOnlyAlertOnce(true).setContentIntent(FamilySafety.openApp(this))
            .addAction(Notification.Action.Builder(null, "Pause 1 h", action(FamilySafetyReceiver.PAUSE, 7041)).build())
            .addAction(Notification.Action.Builder(null, "Stop sharing", action(FamilySafetyReceiver.STOP, 7042)).build())
            .build()
    }

    override fun onDestroy() {
        runCatching { getSystemService(LocationManager::class.java).removeUpdates(listener) }
        scope.cancel()
        super.onDestroy()
    }
}
