package com.voiceguard.app.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.CallEnd
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Done
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.voiceguard.app.audio.HdAudio
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Live
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Reply
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.bi
import com.voiceguard.app.data.json
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.str
import com.voiceguard.app.service.Notify
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject

/** VoiceGuard HD Call (11): app-to-app wideband call to a family member's registered phone. */
@Composable
fun HdCallScreen(callIdIn: String?, peerId: String?, peerName: String?, incoming: Boolean, back: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var callId by remember { mutableStateOf(callIdIn) }
    var status by remember { mutableStateOf(if (incoming) "incoming" else "starting") }
    var msg by remember { mutableStateOf<String?>(null) }
    var analysis by remember { mutableStateOf<JsonObject?>(null) }
    var audio by remember { mutableStateOf<HdAudio?>(null) }
    var muted by remember { mutableStateOf(false) }
    var speaker by remember { mutableStateOf(true) }
    var since by remember { mutableLongStateOf(0L) }
    var now by remember { mutableLongStateOf(0L) }
    val name = peerName?.takeIf { it.isNotBlank() } ?: Sync.member(peerId).str("name") ?: tr("Family", "परिवार")
    val micPerm = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}

    fun connect() {
        val id = callId ?: return
        if (ctx.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            micPerm.launch(Manifest.permission.RECORD_AUDIO); return
        }
        audio = HdAudio(ctx, id).also { it.start(speaker) }
        status = "connected"; since = System.currentTimeMillis()
    }

    fun hangup() {
        callId?.let { Live.send(json("type" to "hd_hangup", "call_id" to it)) }
        audio?.stop(); audio = null
        status = "ended"
    }

    LaunchedEffect(Unit) {
        Notify.cancel(ctx, Notify.ID_HD)
        if (!incoming && peerId != null) {
            runCatching { Api.post("/api/hd/start", json("caller_id" to Prefs.userId, "callee_id" to peerId)).asObj() }
                .onSuccess { r -> status = r.str("status") ?: "offline"; callId = r.str("call_id"); msg = r.bi("message", "message_hi") }
                .onFailure { status = "offline"; msg = it.message }
        }
    }
    LaunchedEffect(Unit) {
        Live.events.collect { e ->
            if (e.str("call_id") != callId) return@collect
            when (e.str("type")) {
                "hd_status" -> when (e.str("status")) {
                    "accepted" -> connect()
                    "ended" -> { audio?.stop(); audio = null; status = "ended" }
                    else -> status = e.str("status") ?: status
                }
                "hd_analysis" -> analysis = e.obj("result")
            }
        }
    }
    LaunchedEffect(status) { while (status == "connected") { now = System.currentTimeMillis(); delay(1000) } }
    DisposableEffect(Unit) { onDispose { if (audio != null) hangup() } }

    val level by (audio?.level ?: kotlinx.coroutines.flow.MutableStateFlow(-96.0)).collectAsState()
    val remote by (audio?.remoteLevel ?: kotlinx.coroutines.flow.MutableStateFlow(-96.0)).collectAsState()

    Screen(tr("VoiceGuard HD Call", "VoiceGuard HD कॉल"), back) {
        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(VG.surface).padding(18.dp),
            horizontalAlignment = Alignment.CenterHorizontally) {
            Text(name, fontSize = 26.sp, fontWeight = FontWeight.Bold)
            Chip("HD · 16 kHz · " + tr("verified registered phone", "रजिस्टर्ड फ़ोन सत्यापित"), VG.blue)
            Text(when (status) {
                "starting" -> tr("Connecting…", "जुड़ रहा है…")
                "ringing" -> tr("Ringing $name's VoiceGuard app…", "$name के ऐप पर घंटी जा रही है…")
                "incoming" -> tr("$name is calling you on VoiceGuard HD", "$name आपको HD कॉल कर रहे हैं")
                "connected" -> "%02d:%02d".format((now - since) / 60000, (now - since) / 1000 % 60)
                "declined" -> tr("$name declined the HD call", "$name ने HD कॉल मना की")
                "missed" -> tr("$name did not answer", "$name ने जवाब नहीं दिया")
                "offline" -> tr("$name's app is not reachable", "$name का ऐप उपलब्ध नहीं है")
                else -> tr("Call ended", "कॉल ख़त्म")
            }, color = if (status == "connected") VG.green else VG.muted, modifier = Modifier.padding(top = 6.dp))
        }
        msg?.let { if (status == "offline") ErrorBox(it) }
        if (status in listOf("declined", "missed", "offline") && !incoming) Banner(
            tr("Could not verify over HD", "HD से पुष्टि नहीं हो पाई"), VG.amber,
            tr("If the person on your normal call refuses to switch to VoiceGuard HD, treat it as a scam. Call them back on their saved number.",
                "अगर सामान्य कॉल वाला व्यक्ति HD कॉल पर आने से मना करे तो इसे ठगी मानें।"))

        when (status) {
            "incoming" -> Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                BigButton(tr("Decline", "मना करें"), Icons.Default.Close, VG.red, modifier = Modifier.weight(1f)) {
                    val id = callId
                    kotlinx.coroutines.MainScope().launch { Reply.hd(id, false) }
                    status = "ended"; back()
                }
                BigButton(tr("Accept", "स्वीकार करें"), Icons.Default.Done, VG.green, modifier = Modifier.weight(1f)) {
                    scope.launch {
                        when (val st = Reply.hd(callId, true)) {
                            "accepted" -> connect()
                            null -> status = "ended"
                            else -> status = st          // e.g. missed: the caller gave up
                        }
                    }
                }
            }
            "connected" -> {
                Text(tr("You", "आप"), color = VG.muted, fontSize = 12.sp); LevelMeter(level)
                Text(name, color = VG.muted, fontSize = 12.sp); LevelMeter(remote)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SmallButton(if (muted) tr("Unmute", "अनम्यूट") else tr("Mute", "म्यूट"), if (muted) Icons.Default.MicOff else Icons.Default.Mic, Modifier.weight(1f)) {
                        muted = !muted; audio?.muted = muted
                    }
                    SmallButton(tr("Speaker", "स्पीकर") + if (speaker) " ✓" else "", Icons.Default.VolumeUp, Modifier.weight(1f)) {
                        speaker = !speaker; audio?.setSpeaker(speaker)
                    }
                }
                BigButton(tr("Hang up", "कॉल काटें"), Icons.Default.CallEnd, VG.red) { hangup() }
                if (analysis == null) Busy(tr("Live check runs after ~6 s of speech (AI voice, voice print, reply delay)…",
                    "6 सेकंड बोलने के बाद लाइव जाँच होगी…"))
            }
            "ringing", "starting" -> BigButton(tr("Cancel", "रद्द करें"), Icons.Default.CallEnd, VG.red) { hangup(); back() }
            "ended", "declined", "missed", "offline" -> if (peerId != null) BigButton(tr("Call ${name} on saved number", "$name को सेव नंबर पर कॉल करें"), Icons.Default.Call, VG.blue) {
                dial(ctx, Sync.member(peerId).str("phone") ?: return@BigButton)
            }
        }
        analysis?.let {
            Text(tr("Live check of $name's voice", "$name की आवाज़ की लाइव जाँच"), fontWeight = FontWeight.SemiBold)
            ReportView(it, ReportCtx(null, peerId, "hd_call", null), null, compact = true)
        }
    }
}

/** The claimed person's side of "Are You Really Calling?" (8). */
@Composable
fun VerifyRequestScreen(back: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val v = Shared.verify
    var answered by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) { Notify.cancel(ctx, Notify.ID_VERIFY) }
    Screen(tr("Are you calling?", "क्या आप कॉल कर रहे हैं?"), back) {
        if (v == null) { Text("No request"); return@Screen }
        val (rid, from, number) = v
        Banner(tr("$from is asking: are you calling them right now?", "$from पूछ रहे हैं: क्या आप अभी उन्हें कॉल कर रहे हैं?"), VG.amber,
            tr("A caller from ${Numbers.pretty(number)} says they are YOU.", "${Numbers.pretty(number)} से कोई कॉलर कह रहा है कि वह आप हैं।"))
        if (answered == null) {
            BigButton(tr("YES, it's me calling", "हाँ, मैं ही कॉल कर रहा हूँ"), Icons.Default.Done, VG.green) {
                scope.launch { Reply.verify(rid, "yes") }; answered = "yes"
            }
            BigButton(tr("NO, it's NOT me", "नहीं, यह मैं नहीं हूँ"), Icons.Default.Close, VG.red) {
                scope.launch { Reply.verify(rid, "no") }; answered = "no"
            }
        } else {
            Banner(if (answered == "no") tr("$from has been warned: FAKE CALL.", "$from को चेतावनी दे दी गई: नकली कॉल।")
                   else tr("$from now knows it's really you.", "$from को पता चल गया कि यह आप ही हैं।"), if (answered == "no") VG.red else VG.green)
            if (answered == "no") {
                val phone = Sync.others().firstOrNull { it.str("name") == from }?.str("phone")
                if (phone != null) BigButton(tr("Call $from now", "$from को अभी कॉल करें"), Icons.Default.Call, VG.blue) { dial(ctx, phone) }
            }
        }
    }
}
