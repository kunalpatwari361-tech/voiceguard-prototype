package com.voiceguard.app.telecom

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.telecom.Call
import android.telecom.CallAudioState
import android.telecom.InCallService
import android.telecom.PhoneAccountHandle
import android.telecom.TelecomManager
import android.telecom.VideoProfile
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.ui.InCallActivity
import kotlinx.coroutines.flow.MutableStateFlow

/** Single source of truth for the current real phone call when VoiceGuard is the default phone app. */
object CallManager {
    val call = MutableStateFlow<Call?>(null)
    val state = MutableStateFlow(Call.STATE_DISCONNECTED)
    val muted = MutableStateFlow(false)
    val speaker = MutableStateFlow(false)
    val connectedAt = MutableStateFlow(0L)
    /** "Live risk 92/100" from the live voice check, shown in the call notification. */
    val liveRisk = MutableStateFlow<String?>(null)
    var service: VgInCallService? = null
    private var appCtx: Context? = null

    private val cb = object : Call.Callback() {
        override fun onStateChanged(c: Call, s: Int) {
            state.value = s
            if (s == Call.STATE_ACTIVE && connectedAt.value == 0L) connectedAt.value = System.currentTimeMillis()
            appCtx?.let { CallNotifier.update(it) }
        }
    }

    fun added(ctx: Context, c: Call) {
        appCtx = ctx.applicationContext
        call.value = c
        state.value = c.state
        connectedAt.value = 0L
        liveRisk.value = null
        c.registerCallback(cb)
        CallNotifier.update(ctx)
    }

    fun removed(ctx: Context, c: Call) {
        c.unregisterCallback(cb)
        if (call.value == c) {
            state.value = Call.STATE_DISCONNECTED
            call.value = null
        }
        CallNotifier.cancel(ctx)
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
    fun toggleHold() = call.value?.run { if (state == Call.STATE_HOLDING) unhold() else hold() }

    /** Dual-SIM phones set to "ask every time": the call waits here until a SIM is picked. */
    fun selectSim(h: PhoneAccountHandle) = call.value?.phoneAccountSelected(h, false)

    fun publishRisk(text: String?) {
        if (liveRisk.value == text) return
        liveRisk.value = text
        appCtx?.let { CallNotifier.update(it) }
    }
}

/** The phone's SIM cards that can place calls, with readable names ("Jio", "Airtel", "SIM 2"). */
object Sims {
    @SuppressLint("MissingPermission")
    fun list(ctx: Context): List<Pair<PhoneAccountHandle, String>> {
        val tm = ctx.getSystemService(TelecomManager::class.java)
        val handles = runCatching { tm.callCapablePhoneAccounts }.getOrDefault(emptyList())
        return handles.mapIndexed { i, h ->
            val label = runCatching { tm.getPhoneAccount(h)?.label?.toString() }.getOrNull()?.takeIf { it.isNotBlank() }
            h to ("SIM ${i + 1}" + (label?.let { " · $it" } ?: ""))
        }
    }

    @SuppressLint("MissingPermission")
    fun default(ctx: Context): PhoneAccountHandle? = runCatching {
        ctx.getSystemService(TelecomManager::class.java).getDefaultOutgoingPhoneAccount(android.telecom.PhoneAccount.SCHEME_TEL)
    }.getOrNull()
}

/** VoiceGuard Dialer (feature 1): our in-call screen with the scam tools built in. */
class VgInCallService : InCallService() {
    override fun onCallAdded(call: Call) {
        CallManager.service = this
        CallManager.added(this, call)
        startActivity(Intent(this, InCallActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    override fun onCallRemoved(call: Call) {
        CallManager.removed(this, call)
    }

    override fun onCallAudioStateChanged(audioState: CallAudioState) {
        CallManager.muted.value = audioState.isMuted
        CallManager.speaker.value = audioState.route == CallAudioState.ROUTE_SPEAKER
        CallNotifier.update(this)
    }

    override fun onDestroy() {
        if (CallManager.service == this) CallManager.service = null
        super.onDestroy()
    }
}
