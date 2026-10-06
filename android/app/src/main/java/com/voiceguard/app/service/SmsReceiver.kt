package com.voiceguard.app.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony
import android.telephony.TelephonyManager
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.SmsGuard
import com.voiceguard.app.telecom.CallManager
import com.voiceguard.app.ui.tr
import kotlinx.coroutines.flow.MutableStateFlow

/** New SMS arrives → checked on the phone at once (VoiceGuard does not replace the SMS app; it only reads). */
class SmsReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return
        val parts = Telephony.Sms.Intents.getMessagesFromIntent(intent) ?: return
        val from = parts.firstOrNull()?.originatingAddress ?: return
        SmsWatch.onSms(ctx, from, parts.joinToString("") { it.messageBody.orEmpty() })
    }
}

data class CallerSms(val from: String, val body: String, val verdict: SmsGuard.Verdict, val fromCaller: Boolean, val at: Long)

/**
 * Links messages to calls – the classic trick is "I'm sending you a code, read it to me". While a call is on (or
 * right after a risky one) an incoming OTP gets an urgent "never share this" warning, and a message from the
 * caller's own number shows up on the call screen.
 */
object SmsWatch {
    /** Last message that matters for the call screen (from the caller, or an OTP during a call). */
    val forCall = MutableStateFlow<CallerSms?>(null)

    private fun onCall(ctx: Context): Boolean = CallManager.call.value != null || runCatching {
        @Suppress("DEPRECATION")
        ctx.getSystemService(TelephonyManager::class.java).callState != TelephonyManager.CALL_STATE_IDLE
    }.getOrDefault(false)

    fun onSms(ctx: Context, from: String, body: String) {
        val v = SmsGuard.check(body, from)
        val n = Numbers.normalize(from)
        val inCall = onCall(ctx)
        val caller = CallManager.call.value?.let { CallManager.number }
        val fromCaller = caller != null && caller == n ||
            (Prefs.inRiskyWindow(30) && Prefs.lastRiskyNumber?.let { Numbers.normalize(it) } == n)
        if (fromCaller || (inCall && v.isOtp)) forCall.value = CallerSms(from, body, v, fromCaller, System.currentTimeMillis())
        when {
            v.isOtp && (inCall || Prefs.inRiskyWindow(15)) -> Notify.smsWarning(ctx, from,
                tr("NEVER share this OTP with the caller", "यह OTP कॉलर को कभी न बताएं"),
                tr("You got a code while on a call. Banks, police and family never ask you to read an OTP out. If the caller asks for it, hang up.",
                    "कॉल के दौरान कोड आया है। बैंक, पुलिस या परिवार कभी OTP नहीं माँगते। कॉलर माँगे तो कॉल काट दें।"), urgent = true)
            v.level == "danger" -> Notify.smsWarning(ctx, from, tr("⚠ Scam SMS from ", "⚠ स्कैम SMS: ") + Numbers.pretty(from),
                v.reasons.joinToString(" · ") + "\n" + body.take(160))
            fromCaller -> Notify.smsWarning(ctx, from, tr("Message from the caller ", "कॉलर का मैसेज ") + Numbers.pretty(from),
                (if (v.reasons.isNotEmpty()) "⚠ " + v.reasons.joinToString(" · ") + "\n" else "") + body.take(160))
        }
    }
}
