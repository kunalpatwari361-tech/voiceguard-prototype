package com.voiceguard.app.ui

import android.os.Bundle
import android.os.PowerManager
import android.telecom.Call
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.CallEnd
import androidx.compose.material.icons.filled.Dialpad
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.Text
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Contacts
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.int
import com.voiceguard.app.data.num
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.str
import com.voiceguard.app.service.CallWatch
import com.voiceguard.app.telecom.CallManager
import kotlinx.coroutines.delay
import kotlinx.serialization.json.JsonObject

/** VoiceGuard Dialer in-call screen for real phone calls (feature 1 + tools 4, 6, 8, 11, 12, 15, 16, 19). */
class InCallActivity : ComponentActivity() {
    private var proximity: PowerManager.WakeLock? = null

    private fun handleAction(i: android.content.Intent?) {
        if (i?.getStringExtra("action") == "answer") CallManager.answer()
    }

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        handleAction(intent)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handleAction(intent)
        val pm = getSystemService(PowerManager::class.java)
        if (pm.isWakeLockLevelSupported(PowerManager.PROXIMITY_SCREEN_OFF_WAKE_LOCK))
            proximity = pm.newWakeLock(PowerManager.PROXIMITY_SCREEN_OFF_WAKE_LOCK, "voiceguard:incall")
        setContent {
            VgTheme {
                val call by CallManager.call.collectAsState()
                val state by CallManager.state.collectAsState()
                val muted by CallManager.muted.collectAsState()
                val speaker by CallManager.speaker.collectAsState()
                val connectedAt by CallManager.connectedAt.collectAsState()
                val number = remember(call) { CallManager.number }
                val family = remember(number) { Sync.memberByPhone(number) }
                val contactName = remember(number) { Contacts.nameFor(this@InCallActivity, number) }
                var info by remember { mutableStateOf<JsonObject?>(null) }
                var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
                var keypad by remember { mutableStateOf(false) }
                var dtmf by remember { mutableStateOf("") }
                val tools = remember(number) { CallToolsState(number, family?.str("id"), "live_call") }
                val live = remember(number) { LiveCallMonitor(tools) }
                var ended by remember { mutableStateOf(false) }
                val scope = rememberCoroutineScope()

                LaunchedEffect(number) {
                    if (number.isNotBlank() && family == null)
                        info = runCatching { Api.get("/api/numbers/$number?user_id=${Prefs.userId}").asObj() }.getOrNull()
                }
                LaunchedEffect(state) {
                    // screen turns off at the ear while talking, unless the speaker is on
                    if (state == Call.STATE_ACTIVE && !speaker) runCatching { if (proximity?.isHeld == false) proximity?.acquire(60 * 60_000L) }
                    else runCatching { if (proximity?.isHeld == true) proximity?.release() }
                    // Auto Check Every Call (27): unknown callers get the live voice check as soon as the call connects
                    if (state == Call.STATE_ACTIVE && Prefs.autoCheck && family == null && contactName == null)
                        live.start(scope) { if (!CallManager.speaker.value) CallManager.toggleSpeaker() }
                    if (state == Call.STATE_DISCONNECTED && call == null) {
                        live.stop(); ended = true
                        CallWatch.onCallEnded(this@InCallActivity)
                        if (!Prefs.inRiskyWindow(1)) { delay(2500); finish() }
                    }
                    while (state == Call.STATE_ACTIVE || state == Call.STATE_HOLDING) { now = System.currentTimeMillis(); delay(1000) }
                }
                DisposableEffect(Unit) { onDispose { live.stop() } }
                LaunchedEffect(tools.analysis) {
                    tools.analysis.obj("risk")?.let { CallManager.publishRisk("Live risk ${it.int("score")}/100") }
                }

                Screen(tr("VoiceGuard call", "VoiceGuard कॉल"), onBack = { finish() }) {
                    val risk = tools.analysis.obj("risk")
                    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(VG.surface).padding(18.dp),
                        horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(family?.str("name") ?: contactName ?: Numbers.pretty(number), fontSize = 26.sp, fontWeight = FontWeight.Bold)
                        if (family != null || contactName != null) Text(Numbers.pretty(number), color = VG.muted, fontSize = 14.sp)
                        when {
                            family != null -> Chip(tr("Saved family number", "सेव परिवार नंबर"), VG.green)
                            number in Prefs.scamNumbers -> Chip(tr("Reported scam number", "रिपोर्टेड स्कैम नंबर"), VG.red)
                            contactName == null -> Chip(tr("Not in your contacts", "आपके संपर्कों में नहीं"), VG.amber)
                        }
                        Text(when {
                            ended -> tr("Call ended", "कॉल ख़त्म")
                            state == Call.STATE_RINGING -> tr("Incoming call", "इनकमिंग कॉल")
                            state == Call.STATE_SELECT_PHONE_ACCOUNT -> tr("Choose a SIM to call", "कॉल के लिए SIM चुनें")
                            state == Call.STATE_DIALING || state == Call.STATE_CONNECTING -> tr("Calling…", "कॉल हो रहा है…")
                            state == Call.STATE_HOLDING -> tr("On hold", "होल्ड पर")
                            state == Call.STATE_ACTIVE && connectedAt > 0 -> "%02d:%02d".format((now - connectedAt) / 60000, (now - connectedAt) / 1000 % 60)
                            else -> ""
                        }, color = VG.muted, modifier = Modifier.padding(top = 4.dp))
                        if (risk != null) Text(tr("Live risk ", "लाइव जोखिम ") + "${risk.int("score")}/100", color = VG.level(risk.str("level")),
                            fontWeight = FontWeight.Bold, fontSize = 18.sp)
                    }
                    info?.let { if ((it.num("spam_score") ?: 0.0) >= 0.3 || contactName == null) NumberInfoCard(it) }

                    if (!ended) when (state) {
                        Call.STATE_SELECT_PHONE_ACCOUNT -> {
                            Text(tr("Which SIM should make this call?", "यह कॉल किस SIM से करें?"), fontWeight = FontWeight.SemiBold)
                            com.voiceguard.app.telecom.Sims.list(this@InCallActivity).forEach { (h, label) ->
                                BigButton(label, Icons.Default.Call, VG.green) { CallManager.selectSim(h) }
                            }
                            SmallButton(tr("Cancel call", "कॉल रद्द करें")) { CallManager.hangup() }
                        }
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
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                SmallButton(tr("Keypad", "कीपैड"), Icons.Default.Dialpad, Modifier.weight(1f)) { keypad = !keypad }
                                SmallButton(if (state == Call.STATE_HOLDING) tr("Resume", "जारी रखें") else tr("Hold", "होल्ड"),
                                    if (state == Call.STATE_HOLDING) Icons.Default.PlayArrow else Icons.Default.Pause, Modifier.weight(1f)) { CallManager.toggleHold() }
                            }
                            if (keypad) {
                                Text(dtmf, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                                listOf("123", "456", "789", "*0#").forEach { row ->
                                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                        row.forEach { ch ->
                                            Box(Modifier.weight(1f).aspectRatio(2.6f).clip(RoundedCornerShape(12.dp)).background(VG.surface2)
                                                .clickable { CallManager.dtmf(ch); dtmf += ch }, contentAlignment = Alignment.Center) {
                                                Text("$ch", fontSize = 22.sp, fontWeight = FontWeight.SemiBold)
                                            }
                                        }
                                    }
                                }
                            }
                            BigButton(tr("End call", "कॉल काटें"), Icons.Default.CallEnd, VG.red) { CallManager.hangup() }
                        }
                    }
                    if (state != Call.STATE_RINGING && state != Call.STATE_SELECT_PHONE_ACCOUNT && !ended) {
                        LiveCheckCard(live) { live.start(scope) { if (!CallManager.speaker.value) CallManager.toggleSpeaker() } }
                        CallTools(tools, null) { _ ->
                            if (!CallManager.speaker.value) CallManager.toggleSpeaker()
                            com.voiceguard.app.audio.Recorder().let { r -> r.record(7).takeUnless { r.allSilent } }
                        }
                    }
                    if (ended) {
                        val claimed = Sync.member(tools.claimedId)
                        if (Prefs.inRiskyWindow(1) && claimed != null) {
                            Banner(tr("Call-Back Alert: call ${claimed.str("name")} on their saved number", "कॉल-बैक अलर्ट: ${claimed.str("name")} को सेव नंबर पर कॉल करें"), VG.blue)
                            BigButton(tr("Call ${claimed.str("name")}", "${claimed.str("name")} को कॉल करें"), Icons.Default.Call, VG.blue) {
                                dial(this@InCallActivity, claimed.str("phone") ?: return@BigButton)
                            }
                        }
                        tools.analysis?.let { ReportActions(it, ReportCtx(number, tools.claimedId, "live_call", tools.audio), null) }
                        SmallButton(tr("Close", "बंद करें")) { finish() }
                    }
                }
            }
        }
    }

    override fun onDestroy() {
        runCatching { if (proximity?.isHeld == true) proximity?.release() }
        super.onDestroy()
    }
}
