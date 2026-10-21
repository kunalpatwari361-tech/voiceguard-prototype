package com.voiceguard.app.familysafety

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.voiceguard.app.data.Prefs
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/** Pause / Stop from the sharing notification, and a reminder after the phone restarts (no automatic tracking). */
class FamilySafetyReceiver : BroadcastReceiver() {
    companion object {
        const val PAUSE = "com.voiceguard.app.familysafety.PAUSE"
        const val STOP = "com.voiceguard.app.familysafety.STOP"
    }

    override fun onReceive(ctx: Context, intent: Intent) {
        val app = ctx.applicationContext
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED -> if (FamilySafety.wantSharing) {
                // Android does not allow (and we do not want) location tracking to start unseen after a reboot
                FamilySafety.info(app, "Family location sharing is waiting", "Your phone restarted. Open VoiceGuard and tap Resume to share again.")
            }
            PAUSE, STOP -> {
                Prefs.init(app)
                val done = goAsync()
                CoroutineScope(Dispatchers.IO).launch {
                    runCatching {
                        if (intent.action == PAUSE) FamilySafety.pause(app, 60) else {
                            FamilySafety.stopSharing(app)
                            FamilySafety.info(app, "Location sharing stopped", "Your family circle can no longer see your location.")
                        }
                    }.onFailure {
                        FamilySafety.stopService(app)   // even without network: stop sending from this phone now
                        if (intent.action == STOP) FamilySafety.wantSharing = false
                        FamilySafety.info(app, "Stopped on this phone", "VoiceGuard will tell the server when it is back online. Open the app to check.")
                    }
                    done.finish()
                }
            }
        }
    }
}
