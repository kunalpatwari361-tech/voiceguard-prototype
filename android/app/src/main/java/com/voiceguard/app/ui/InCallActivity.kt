package com.voiceguard.app.ui

import android.os.Bundle
import android.telecom.Call
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
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
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.Text
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.voiceguard.app.audio.Recorder
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Contacts
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.str
import com.voiceguard.app.service.CallWatch
import com.voiceguard.app.telecom.CallManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject

/** VoiceGuard Dialer in-call screen for real phone calls (feature 1 + tools 6, 8, 11, 12, 15, 16, 19). */
class InCallActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            VgTheme {
                val call by CallManager.call.collectAsState()
                val state by CallManager.state.collectAsState()
                val muted by CallManager.muted.collectAsState()
                val speaker by CallManager.speaker.collectAsState()
                val connectedAt by CallManager.connectedAt.collectAsState()
                val number = remember(call) { CallManager.number }
                val family = remember(number) { Sync.memberByPhone(number) }
                val contactName by produceState<String?>(null, number) {
                    value = withContext(Dispatchers.IO) { Contacts.nameFor(this@InCallActivity, number) }
                }
                var info by remember { mutableStateOf<JsonObject?>(null) }
                var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
                val tools = remember(number) { CallToolsState(number, null, "live_call") }
                var ended by remember { mutableStateOf(false) }
                val scope = rememberCoroutineScope()

                LaunchedEffect(number) {
                    // Saved contacts are people you know: skip the spam lookup, like a caller-ID app does.
                    if (number.isNotBlank() && family == null && withContext(Dispatchers.IO) { Contacts.nameFor(this@InCallActivity, number) } == null)
                        info = runCatching { Api.get("/api/numbers/$number?user_id=${Prefs.userId}").asObj() }.getOrNull()
                }
                LaunchedEffect(state) {
                    if (state == Call.STATE_DISCONNECTED && call == null) { ended = true; CallWatch.onCallEnded(this@InCallActivity) }
                    while (state == Call.STATE_ACTIVE) { now = System.currentTimeMillis(); delay(1000) }
                }

                Screen(tr("VoiceGuard call", "VoiceGuard कॉल"), onBack = { finish() }) {
                    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(VG.surface).padding(18.dp),
                        horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(family?.str("name") ?: contactName ?: Numbers.pretty(number), fontSize = 26.sp, fontWeight = FontWeight.Bold)
                        if (family == null && contactName != null) Text(Numbers.pretty(number), color = VG.muted)
                        if (family != null) Chip(tr("Saved family number", "सेव परिवार नंबर"), VG.green)
                        else if (contactName != null) Chip(tr("Saved contact", "सेव कॉन्टैक्ट"), VG.blue)
                        Text(when {
                            ended -> tr("Call ended", "कॉल ख़त्म")
                            state == Call.STATE_RINGING -> tr("Incoming call", "इनकमिंग कॉल")
                            state == Call.STATE_DIALING || state == Call.STATE_CONNECTING -> tr("Calling…", "कॉल हो रहा है…")
                            state == Call.STATE_ACTIVE && connectedAt > 0 -> "%02d:%02d".format((now - connectedAt) / 60000, (now - connectedAt) / 1000 % 60)
                            state == Call.STATE_HOLDING -> tr("On hold", "होल्ड पर")
                            else -> ""
                        }, color = VG.muted)
                    }
                    info?.let { NumberInfoCard(it) }
                    if (!ended) when (state) {
                        Call.STATE_RINGING -> Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            BigButton(tr("Decline", "काटें"), Icons.Default.CallEnd, VG.red, modifier = Modifier.weight(1f)) { CallManager.hangup() }
                            BigButton(tr("Answer", "उठाएं"), Icons.Default.Call, VG.green, modifier = Modifier.weight(1f)) { CallManager.answer() }
                        }
                        else -> {
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                SmallButton(if (muted) tr("Unmute", "अनम्यूट") else tr("Mute", "म्यूट"), if (muted) Icons.Default.MicOff else Icons.Default.Mic,
                                    Modifier.weight(1f)) { CallManager.toggleMute() }
                                SmallButton(tr("Speaker", "स्पीकर") + if (speaker) " ✓" else "", Icons.Default.VolumeUp, Modifier.weight(1f)) { CallManager.toggleSpeaker() }
                            }
                            BigButton(tr("End call", "कॉल काटें"), Icons.Default.CallEnd, VG.red) { CallManager.hangup() }
                        }
                    }
                    if (family == null && state != Call.STATE_RINGING) {
                        SmallButton(tr("Record caller on speaker (8 s) for voice check", "आवाज़ जाँच के लिए 8 सेकंड रिकॉर्ड करें"), Icons.Default.Mic) {
                            if (!speaker) CallManager.toggleSpeaker()
                            val r = Recorder()
                            scope.launch {
                                val pcm = r.record(8)
                                if (r.allSilent) tools.err = tr("Android blocked call audio on this phone. Use 'Verify with HD call' or 'Are You Really Calling?' instead.",
                                    "इस फ़ोन पर Android ने कॉल ऑडियो रोक दिया। HD कॉल या 'क्या सच में आप कॉल कर रहे हैं?' इस्तेमाल करें।")
                                else { tools.audio = pcm; tools.analyze() }
                            }
                        }
                        CallTools(tools, null) { _ ->
                            if (!speaker) CallManager.toggleSpeaker()
                            Recorder().let { r -> r.record(7).takeUnless { r.allSilent } }
                        }
                    }
                }
            }
        }
    }
}
