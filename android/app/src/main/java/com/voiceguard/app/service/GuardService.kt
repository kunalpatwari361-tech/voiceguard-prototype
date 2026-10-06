package com.voiceguard.app.service

import android.Manifest
import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Process
import android.provider.Settings
import android.telephony.TelephonyCallback
import android.telephony.TelephonyManager
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Live
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.json
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.str
import com.voiceguard.app.telecom.CallerIdOverlay
import com.voiceguard.app.ui.PanicPauseActivity
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject

/**
 * Always-on protection: keeps the family link open, reports call state (for "Are You Really
 * Calling?"), answers location requests, shows family alerts, and runs Panic Pause.
 */
class GuardService : LifecycleService() {

    override fun onCreate() {
        super.onCreate()
        startFg()
        Live.start()
        lifecycleScope.launch { Live.events.collect { handle(it) } }
        lifecycleScope.launch { Live.connected.collect { if (it) onConnected() } }
        registerCallState()
        lifecycleScope.launch {
            while (true) {
                runCatching { Loc.send(this@GuardService) }
                delay(10 * 60_000L)
            }
        }
        lifecycleScope.launch { panicLoop() }
        lifecycleScope.launch { Sync.lists(); runCatching { Sync.family() } }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        return START_STICKY
    }

    private fun startFg() {
        val base = ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
        val withLoc = if (Loc.granted(this)) base or ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION else base
        try {
            ServiceCompat.startForeground(this, Notify.ID_GUARD, Notify.guard(this), withLoc)
        } catch (e: Exception) {
            ServiceCompat.startForeground(this, Notify.ID_GUARD, Notify.guard(this), base)
        }
    }

    private fun onConnected() {
        CallWatch.current(this)?.let { Live.send(json("type" to "call_state", "state" to it)) }
        lifecycleScope.launch { runCatching { Loc.send(this@GuardService) } }
    }

    private suspend fun handle(e: JsonObject) {
        when (e.str("type")) {
            "verify_request" -> Notify.verifyRequest(this, e.str("request_id")!!, e.obj("from").str("name") ?: "Family",
                e.str("number"))
            "hd_incoming" -> Notify.hdIncoming(this, e.str("call_id")!!, e.obj("from").str("name") ?: "Family")
            "location_request" -> runCatching { Loc.send(this, e.str("request_id")) }
            "scamlist_updated" -> Sync.lists()
            "family_updated" -> runCatching { Sync.family() }
            "alert" -> {
                val a = e.obj("alert")
                val p = a.obj("payload")
                when (a.str("kind")) {
                    "impersonation" -> Notify.alert(this, "⚠ " + (a.str("title") ?: ""), a.str("body") ?: "",
                        p.str("asker_phone"), "Call ${p.str("asker_name") ?: "them"} now")
                    "cyber_cell" -> Notify.alert(this, a.str("title") ?: "", a.str("body") ?: "", "1930", "Call 1930")
                    "scam_call", "panic", "bank_hold" -> {
                        val victim = Sync.member(a.str("from_user_id"))
                        Notify.alert(this, "⚠ " + (a.str("title") ?: ""), a.str("body") ?: "",
                            victim?.str("phone") ?: p.str("victim_phone"), "Call ${victim?.str("name") ?: p.str("from_name") ?: "them"} now")
                    }
                    else -> Notify.alert(this, a.str("title") ?: "VoiceGuard alert", a.str("body") ?: "")
                }
            }
        }
    }

    // ------------------------------------------------------------------ call state
    private fun registerCallState() {
        if (checkSelfPermission(Manifest.permission.READ_PHONE_STATE) != PackageManager.PERMISSION_GRANTED) return
        val tm = getSystemService(TelephonyManager::class.java)
        if (Build.VERSION.SDK_INT >= 31) {
            tm.registerTelephonyCallback(mainExecutor, object : TelephonyCallback(), TelephonyCallback.CallStateListener {
                override fun onCallStateChanged(state: Int) = CallWatch.onState(this@GuardService, state)
            })
        } else {
            @Suppress("DEPRECATION")
            tm.listen(object : android.telephony.PhoneStateListener() {
                @Deprecated("Deprecated in Java")
                override fun onCallStateChanged(state: Int, phoneNumber: String?) = CallWatch.onState(this@GuardService, state)
            }, android.telephony.PhoneStateListener.LISTEN_CALL_STATE)
        }
    }

    // ------------------------------------------------------------------ Panic Pause (feature 19)
    private suspend fun panicLoop() {
        var lastShown = 0L
        while (true) {
            delay(1500)
            if (!Prefs.panicPause || !Prefs.inRiskyWindow(15) || !usageAccess(this)) continue
            val app = foregroundPaymentApp() ?: continue
            if (System.currentTimeMillis() - lastShown < 60_000) continue
            lastShown = System.currentTimeMillis()
            if (Settings.canDrawOverlays(this)) {
                startActivity(Intent(this, PanicPauseActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).putExtra("app", app))
            } else {
                Notify.panic(this, app)
            }
            runCatching {
                Api.post("/api/alerts", json("from_user_id" to Prefs.userId, "kind" to "panic",
                    "title" to "${Prefs.name} opened $app right after a risky call",
                    "body" to "VoiceGuard paused them. Please call ${Prefs.name} now."))
            }
        }
    }

    private fun foregroundPaymentApp(): String? {
        val usm = getSystemService(UsageStatsManager::class.java)
        val now = System.currentTimeMillis()
        val ev = usm.queryEvents(now - 4000, now)
        val e = UsageEvents.Event()
        var found: String? = null
        while (ev.hasNextEvent()) {
            ev.getNextEvent(e)
            if (e.eventType == UsageEvents.Event.ACTIVITY_RESUMED && e.packageName in PAYMENT_APPS) found = e.packageName
        }
        return found?.let { PAYMENT_APPS[it] }
    }

    companion object {
        val PAYMENT_APPS = mapOf(
            "com.google.android.apps.nbu.paisa.user" to "Google Pay",
            "com.phonepe.app" to "PhonePe",
            "net.one97.paytm" to "Paytm",
            "in.org.npci.upiapp" to "BHIM UPI",
            "in.amazon.mShop.android.shopping" to "Amazon Pay",
        )

        fun start(ctx: Context) {
            if (!Prefs.registered) return
            ContextCompat.startForegroundService(ctx, Intent(ctx, GuardService::class.java))
        }

        fun usageAccess(ctx: Context): Boolean {
            val ops = ctx.getSystemService(AppOpsManager::class.java)
            return ops.unsafeCheckOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx.packageName) ==
                AppOpsManager.MODE_ALLOWED
        }
    }
}

/** Tracks the phone's call state; reports it to the family link and fires the Call-Back Alert. */
object CallWatch {
    private var last = TelephonyManager.CALL_STATE_IDLE

    fun name(state: Int) = when (state) {
        TelephonyManager.CALL_STATE_RINGING -> "ringing"
        TelephonyManager.CALL_STATE_OFFHOOK -> "offhook"
        else -> "idle"
    }

    fun current(ctx: Context): String? {
        if (ctx.checkSelfPermission(Manifest.permission.READ_PHONE_STATE) != PackageManager.PERMISSION_GRANTED) return null
        @Suppress("DEPRECATION")
        return name(ctx.getSystemService(TelephonyManager::class.java).callState)
    }

    fun onState(ctx: Context, state: Int) {
        Live.send(json("type" to "call_state", "state" to name(state)))
        if (state != TelephonyManager.CALL_STATE_RINGING) CallerIdOverlay.hide()
        if (last == TelephonyManager.CALL_STATE_OFFHOOK && state == TelephonyManager.CALL_STATE_IDLE) onCallEnded(ctx)
        last = state
    }

    fun onCallEnded(ctx: Context) {
        if (!Prefs.inRiskyWindow(3)) return
        val claimed = Sync.member(Prefs.lastRiskyClaimedId) ?: return
        Notify.callBack(ctx, claimed.str("name") ?: "them", claimed.str("phone") ?: return)
    }
}
