package com.voiceguard.app.service

import android.content.Context
import android.util.Log
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.VgJson
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.bool
import com.voiceguard.app.data.json
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.str
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Firebase Cloud Messaging: lets the server wake this phone when the app is closed (alerts, "Are you really
 * calling?", HD call ringing). The Firebase settings come from our server (/api/push/config), so the same APK
 * works with any Firebase project and nothing changes until the server has its Firebase files.
 */
object Push {
    private const val TAG = "VgPush"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /** Application start: set up Firebase from the settings saved last time (needed when a push starts the app). */
    fun init(ctx: Context): Boolean {
        if (FirebaseApp.getApps(ctx).isNotEmpty()) return true
        val c = Prefs.pushConfig?.let { runCatching { VgJson.parseToJsonElement(it).asObj() }.getOrNull() } ?: return false
        return runCatching {
            FirebaseApp.initializeApp(ctx, FirebaseOptions.Builder()
                .setApplicationId(c.str("app_id")!!)
                .setApiKey(c.str("api_key")!!)
                .setProjectId(c.str("project_id")!!)
                .setGcmSenderId(c.str("sender_id")!!)
                .setStorageBucket(c.str("storage_bucket"))
                .build())
            true
        }.getOrElse { Log.w(TAG, "Firebase set-up failed", it); false }
    }

    /** After sign-in / on every start: fetch the settings, get this phone's push token and register it. */
    suspend fun register(ctx: Context) {
        if (!Prefs.registered) return
        val cfg = runCatching { Api.get("/api/push/config").asObj() }.getOrNull() ?: return
        Prefs.pushStatus = cfg.str("status")
        if (cfg.bool("enabled") != true) return
        val android = cfg.obj("android")?.toString() ?: return
        if (android != Prefs.pushConfig) {
            if (Prefs.pushConfig != null && FirebaseApp.getApps(ctx).isNotEmpty()) Log.i(TAG, "Firebase project changed, used after restart")
            Prefs.pushConfig = android
        }
        if (!init(ctx)) return
        @Suppress("DEPRECATION")   // getToken() still works; newer releases also call onRegistered() below
        val token = runCatching { FirebaseMessaging.getInstance().token.await() }
            .getOrElse { Log.w(TAG, "no push token (Google Play services?)", it); return }
        sendToken(token)
    }

    suspend fun sendToken(token: String) {
        if (!Prefs.registered) return
        runCatching { Api.post("/api/push/register", json("token" to token, "platform" to "android")) }
            .onFailure { Log.w(TAG, "push token not registered", it) }
    }

    fun sendTokenLater(token: String) {
        scope.launch { sendToken(token) }
    }
}

/** Receives the Firebase pushes. Data-only and high priority, so it runs even when the app was swiped away. */
class VgMessagingService : FirebaseMessagingService() {
    @Deprecated("Firebase 25 also reports tokens through onRegistered()")
    override fun onNewToken(token: String) = Push.sendTokenLater(token)

    override fun onRegistered(token: String) = Push.sendTokenLater(token)

    override fun onMessageReceived(message: RemoteMessage) {
        if (!Prefs.registered) return
        val e = message.data["vg"]?.let { runCatching { VgJson.parseToJsonElement(it).asObj() }.getOrNull() } ?: return
        runCatching { GuardService.start(this) }      // bring the live family link back as well
        runBlocking { withTimeoutOrNull(15_000) { Inbox.handle(applicationContext, e) } }
    }
}
