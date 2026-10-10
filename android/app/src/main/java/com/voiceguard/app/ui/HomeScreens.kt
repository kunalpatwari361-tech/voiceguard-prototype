package com.voiceguard.app.ui

import android.app.role.RoleManager
import android.net.Uri
import android.telecom.TelecomManager
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Backspace
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.Dialpad
import androidx.compose.material.icons.filled.Flag
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Group
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.HelpOutline
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.LocalPolice
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.PanTool
import androidx.compose.material.icons.filled.Sms
import androidx.compose.material.icons.filled.PlayCircle
import androidx.compose.material.icons.filled.RecordVoiceOver
import androidx.compose.material.icons.filled.Science
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Translate
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.navigation.NavHostController
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Live
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.arr
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.int
import com.voiceguard.app.data.json
import com.voiceguard.app.data.num
import com.voiceguard.app.data.objs
import com.voiceguard.app.data.str
import com.voiceguard.app.telecom.RecentCalls
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject

@Composable
fun HomeScreen(nav: NavHostController) {
    val ctx = LocalContext.current
    val connected by Live.connected.collectAsState()
    var lang by remember { mutableIntStateOf(0) }
    var fam by remember { mutableStateOf(Sync.cachedFamily()) }
    LaunchedEffect(Unit) { runCatching { Sync.family() }.getOrNull()?.let { fam = it } }

    Screen("VoiceGuard", onBack = null, actions = {
        IconButton({ Prefs.hindi = !Prefs.hindi; lang++ }) { Icon(Icons.Default.Translate, "Language") }
        IconButton({ nav.navigate("alerts") }) { Icon(Icons.Default.Notifications, "Alerts") }
        IconButton({ nav.navigate("settings") }) { Icon(Icons.Default.Settings, "Settings") }
    }) {
        androidx.compose.runtime.key(lang) {
            Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(VG.surface).padding(16.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(56.dp).clip(CircleShape).background((if (connected) VG.green else VG.amber).copy(alpha = 0.2f)),
                    contentAlignment = Alignment.Center) {
                    Icon(Icons.Default.Shield, null, tint = if (connected) VG.green else VG.amber, modifier = Modifier.size(32.dp))
                }
                Spacer(Modifier.width(14.dp))
                Column(Modifier.weight(1f)) {
                    Text(tr("Namaste, ", "नमस्ते, ") + (Prefs.name ?: ""), fontWeight = FontWeight.Bold, fontSize = 18.sp)
                    Text(if (connected) tr("Protected · family link live", "सुरक्षित · परिवार लिंक चालू")
                         else tr("Server offline – start the laptop server (same Wi-Fi or USB)", "सर्वर बंद – लैपटॉप सर्वर चालू करें (वही Wi-Fi या USB)"),
                        color = if (connected) VG.green else VG.amber, fontSize = 13.sp)
                    val famCount = fam.objs("members").count { it.str("id") != Prefs.userId }
                    Text("$famCount " + if (famCount == 1) tr("family member", "परिवार सदस्य") else tr("family members", "परिवार सदस्य"), color = VG.muted, fontSize = 13.sp)
                }
            }
            if (Prefs.inRiskyWindow(15)) Banner(tr("You had a risky call recently", "हाल ही में एक ख़तरनाक कॉल आई थी"), VG.red,
                tr("Panic Pause is ON for payments. Talk to family before sending money.", "पेमेंट पर पैनिक पॉज़ चालू है। पैसे भेजने से पहले परिवार से बात करें।"))

            Text(tr("Calls", "कॉल"), color = VG.muted)
            Grid(listOf(
                Triple(Icons.Default.Dialpad, tr("Dialer", "डायलर") to tr("Calls with built-in scam check", "स्कैम जाँच वाला डायलर"), "dialer"),
                Triple(Icons.Default.Group, tr("Family Circle", "परिवार सर्कल") to tr("Verify · location · HD call", "पुष्टि · लोकेशन · HD कॉल"), "family"),
                Triple(Icons.Default.PlayCircle, tr("Demo scam call", "डेमो स्कैम कॉल") to tr("Try an AI clone call safely", "AI क्लोन कॉल आज़माएं"), "demo"),
                Triple(Icons.Default.RecordVoiceOver, tr("My Voice Print", "मेरा वॉइस प्रिंट") to tr("So family can verify you", "ताकि परिवार पहचान सके"), "voiceprint"),
            ), nav)
            LayerTitle(1, tr("Spam protection – before you answer", "स्पैम सुरक्षा – कॉल उठाने से पहले"), VG.blue)
            Grid(listOf(
                Triple(Icons.Default.Block, tr("Scam list & block", "स्कैम सूची व ब्लॉक") to tr("Community reports", "समुदाय की रिपोर्ट"), "scamlist"),
                Triple(Icons.Default.GraphicEq, tr("Voice Note Check", "वॉइस नोट जाँच") to tr("WhatsApp audio, recordings", "WhatsApp ऑडियो"), "check?mode=file"),
            ), nav)
            LayerTitle(2, tr("During the call – live protection", "कॉल के दौरान – लाइव सुरक्षा"), VG.violet)
            Grid(listOf(
                Triple(Icons.Default.Mic, tr("Live Call Check", "लाइव कॉल जाँच") to tr("AI listens on speaker", "स्पीकर पर AI सुनता है"), "check?mode=record"),
                Triple(Icons.Default.Sms, tr("Tell family", "परिवार को बताएं") to tr("Alert · SMS · WhatsApp", "अलर्ट · SMS · WhatsApp"), "tellfamily"),
                Triple(Icons.Default.HelpOutline, tr("Are you really calling?", "क्या सच में आप हैं?") to tr("Ask their own phone", "उनके फ़ोन से पूछें"), "family"),
                Triple(Icons.Default.PanTool, tr("Panic Pause", "पैनिक पॉज़") to tr("Stops rushed payments", "जल्दबाज़ी के पेमेंट रोके"), "settings"),
            ), nav)
            LayerTitle(3, tr("After the scam", "ठगी के बाद"), VG.red)
            Grid(listOf(
                Triple(Icons.Default.Folder, tr("Evidence & report", "सबूत व रिपोर्ट") to tr("Chakshu · cyber cell", "चक्षु · साइबर सेल"), "evidence"),
                Triple(Icons.Default.Notifications, tr("Family alerts", "परिवार अलर्ट") to tr("What happened", "क्या हुआ"), "alerts"),
                Triple(Icons.Default.Science, tr("Future lab", "भविष्य की सुविधाएँ") to tr("Police, telecom, bank API…", "पुलिस, टेलीकॉम, बैंक…"), "future"),
            ), nav)
            BigButton(tr("Call 1930 – Cyber Crime Helpline", "1930 पर कॉल करें – साइबर हेल्पलाइन"), Icons.Default.LocalPolice, VG.red) {
                dial(ctx, "1930")
            }
        }
    }
}

/** Heading of one protection layer: 1 before the call, 2 during it, 3 after a scam. */
@Composable
private fun LayerTitle(n: Int, title: String, color: androidx.compose.ui.graphics.Color) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 4.dp)) {
        Box(Modifier.size(22.dp).clip(CircleShape).background(color.copy(alpha = 0.25f)), contentAlignment = Alignment.Center) {
            Text("$n", color = color, fontWeight = FontWeight.Bold, fontSize = 12.sp)
        }
        Spacer(Modifier.width(8.dp))
        Text(title, color = color, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun Grid(items: List<Triple<ImageVector, Pair<String, String>, String>>, nav: NavHostController) {
    val colors = listOf(VG.blue, VG.green, VG.violet, VG.amber, VG.red)
    items.chunked(2).forEachIndexed { r, row ->
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            row.forEachIndexed { c, (icon, t, route) ->
                Tile(icon, t.first, t.second, colors[(r * 2 + c) % colors.size], Modifier.weight(1f)) { nav.navigate(route) }
            }
            if (row.size == 1) Spacer(Modifier.weight(1f))
        }
    }
}

@Composable
fun NumberInfoCard(info: JsonObject) {
    val s = info.num("spam_score") ?: 0.0
    Section(tr("Number Info", "नंबर जानकारी"), Icons.Default.Info, VG.score(s)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(Numbers.pretty(info.str("number")), fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
            Chip(tr("Risk", "जोखिम") + " ${(s * 100).toInt()}%", VG.score(s))
        }
        Text(listOfNotNull(info.str("kind"), info.str("country")).joinToString(" · "), color = VG.muted, fontSize = 13.sp)
        info.objs("flags").forEach { f ->
            val c = when (f.str("level")) { "danger" -> VG.red; "warn" -> VG.amber; "safe" -> VG.green; else -> VG.muted }
            Text("• " + (if (Prefs.hindi) f.str("hi") else f.str("en")).orEmpty(), color = c, fontSize = 14.sp)
        }
    }
}

// ------------------------------------------------------------------ Number Info (feature 15)
@Composable
fun NumberScreen(number: String, back: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var info by remember { mutableStateOf<JsonObject?>(null) }
    var err by remember { mutableStateOf<String?>(null) }
    var reason by remember { mutableStateOf("") }
    var done by remember { mutableStateOf<String?>(null) }
    suspend fun load() {
        runCatching { Api.get("/api/numbers/${Uri.encode(number)}?user_id=${Prefs.userId}").asObj() }
            .onSuccess { info = it }.onFailure { err = it.message }
    }
    LaunchedEffect(number) { load() }
    Screen(tr("Number details", "नंबर जानकारी"), back) {
        ErrorBox(err)
        info?.let { i ->
            NumberInfoCard(i)
            val reasons = i.arr("recent_reasons").mapNotNull { it.str() }
            if (reasons.isNotEmpty()) Section(tr("What people reported", "लोगों ने क्या बताया"), Icons.Default.Flag, VG.red) {
                reasons.forEach { Text("“$it”", color = VG.muted) }
            }
            Text(tr("Community reports: ", "समुदाय रिपोर्ट: ") + (i.int("community_reports") ?: 0))
        }
        OutlinedTextField(reason, { reason = it }, label = { Text(tr("What did the caller do? (optional)", "कॉलर ने क्या किया? (वैकल्पिक)")) },
            modifier = Modifier.fillMaxWidth())
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SmallButton(tr("Report scam", "स्कैम रिपोर्ट करें"), Icons.Default.Flag, Modifier.weight(1f)) {
                scope.launch {
                    runCatching { Api.post("/api/scamlist/report", json("number" to number, "reporter_id" to Prefs.userId, "reason" to reason)) }
                        .onSuccess { done = tr("Reported. Thank you for protecting others.", "रिपोर्ट हो गया। दूसरों को बचाने के लिए धन्यवाद।"); Sync.lists(); load() }
                        .onFailure { err = it.message }
                }
            }
            SmallButton(tr("Block", "ब्लॉक"), Icons.Default.Block, Modifier.weight(1f)) {
                scope.launch {
                    runCatching { Api.post("/api/blocked", json("user_id" to Prefs.userId, "number" to number)) }
                        .onSuccess { done = tr("Blocked for your whole family.", "पूरे परिवार के लिए ब्लॉक हो गया।"); Sync.lists() }
                        .onFailure { err = it.message }
                }
            }
        }
        done?.let { Banner(it, VG.green) }
        SmallButton(tr("Call this number", "इस नंबर पर कॉल करें"), Icons.Default.Call) { dial(ctx, number, direct = false) }
        Text(tr("Never share OTP, PIN or send money because a caller is rushing you.", "कॉलर जल्दबाज़ी करवाए तो भी OTP, PIN न बताएं, पैसे न भेजें।"),
            color = VG.muted, fontSize = 13.sp)
        Spacer(Modifier.height(4.dp))
        Icon(Icons.Default.HelpOutline, null, tint = VG.muted)
    }
}
