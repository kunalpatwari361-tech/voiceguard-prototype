package com.voiceguard.app.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.voiceguard.app.data.Reply
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/** Handles notification buttons without opening the app (e.g. "NO, not me"). */
class ActionReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action == VERIFY) {
            Notify.cancel(ctx, Notify.ID_VERIFY)
            val done = goAsync()     // works even if the app was closed and the live link is down
            CoroutineScope(Dispatchers.IO).launch {
                try {
                    Reply.verify(intent.getStringExtra("rid"), intent.getStringExtra("answer"))
                } finally {
                    done.finish()
                }
            }
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
