package com.voiceguard.app.telecom

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.telecom.Call
import android.telecom.CallAudioState
import android.telecom.InCallService
import android.telecom.VideoProfile
import com.voiceguard.app.R
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.service.Notify
import com.voiceguard.app.ui.InCallActivity
import kotlinx.coroutines.flow.MutableStateFlow

/** Single source of truth for the current real phone call when VoiceGuard is the default phone app. */
object CallManager {
    val call = MutableStateFlow<Call?>(null)
    val state = MutableStateFlow(Call.STATE_DISCONNECTED)
    val muted = MutableStateFlow(false)
    val speaker = MutableStateFlow(false)
    val connectedAt = MutableStateFlow(0L)
    var service: VgInCallService? = null

    private val cb = object : Call.Callback() {
        override fun onStateChanged(c: Call, s: Int) {
            state.value = s
            if (s == Call.STATE_ACTIVE && connectedAt.value == 0L) connectedAt.value = System.currentTimeMillis()
        }
    }

    fun added(c: Call) {
        call.value = c
        state.value = c.state
        connectedAt.value = 0L
        c.registerCallback(cb)
    }

    fun removed(c: Call) {
        c.unregisterCallback(cb)
        if (call.value == c) {
            state.value = Call.STATE_DISCONNECTED
            call.value = null
        }
    }

    val number: String get() = Numbers.normalize(call.value?.details?.handle?.schemeSpecificPart.orEmpty())
    val incoming: Boolean get() = call.value?.details?.callDirection == Call.Details.DIRECTION_INCOMING

    fun answer() = call.value?.answer(VideoProfile.STATE_AUDIO_ONLY)
    fun hangup() = call.value?.let { if (it.state == Call.STATE_RINGING) it.reject(false, null) else it.disconnect() }
    fun toggleMute() { muted.value = !muted.value; service?.setMuted(muted.value) }
    fun toggleSpeaker() {
        speaker.value = !speaker.value
        service?.setAudioRoute(if (speaker.value) CallAudioState.ROUTE_SPEAKER else CallAudioState.ROUTE_EARPIECE)
    }
    fun dtmf(c: Char) = call.value?.run { playDtmfTone(c); stopDtmfTone() }
}

/** VoiceGuard Dialer (feature 1): our in-call screen with the scam tools built in. */
class VgInCallService : InCallService() {
    override fun onCallAdded(call: Call) {
        CallManager.service = this
        CallManager.added(call)
        val ui = Intent(this, InCallActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (call.state == Call.STATE_RINGING) {
            val pi = PendingIntent.getActivity(this, 1, ui, PendingIntent.FLAG_IMMUTABLE)
            val n = Notification.Builder(this, Notify.CH_URGENT).setSmallIcon(R.drawable.ic_shield)
                .setContentTitle("Incoming call").setContentText(Numbers.pretty(CallManager.number))
                .setCategory(Notification.CATEGORY_CALL).setOngoing(true)
                .setFullScreenIntent(pi, true).setContentIntent(pi).build()
            getSystemService(NotificationManager::class.java).notify(RING_ID, n)
        }
        startActivity(ui)
    }

    override fun onCallRemoved(call: Call) {
        CallManager.removed(call)
        getSystemService(NotificationManager::class.java).cancel(RING_ID)
    }

    override fun onCallAudioStateChanged(audioState: CallAudioState) {
        CallManager.muted.value = audioState.isMuted
        CallManager.speaker.value = audioState.route == CallAudioState.ROUTE_SPEAKER
    }

    override fun onDestroy() {
        if (CallManager.service == this) CallManager.service = null
        super.onDestroy()
    }

    companion object { const val RING_ID = 20 }
}
