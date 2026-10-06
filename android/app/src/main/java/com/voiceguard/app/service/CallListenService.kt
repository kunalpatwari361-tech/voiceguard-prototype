package com.voiceguard.app.service

import android.accessibilityservice.AccessibilityService
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.view.accessibility.AccessibilityEvent

/**
 * "VoiceGuard call listening" (Settings → Accessibility). During a phone call Android hands normal apps silence from
 * the microphone; apps with an enabled accessibility service are the exception. That is the only reason this
 * service exists: it reads no screen content and only ever sees events from VoiceGuard itself.
 */
class CallListenService : AccessibilityService() {
    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit
    override fun onInterrupt() = Unit

    companion object {
        fun enabled(ctx: Context): Boolean {
            val list = Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES) ?: return false
            val me = ComponentName(ctx, CallListenService::class.java)
            return list.split(':').any { ComponentName.unflattenFromString(it) == me }
        }

        fun openSettings(ctx: Context) {
            ctx.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }
}
