package com.voiceguard.app.telecom

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.telecom.Call
import com.voiceguard.app.R
import com.voiceguard.app.data.Contacts
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.str
import com.voiceguard.app.ui.InCallActivity

/**
 * The phone-call notification of the VoiceGuard Dialer: Answer / Decline while ringing,
 * Hang up / Speaker / Mute with a running timer during the call. Works from the lock screen,
 * the notification shade, or while another app is open.
 */
object CallNotifier {
    const val ID = 20
    /** True while the VoiceGuard call screen is in front: then the ringing notification must not pop up over it. */
    @Volatile var uiVisible = false
    private const val CH_RING = "call_ringing"      // high importance + full screen; Telecom plays the ringtone
    private const val CH_ONGOING = "call_ongoing"   // silent, stays in the status bar during the call

    fun createChannels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH_RING, "Incoming calls", NotificationManager.IMPORTANCE_HIGH).apply {
            setSound(null, null); enableVibration(false)
        })
        nm.createNotificationChannel(NotificationChannel(CH_ONGOING, "Ongoing call", NotificationManager.IMPORTANCE_LOW))
    }

    private fun who(ctx: Context, number: String): String =
        Sync.memberByPhone(number)?.str("name") ?: Contacts.nameFor(ctx, number) ?: Numbers.pretty(number)

    private fun action(ctx: Context, act: String, req: Int): PendingIntent =
        PendingIntent.getBroadcast(ctx, req, Intent(ctx, CallActionReceiver::class.java).setAction(act), PendingIntent.FLAG_IMMUTABLE)

    private fun openUi(ctx: Context, extra: String? = null, req: Int = 0): PendingIntent =
        PendingIntent.getActivity(ctx, req, Intent(ctx, InCallActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).apply { if (extra != null) putExtra("action", extra) },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    fun update(ctx: Context) {
        val call = CallManager.call.value ?: return cancel(ctx)
        val state = call.state
        if (state == Call.STATE_DISCONNECTED) return cancel(ctx)
        val number = CallManager.number
        val name = who(ctx, number)
        val scam = number in Prefs.scamNumbers
        val ringing = state == Call.STATE_RINGING
        val b = Notification.Builder(ctx, if (ringing && !uiVisible) CH_RING else CH_ONGOING)
            .setSmallIcon(R.drawable.ic_shield)
            .setContentTitle(if (scam) "⚠ $name · reported scam" else name)
            .setCategory(Notification.CATEGORY_CALL)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setColor(if (scam) 0xFFEF4444.toInt() else 0xFF22C55E.toInt())
            .setContentIntent(openUi(ctx))
        when (state) {
            Call.STATE_RINGING -> {
                b.setContentText("Incoming call · protected by VoiceGuard")
                if (!uiVisible) b.setFullScreenIntent(openUi(ctx, req = 1), true)
                b
                    .addAction(Notification.Action.Builder(null, "Decline", action(ctx, CallActionReceiver.HANGUP, 11)).build())
                    .addAction(Notification.Action.Builder(null, "Answer", openUi(ctx, "answer", 12)).build())
            }
            Call.STATE_SELECT_PHONE_ACCOUNT -> b.setContentText("Choose a SIM to call")
                .addAction(Notification.Action.Builder(null, "Cancel", action(ctx, CallActionReceiver.HANGUP, 11)).build())
            Call.STATE_DIALING, Call.STATE_CONNECTING, Call.STATE_NEW -> b.setContentText("Calling…")
                .addAction(Notification.Action.Builder(null, "Hang up", action(ctx, CallActionReceiver.HANGUP, 11)).build())
            else -> {
                val risk = CallManager.liveRisk.value
                b.setContentText(if (state == Call.STATE_HOLDING) "On hold" else "Ongoing call" + (risk?.let { " · $it" } ?: ""))
                val since = CallManager.connectedAt.value
                if (since > 0) b.setUsesChronometer(true).setWhen(since).setShowWhen(true)
                b.addAction(Notification.Action.Builder(null, "Hang up", action(ctx, CallActionReceiver.HANGUP, 11)).build())
                b.addAction(Notification.Action.Builder(null, if (CallManager.speaker.value) "Speaker off" else "Speaker",
                    action(ctx, CallActionReceiver.SPEAKER, 13)).build())
                b.addAction(Notification.Action.Builder(null, if (CallManager.muted.value) "Unmute" else "Mute",
                    action(ctx, CallActionReceiver.MUTE, 14)).build())
            }
        }
        ctx.getSystemService(NotificationManager::class.java).notify(ID, b.build())
    }

    fun cancel(ctx: Context) = ctx.getSystemService(NotificationManager::class.java).cancel(ID)
}

/** Buttons on the call notification. */
class CallActionReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        when (intent.action) {
            HANGUP -> CallManager.hangup()
            SPEAKER -> CallManager.toggleSpeaker()
            MUTE -> CallManager.toggleMute()
        }
        CallNotifier.update(ctx)
    }

    companion object {
        const val HANGUP = "com.voiceguard.app.CALL_HANGUP"
        const val SPEAKER = "com.voiceguard.app.CALL_SPEAKER"
        const val MUTE = "com.voiceguard.app.CALL_MUTE"
    }
}
