package com.voiceguard.app.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.voiceguard.app.data.Live
import com.voiceguard.app.data.json

/** Handles notification buttons without opening the app (e.g. "NO, not me"). */
class ActionReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action == VERIFY) {
            Live.send(json("type" to "verify_answer", "request_id" to intent.getStringExtra("rid"),
                "answer" to intent.getStringExtra("answer")))
            Notify.cancel(ctx, Notify.ID_VERIFY)
        }
    }

    companion object {
        const val VERIFY = "com.voiceguard.app.VERIFY_ANSWER"
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) GuardService.start(ctx)
    }
}
