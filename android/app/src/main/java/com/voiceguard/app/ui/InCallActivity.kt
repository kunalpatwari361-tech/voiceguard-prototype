package com.voiceguard.app.ui

import android.media.MediaRecorder
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
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.CallEnd
import androidx.compose.material.icons.filled.Dialpad
import androidx.compose.material.icons.filled.FamilyRestroom
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.MoreHoriz
import androidx.compose.material.icons.filled.PanTool
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.Quiz
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
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
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.voiceguard.app.audio.Recorder
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Contacts
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.bool
import com.voiceguard.app.data.int
import com.voiceguard.app.data.num
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.str
import com.voiceguard.app.service.CallWatch
import com.voiceguard.app.service.CallerSms
import com.voiceguard.app.service.SmsWatch
import com.voiceguard.app.telecom.CallManager
import kotlinx.coroutines.delay
import kotlinx.serialization.json.JsonObject

/**
 * VoiceGuard Dialer in-call screen for real phone calls, laid out like a phone's own call screen: caller on top,
 * round buttons, red End button. The VoiceGuard tools are buttons on the same screen:
 *  AI check   – live AI voice test of the caller (features 4, 6, 16)
 *  Family     – "Are you really calling?" + tell family by app alert / SMS / WhatsApp, in one place (8, 17)
 *  Voice test – Voice CAPTCHA (12) · Panic (19) · More – number info, HD call, risk details (11, 15)
 */
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
        setContent { VgTheme { CallScreen() } }
    }

    @OptIn(ExperimentalMaterial3Api::class)
    @Composable
    private fun CallScreen() {
        val ctx = this@InCallActivity
        val call by CallManager.call.collectAsState()
        val state by CallManager.state.collectAsState()
        val muted by CallManager.muted.collectAsState()
        val speaker by CallManager.speaker.collectAsState()
        val connectedAt by CallManager.connectedAt.collectAsState()
        val number = remember(call) { CallManager.number }
        val family = remember(number) { Sync.memberByPhone(number) }
        val contactName = remember(number) { Contacts.nameFor(ctx, number) }
        var info by remember { mutableStateOf<JsonObject?>(null) }
        var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
        var keypad by remember { mutableStateOf(false) }
        var dtmf by remember { mutableStateOf("") }
        var sheet by remember { mutableStateOf<String?>(null) }
        var confirmPanic by remember { mutableStateOf(false) }
        val tools = remember(number) { CallToolsState(number, family?.str("id"), "live_call") }
        val live = remember(number) { LiveCallMonitor(tools) }
        var ended by remember { mutableStateOf(false) }
        val scope = rememberCoroutineScope()
        val speakerOn = { if (!CallManager.speaker.value) CallManager.toggleSpeaker() }
        val callerSms by SmsWatch.forCall.collectAsState()
        val callStart = remember(number) { System.currentTimeMillis() - 60_000 }
        val startAi = { live.start(ctx, scope, speakerOn) }

        LaunchedEffect(number) {
            if (number.isNotBlank() && family == null)
                info = runCatching { Api.get("/api/numbers/$number?user_id=${Prefs.userId}").asObj() }.getOrNull()
        }
        LaunchedEffect(state) {
            // screen turns off at the ear while talking, unless the speaker is on
            if (state == Call.STATE_ACTIVE && !speaker) runCatching { if (proximity?.isHeld == false) proximity?.acquire(60 * 60_000L) }
            else runCatching { if (proximity?.isHeld == true) proximity?.release() }
            // Auto Check Every Call (27): unknown callers get the AI voice check as soon as the call connects
            if (state == Call.STATE_ACTIVE && Prefs.autoCheck && family == null && contactName == null) startAi()
            if (state == Call.STATE_DISCONNECTED && call == null) {
                live.stop(); ended = true; sheet = null
                CallWatch.onCallEnded(ctx)
                if (!Prefs.inRiskyWindow(1)) { delay(2500); finish() }
            }
            while (state == Call.STATE_ACTIVE || state == Call.STATE_HOLDING) { now = System.currentTimeMillis(); delay(1000) }
        }
        DisposableEffect(Unit) { onDispose { live.stop() } }
        LaunchedEffect(tools.analysis) {
            tools.analysis.obj("risk")?.let { CallManager.publishRisk("Live risk ${it.int("score")}/100") }
        }
        // Voice Test answer: pause the live check, record the caller's answer on speaker, then carry on
        val captureAnswer: suspend (String) -> ShortArray? = { _ ->
            speakerOn()
            val wasRunning = live.running
            live.stop(); delay(300)
            val pcm = Recorder(MediaRecorder.AudioSource.VOICE_RECOGNITION).let { r ->
                runCatching { r.record(7) }.getOrNull()?.takeUnless { r.allSilent }
            }
            if (wasRunning) startAi()
            pcm
        }

        val result = tools.analysis?.takeIf { it.bool("ok") != false }
        val level = result.obj("risk").str("level")
        val tint = if (result != null) VG.level(level) else VG.blue
        val name = family?.str("name") ?: contactName ?: Numbers.pretty(number)

        Box(Modifier.fillMaxSize().background(Color(0xFF070B14))
            .background(Brush.verticalGradient(listOf(tint.copy(alpha = 0.38f), Color(0xFF0B1220), Color(0xFF05080F))))) {
            Column(Modifier.fillMaxSize().systemBarsPadding().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp, vertical = 8.dp),
                horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Default.Shield, null, tint = VG.green, modifier = Modifier.size(16.dp))
                    Text(" " + tr("VoiceGuard protected", "VoiceGuard सुरक्षा"), color = VG.muted, fontSize = 12.sp, modifier = Modifier.weight(1f))
                    IconButton({ finish() }) { Icon(Icons.Default.KeyboardArrowDown, tr("Minimise", "छोटा करें"), tint = Color.White) }
                }
                Spacer(Modifier.height(12.dp))
                Text(name, color = Color.White, fontSize = 34.sp, fontWeight = FontWeight.Medium, textAlign = TextAlign.Center, maxLines = 2)
                if (family != null || contactName != null) Text(Numbers.pretty(number), color = VG.muted, fontSize = 15.sp)
                Text(when {
                    ended -> tr("Call ended", "कॉल ख़त्म")
                    state == Call.STATE_RINGING -> tr("Incoming call", "इनकमिंग कॉल")
                    state == Call.STATE_SELECT_PHONE_ACCOUNT -> tr("Choose a SIM to call", "कॉल के लिए SIM चुनें")
                    state == Call.STATE_DIALING || state == Call.STATE_CONNECTING -> tr("Calling…", "कॉल हो रहा है…")
                    state == Call.STATE_HOLDING -> tr("On hold", "होल्ड पर")
                    state == Call.STATE_ACTIVE && connectedAt > 0 -> "%02d:%02d".format((now - connectedAt) / 60000, (now - connectedAt) / 1000 % 60)
                    else -> ""
                }, color = Color.White.copy(alpha = 0.8f), fontSize = 17.sp)
                when {
                    family != null -> Chip(tr("Saved family number", "सेव परिवार नंबर"), VG.green)
                    number in Prefs.scamNumbers -> Chip(tr("Reported scam number", "रिपोर्टेड स्कैम नंबर"), VG.red)
                    contactName == null -> Chip(tr("Not in your contacts", "आपके संपर्कों में नहीं"), VG.amber)
                }
                if (!ended && state != Call.STATE_RINGING) AiPill(live, tools) { sheet = "ai" }
                callerSms?.takeIf { it.at >= callStart }?.let { m ->
                    CallerSmsCard(m) {
                        startActivity(android.content.Intent(ctx, MainActivity::class.java).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
                            .putExtra("nav", "sms").putExtra("address", m.from))
                    }
                }
                Spacer(Modifier.height(16.dp))

                when {
                    ended -> EndedPanel(tools, number)
                    state == Call.STATE_SELECT_PHONE_ACCOUNT -> {
                        Text(tr("Which SIM should make this call?", "यह कॉल किस SIM से करें?"), color = Color.White, fontWeight = FontWeight.SemiBold)
                        com.voiceguard.app.telecom.Sims.list(ctx).forEach { (h, label) ->
                            BigButton(label, Icons.Default.Call, VG.green) { CallManager.selectSim(h) }
                        }
                        SmallButton(tr("Cancel call", "कॉल रद्द करें")) { CallManager.hangup() }
                    }
                    state == Call.STATE_RINGING -> {
                        info?.let { if ((it.num("spam_score") ?: 0.0) >= 0.3 || contactName == null) NumberInfoCard(it) }
                        Spacer(Modifier.height(24.dp))
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
                            RoundAction(Icons.Default.CallEnd, tr("Decline", "काटें"), VG.red) { CallManager.hangup() }
                            RoundAction(Icons.Default.Call, tr("Accept", "उठाएं"), VG.green) { CallManager.answer() }
                        }
                    }
                    keypad -> {
                        Text(dtmf.ifEmpty { " " }, color = Color.White, fontSize = 30.sp, fontWeight = FontWeight.Light)
                        listOf("123", "456", "789", "*0#").forEach { row ->
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
                                row.forEach { ch -> KeyButton(ch) { CallManager.dtmf(ch); dtmf += ch } }
                            }
                        }
                        Spacer(Modifier.height(8.dp))
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly, verticalAlignment = Alignment.CenterVertically) {
                            Spacer(Modifier.width(78.dp))
                            EndButton { CallManager.hangup() }
                            TextButton({ keypad = false }, Modifier.width(78.dp)) { Text(tr("Hide", "छिपाएं"), color = Color.White) }
                        }
                    }
                    else -> {
                        val holding = state == Call.STATE_HOLDING
                        val really = tools.really.str("answer")
                        ButtonRow(
                            { CallButton(Icons.Default.MicOff, tr("Mute", "म्यूट"), active = muted) { CallManager.toggleMute() } },
                            { CallButton(Icons.AutoMirrored.Filled.VolumeUp, tr("Speaker", "स्पीकर"), active = speaker) { CallManager.toggleSpeaker() } },
                            { CallButton(Icons.Default.Pause, if (holding) tr("Resume", "जारी") else tr("Hold", "होल्ड"), active = holding) { CallManager.toggleHold() } },
                        )
                        ButtonRow(
                            { CallButton(Icons.Default.GraphicEq, tr("AI check", "AI जाँच"), active = live.running,
                                badge = if (result != null) VG.level(level) else if (live.blocked) VG.amber else null) {
                                if (!live.running) startAi()
                                sheet = "ai"
                            } },
                            { CallButton(Icons.Default.FamilyRestroom, tr("Family", "परिवार"),
                                badge = when (really) { "no" -> VG.red; "yes" -> VG.green; null -> null; else -> VG.amber }) { sheet = "family" } },
                            { CallButton(Icons.Default.Dialpad, tr("Keypad", "कीपैड")) { keypad = true } },
                        )
                        ButtonRow(
                            { CallButton(Icons.Default.Quiz, tr("Voice test", "वॉइस टेस्ट"),
                                badge = tools.challengeResult?.let { if (it.bool("passed") == true) VG.green else VG.red }) { sheet = "test" } },
                            { CallButton(Icons.Default.PanTool, tr("Panic", "पैनिक"), tint = VG.red) { confirmPanic = true } },
                            { CallButton(Icons.Default.MoreHoriz, tr("More", "और")) { sheet = "more" } },
                        )
                        Spacer(Modifier.height(10.dp))
                        EndButton { CallManager.hangup() }
                    }
                }
                Spacer(Modifier.height(12.dp))
            }
        }

        if (sheet != null) ModalBottomSheet(onDismissRequest = { sheet = null }, containerColor = VG.bg,
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
            Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp).padding(bottom = 28.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)) {
                when (sheet) {
                    "ai" -> {
                        SheetTitle(tr("AI voice check", "AI आवाज़ जाँच"), tr("Tests the caller's voice every few seconds (call on speaker).",
                            "हर कुछ सेकंड में कॉलर की आवाज़ जाँचता है (कॉल स्पीकर पर)।"))
                        LiveRiskPanel(live, tools) { startAi() }
                        result?.let { ReportView(it, ReportCtx(number, tools.claimedId, "live_call", tools.audio), null, compact = true) }
                        ErrorBox(tools.err)
                    }
                    "family" -> {
                        SheetTitle(tr("Family: check & alert", "परिवार: पुष्टि व अलर्ट"), tr("Ask the person the caller claims to be, and tell your family – without hanging up.",
                            "कॉलर जिसका नाम ले रहा है उससे पूछें, और परिवार को बताएं – कॉल काटे बिना।"))
                        ReallyCallingSection(tools)
                        TellFamilySection(tools)
                    }
                    "test" -> {
                        SheetTitle(tr("Voice test", "वॉइस टेस्ट"), null)
                        VoiceTestSection(tools, captureAnswer)
                    }
                    "more" -> {
                        SheetTitle(tr("More", "और"), null)
                        info?.let { NumberInfoCard(it) }
                        HdCallSection(tools, null)
                        RiskSection(tools, null)
                    }
                }
            }
        }
        if (confirmPanic) AlertDialog(
            onDismissRequest = { confirmPanic = false },
            title = { Text(tr("Feeling pressured?", "दबाव महसूस हो रहा है?")) },
            text = { Text(tr("VoiceGuard will alert your family right now and pause payments. You can hang up – a real family member will understand.",
                "VoiceGuard अभी परिवार को अलर्ट करेगा और पेमेंट रोकेगा। आप कॉल काट सकते हैं – असली परिवार समझेगा।")) },
            confirmButton = { TextButton({ confirmPanic = false; panic(ctx, tools) }) { Text(tr("PANIC – alert family", "पैनिक – परिवार को अलर्ट"), color = VG.red) } },
            dismissButton = { TextButton({ confirmPanic = false }) { Text(tr("Cancel", "रद्द करें")) } },
        )
    }

    @Composable
    private fun EndedPanel(tools: CallToolsState, number: String) {
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

    override fun onResume() {
        super.onResume()
        com.voiceguard.app.telecom.CallNotifier.uiVisible = true
        com.voiceguard.app.telecom.CallNotifier.update(this)
    }

    override fun onPause() {
        com.voiceguard.app.telecom.CallNotifier.uiVisible = false
        com.voiceguard.app.telecom.CallNotifier.update(this)
        super.onPause()
    }

    override fun onDestroy() {
        runCatching { if (proximity?.isHeld == true) proximity?.release() }
        super.onDestroy()
    }
}

/** "AI risk 87 · likely AI" under the caller's name; tap for details. Hidden until the AI check runs. */
@Composable
private fun AiPill(live: LiveCallMonitor, tools: CallToolsState, onClick: () -> Unit) {
    val a = tools.analysis?.takeIf { it.bool("ok") != false }
    val score = a.obj("risk").int("score")
    val level = a.obj("risk").str("level")
    val (color, text) = when {
        live.blocked -> VG.amber to tr("AI check: microphone blocked – tap to fix", "AI जाँच: माइक बंद – ठीक करने के लिए टैप करें")
        score != null -> VG.level(level) to (tr("AI risk ", "AI जोखिम ") + "$score · " + when (level) {
            "danger" -> tr("likely AI / scam", "शायद AI / ठगी")
            "caution" -> tr("be careful", "सावधान")
            else -> tr("sounds human", "इंसानी आवाज़")
        })
        live.running -> VG.violet to tr("AI is listening to the caller…", "AI कॉलर को सुन रहा है…")
        else -> return
    }
    Row(Modifier.clip(RoundedCornerShape(50)).background(color.copy(alpha = 0.25f)).clickable(onClick = onClick)
        .padding(horizontal = 14.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(10.dp).clip(CircleShape).background(color))
        Spacer(Modifier.width(8.dp))
        Text(text, color = Color.White, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
    }
}

/** "The caller just sent you an SMS" / "You got a code during this call – don't read it out". */
@Composable
private fun CallerSmsCard(m: CallerSms, onClick: () -> Unit) {
    val danger = m.verdict.isOtp || m.verdict.level == "danger"
    val color = if (danger) VG.red else if (m.verdict.level == "caution") VG.amber else VG.blue
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(color.copy(alpha = 0.22f)).clickable(onClick = onClick)
        .padding(12.dp)) {
        Text(when {
            m.verdict.isOtp -> tr("You got a code during this call – NEVER read it out", "इस कॉल के दौरान कोड आया – कभी न बताएं")
            m.fromCaller -> tr("The caller sent you an SMS", "कॉलर ने SMS भेजा")
            else -> tr("New SMS", "नया SMS")
        }, color = Color.White, fontWeight = FontWeight.Bold, fontSize = 14.sp)
        Text(m.body.replace('\n', ' ').take(140), color = Color.White.copy(alpha = 0.85f), fontSize = 13.sp, maxLines = 2)
        if (m.verdict.reasons.isNotEmpty()) Text("⚠ " + m.verdict.reasons.joinToString(" · "), color = color, fontSize = 12.sp)
    }
}

@Composable
private fun ButtonRow(vararg buttons: @Composable () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), horizontalArrangement = Arrangement.SpaceEvenly) {
        buttons.forEach { it() }
    }
}

/** Round phone-style button: translucent when off, white when on, optional coloured dot (e.g. the AI result). */
@Composable
private fun CallButton(icon: ImageVector, label: String, active: Boolean = false, tint: Color = Color.White,
                       badge: Color? = null, onClick: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.width(96.dp)) {
        Box(Modifier.size(74.dp)) {
            Box(Modifier.size(74.dp).clip(CircleShape).background(if (active) Color.White else Color.White.copy(alpha = 0.15f))
                .clickable(onClick = onClick), contentAlignment = Alignment.Center) {
                Icon(icon, label, tint = if (active) Color(0xFF0F172A) else tint, modifier = Modifier.size(30.dp))
            }
            if (badge != null) Box(Modifier.align(Alignment.TopEnd).padding(4.dp).size(16.dp).clip(CircleShape).background(badge))
        }
        Text(label, color = Color.White, fontSize = 13.sp, maxLines = 1, modifier = Modifier.padding(top = 6.dp))
    }
}

@Composable
private fun EndButton(onClick: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(Modifier.size(78.dp).clip(CircleShape).background(VG.red).clickable(onClick = onClick), contentAlignment = Alignment.Center) {
            Icon(Icons.Default.CallEnd, tr("End call", "कॉल काटें"), tint = Color.White, modifier = Modifier.size(36.dp))
        }
        Text(tr("End", "काटें"), color = Color.White, fontSize = 13.sp, modifier = Modifier.padding(top = 6.dp))
    }
}

@Composable
private fun RoundAction(icon: ImageVector, label: String, color: Color, onClick: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(Modifier.size(78.dp).clip(CircleShape).background(color).clickable(onClick = onClick), contentAlignment = Alignment.Center) {
            Icon(icon, label, tint = Color.White, modifier = Modifier.size(36.dp))
        }
        Text(label, color = Color.White, fontSize = 14.sp, modifier = Modifier.padding(top = 6.dp))
    }
}

private val KEY_LETTERS = mapOf('2' to "ABC", '3' to "DEF", '4' to "GHI", '5' to "JKL", '6' to "MNO",
    '7' to "PQRS", '8' to "TUV", '9' to "WXYZ", '0' to "+")

@Composable
private fun KeyButton(ch: Char, onClick: () -> Unit) {
    Box(Modifier.size(78.dp).clip(CircleShape).background(Color.White.copy(alpha = 0.15f)).clickable(onClick = onClick),
        contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("$ch", color = Color.White, fontSize = 30.sp)
            KEY_LETTERS[ch]?.let { Text(it, color = Color.White.copy(alpha = 0.7f), fontSize = 10.sp, letterSpacing = 2.sp) }
        }
    }
}

@Composable
private fun SheetTitle(title: String, sub: String?) {
    Column {
        Text(title, fontSize = 20.sp, fontWeight = FontWeight.Bold)
        if (sub != null) Text(sub, color = VG.muted, fontSize = 13.sp)
    }
}
