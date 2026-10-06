package com.voiceguard.app.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import com.voiceguard.app.R
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.ui.MainActivity
import com.voiceguard.app.ui.PanicPauseActivity

object Notify {
    const val CH_GUARD = "guard"
    const val CH_ALERTS = "alerts"
    const val CH_URGENT = "urgent"
    const val CH_SPAM = "spam"
    const val ID_GUARD = 1
    const val ID_VERIFY = 10
    const val ID_HD = 11
    const val ID_PANIC = 12
    const val ID_CALLBACK = 13
    private var nextId = 100

    fun createChannels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH_GUARD, "Protection active", NotificationManager.IMPORTANCE_MIN))
        nm.createNotificationChannel(NotificationChannel(CH_ALERTS, "Family alerts", NotificationManager.IMPORTANCE_HIGH))
        nm.createNotificationChannel(NotificationChannel(CH_URGENT, "Urgent: verify & HD calls", NotificationManager.IMPORTANCE_HIGH))
        nm.createNotificationChannel(NotificationChannel(CH_SPAM, "Spam warnings", NotificationManager.IMPORTANCE_HIGH))
        com.voiceguard.app.telecom.CallNotifier.createChannels(ctx)
    }

    private fun open(ctx: Context, nav: String, extras: Map<String, String?> = emptyMap(), req: Int = nav.hashCode()): PendingIntent {
        val i = Intent(ctx, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra("nav", nav)
            extras.forEach { (k, v) -> putExtra(k, v) }
        }
        return PendingIntent.getActivity(ctx, req, i, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    private fun call(ctx: Context, number: String, req: Int): PendingIntent =
        PendingIntent.getActivity(ctx, req, Intent(Intent.ACTION_CALL, Uri.parse("tel:$number"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE)

    private fun nm(ctx: Context) = ctx.getSystemService(NotificationManager::class.java)

    fun guard(ctx: Context): Notification = Notification.Builder(ctx, CH_GUARD)
        .setSmallIcon(R.drawable.ic_shield).setContentTitle("VoiceGuard is protecting your calls")
        .setContentText("Family link active").setOngoing(true)
        .setContentIntent(open(ctx, "home")).build()

    /** "Are You Really Calling?" arrives on the claimed person's phone. */
    fun verifyRequest(ctx: Context, requestId: String, fromName: String, number: String?) {
        val extras = mapOf("rid" to requestId, "from" to fromName, "number" to number)
        val n = Notification.Builder(ctx, CH_URGENT).setSmallIcon(R.drawable.ic_shield)
            .setContentTitle("$fromName asks: are you calling them?")
            .setContentText("A caller ${Numbers.pretty(number)} claims to be you. Tap to answer.")
            .setCategory(Notification.CATEGORY_CALL).setAutoCancel(true)
            .setFullScreenIntent(open(ctx, "verify", extras, 7001), true)
            .setContentIntent(open(ctx, "verify", extras, 7001))
            .addAction(Notification.Action.Builder(null, "Yes, it's me", answer(ctx, requestId, "yes")).build())
            .addAction(Notification.Action.Builder(null, "NO, not me", answer(ctx, requestId, "no")).build())
            .build()
        nm(ctx).notify(ID_VERIFY, n)
    }

    private fun answer(ctx: Context, rid: String, ans: String): PendingIntent =
        PendingIntent.getBroadcast(ctx, (rid + ans).hashCode(),
            Intent(ctx, ActionReceiver::class.java).setAction(ActionReceiver.VERIFY).putExtra("rid", rid).putExtra("answer", ans),
            PendingIntent.FLAG_IMMUTABLE)

    fun hdIncoming(ctx: Context, callId: String, fromName: String) {
        val extras = mapOf("call_id" to callId, "from" to fromName, "incoming" to "1")
        val n = Notification.Builder(ctx, CH_URGENT).setSmallIcon(R.drawable.ic_shield)
            .setContentTitle("VoiceGuard HD call from $fromName")
            .setContentText("Verified family call - tap to answer").setCategory(Notification.CATEGORY_CALL)
            .setAutoCancel(true).setFullScreenIntent(open(ctx, "hd", extras, 7002), true)
            .setContentIntent(open(ctx, "hd", extras, 7002)).build()
        nm(ctx).notify(ID_HD, n)
    }

    fun cancel(ctx: Context, id: Int) = nm(ctx).cancel(id)

    fun alert(ctx: Context, title: String, body: String, callNumber: String? = null, callLabel: String? = null) {
        val b = Notification.Builder(ctx, CH_ALERTS).setSmallIcon(R.drawable.ic_shield)
            .setContentTitle(title).setContentText(body).setStyle(Notification.BigTextStyle().bigText(body))
            .setAutoCancel(true).setContentIntent(open(ctx, "alerts"))
        if (callNumber != null) b.addAction(Notification.Action.Builder(null, callLabel ?: "Call", call(ctx, callNumber, callNumber.hashCode())).build())
        nm(ctx).notify(nextId++, b.build())
    }

    fun spamWarning(ctx: Context, number: String, text: String) {
        val n = Notification.Builder(ctx, CH_SPAM).setSmallIcon(R.drawable.ic_shield)
            .setContentTitle("⚠ Spam warning: ${Numbers.pretty(number)}").setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text)).setAutoCancel(true)
            .setContentIntent(open(ctx, "number", mapOf("number" to number), number.hashCode())).build()
        nm(ctx).notify(number.hashCode(), n)
    }

    /** Call-Back Alert (feature 18): after a suspicious call, call the real person on their saved number. */
    fun callBack(ctx: Context, name: String, phone: String) {
        val n = Notification.Builder(ctx, CH_ALERTS).setSmallIcon(R.drawable.ic_shield)
            .setContentTitle("Call $name back on their saved number")
            .setContentText("That call claimed to be $name. Check with them directly before doing anything.")
            .setAutoCancel(true).setContentIntent(open(ctx, "home"))
            .addAction(Notification.Action.Builder(null, "Call $name", call(ctx, phone, 7003)).build())
            .build()
        nm(ctx).notify(ID_CALLBACK, n)
    }

    fun panic(ctx: Context, appName: String) {
        val pi = PendingIntent.getActivity(ctx, 7004, Intent(ctx, PanicPauseActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).putExtra("app", appName), PendingIntent.FLAG_IMMUTABLE)
        val n = Notification.Builder(ctx, CH_URGENT).setSmallIcon(R.drawable.ic_shield)
            .setContentTitle("PAUSE before you pay").setContentText("You just had a risky call. Wait and talk to family first.")
            .setCategory(Notification.CATEGORY_ALARM).setFullScreenIntent(pi, true).setContentIntent(pi).setAutoCancel(true).build()
        nm(ctx).notify(ID_PANIC, n)
    }
}
