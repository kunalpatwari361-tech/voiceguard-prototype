package com.voiceguard.app.ui

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import androidx.core.os.BundleCompat
import com.google.android.gms.auth.api.phone.SmsRetriever
import com.google.android.gms.common.api.CommonStatusCodes
import com.google.android.gms.common.api.Status

/**
 * Reads the OTP from the SMS (SMS User Consent API). When the code SMS arrives, Android asks
 * "Allow VoiceGuard to read this message?"; on Allow the 6-digit code is passed to [onCode].
 * No SMS permission: the app sees only that one message, and only after the user taps Allow.
 * [round] restarts listening (e.g. after "Resend code"); Android listens for 5 minutes each time.
 */
@Composable
fun SmsCodeListener(round: Int, onCode: (String) -> Unit) {
    val ctx = LocalContext.current
    val latest = rememberUpdatedState(onCode)
    val consent = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
        if (res.resultCode == Activity.RESULT_OK) {
            val sms = res.data?.getStringExtra(SmsRetriever.EXTRA_SMS_MESSAGE).orEmpty()
            Regex("(?<!\\d)\\d{6}(?!\\d)").find(sms)?.value?.let { latest.value(it) }
        }
    }
    DisposableEffect(round) {
        if (round <= 0) return@DisposableEffect onDispose {}
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context, intent: Intent) {
                if (intent.action != SmsRetriever.SMS_RETRIEVED_ACTION) return
                val extras = intent.extras ?: return
                val status = BundleCompat.getParcelable(extras, SmsRetriever.EXTRA_STATUS, Status::class.java) ?: return
                if (status.statusCode != CommonStatusCodes.SUCCESS) return       // TIMEOUT after 5 minutes
                val ask = BundleCompat.getParcelable(extras, SmsRetriever.EXTRA_CONSENT_INTENT, Intent::class.java) ?: return
                // Only ever open Google Play services' own consent screen, never an arbitrary intent.
                val grants = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                if (ask.resolveActivity(c.packageManager)?.packageName == "com.google.android.gms" && ask.flags and grants == 0) {
                    runCatching { consent.launch(ask) }
                }
            }
        }
        ContextCompat.registerReceiver(ctx, receiver, IntentFilter(SmsRetriever.SMS_RETRIEVED_ACTION),
            SmsRetriever.SEND_PERMISSION, null, ContextCompat.RECEIVER_EXPORTED)
        SmsRetriever.getClient(ctx).startSmsUserConsent(null)   // null = the code may come from any sender
        onDispose { runCatching { ctx.unregisterReceiver(receiver) } }
    }
}
