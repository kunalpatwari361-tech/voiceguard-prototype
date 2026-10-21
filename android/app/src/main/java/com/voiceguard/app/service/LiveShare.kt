package com.voiceguard.app.service

import android.content.Context
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.json
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Live Location, this phone's side: while a family member has the live map open, send a fix every few seconds
 * and keep a notification up that says who is watching, with a "Stop sharing" button. Normal sharing (every
 * 10 minutes, GuardService) resumes when it ends.
 */
object LiveShare {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var job: Job? = null
    @Volatile var until = 0L
        private set

    fun start(ctx: Context, from: String, seconds: Int, intervalS: Int) {
        if (!Prefs.shareLocation || !Loc.granted(ctx)) {
            scope.launch { runCatching { Api.post("/api/location/live/end") } }
            return
        }
        until = System.currentTimeMillis() + seconds * 1000L
        Notify.liveSharing(ctx, from, until)
        job?.cancel()
        job = scope.launch {
            while (isActive && System.currentTimeMillis() < until) {
                runCatching { Loc.send(ctx) }
                delay(intervalS.coerceAtLeast(5) * 1000L)
            }
            finish(ctx)
        }
    }

    fun stop(ctx: Context) {
        job?.cancel()
        finish(ctx)
    }

    /** "Stop sharing" from the notification or Settings. */
    suspend fun endByMe(ctx: Context) {
        stop(ctx)
        runCatching { Api.post("/api/location/live/end", json()) }
    }

    private fun finish(ctx: Context) {
        job = null
        until = 0L
        Notify.cancel(ctx, Notify.ID_LIVE)
    }
}
