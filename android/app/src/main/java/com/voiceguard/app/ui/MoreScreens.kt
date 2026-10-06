package com.voiceguard.app.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountBalance
import androidx.compose.material.icons.filled.AutoMode
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.CellTower
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Flag
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Hub
import androidx.compose.material.icons.filled.LocalPolice
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.OpenInBrowser
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.FamilyRestroom
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import androidx.navigation.NavHostController
import com.voiceguard.app.audio.Recorder
import com.voiceguard.app.audio.Wav
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.asList
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.bi
import com.voiceguard.app.data.bool
import com.voiceguard.app.data.int
import com.voiceguard.app.data.json
import com.voiceguard.app.data.num
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.objs
import com.voiceguard.app.data.str
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import java.io.File

// ------------------------------------------------------------------ Spam: list, block, report (24-26)
@Composable
fun ScamListScreen(nav: NavHostController, back: () -> Unit) {
    val scope = rememberCoroutineScope()
    var tab by remember { mutableIntStateOf(0) }
    var list by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var blocked by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var number by remember { mutableStateOf("") }
    var reason by remember { mutableStateOf("") }
    var err by remember { mutableStateOf<String?>(null) }
    suspend fun load() {
        runCatching { list = Api.get("/api/scamlist").asList(); blocked = Api.get("/api/blocked/${Prefs.userId}").asList(); Sync.lists() }
            .onFailure { err = it.message }
    }
    LaunchedEffect(Unit) { load() }
    Screen(tr("Scam list & blocking", "स्कैम सूची व ब्लॉक"), back) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(tab == 0, { tab = 0 }, label = { Text(tr("Community list", "समुदाय सूची") + " (${list.size})") })
            FilterChip(tab == 1, { tab = 1 }, label = { Text(tr("Blocked", "ब्लॉक") + " (${blocked.size})") })
        }
        Section(tr("Report or block a number", "नंबर रिपोर्ट या ब्लॉक करें"), Icons.Default.Flag, VG.red) {
            OutlinedTextField(number, { number = it }, label = { Text(tr("Phone number", "फ़ोन नंबर")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(reason, { reason = it }, label = { Text(tr("What happened (optional)", "क्या हुआ (वैकल्पिक)")) }, modifier = Modifier.fillMaxWidth())
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SmallButton(tr("Report", "रिपोर्ट"), Icons.Default.Flag, Modifier.weight(1f), enabled = number.length >= 5) {
                    scope.launch { runCatching { Api.post("/api/scamlist/report", json("number" to number, "reporter_id" to Prefs.userId, "reason" to reason)) }; load(); number = ""; reason = "" }
                }
                SmallButton(tr("Block", "ब्लॉक"), Icons.Default.Block, Modifier.weight(1f), enabled = number.length >= 5) {
                    scope.launch { runCatching { Api.post("/api/blocked", json("user_id" to Prefs.userId, "number" to number)) }; load(); number = "" }
                }
            }
        }
        ErrorBox(err)
        if (tab == 0) list.forEach { r ->
            Row(Modifier.fillMaxWidth().clickable { nav.navigate("number/" + Uri.encode(r.str("number"))) }, verticalAlignment = Alignment.CenterVertically) {
                Text(Numbers.pretty(r.str("number")), fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                Chip("${r.int("reports")} " + tr("reports", "रिपोर्ट"), VG.red)
            }
            r.str("last_reason")?.let { Text(it, color = VG.muted, fontSize = 13.sp) }
        } else blocked.forEach { b ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(Numbers.pretty(b.str("number")), modifier = Modifier.weight(1f))
                Text(tr("by ", "द्वारा ") + b.str("by"), color = VG.muted, fontSize = 13.sp)
                if (b.bool("mine") == true) IconButton({
                    scope.launch { runCatching { Api.delete("/api/blocked/${Prefs.userId}/${Uri.encode(b.str("number"))}") }; load() }
                }) { Icon(Icons.Default.Delete, null) }
            }
        }
        Text(tr("Blocked numbers are rejected automatically on every family phone (needs Call screening permission).",
            "ब्लॉक नंबर परिवार के हर फ़ोन पर अपने आप रिजेक्ट होते हैं।"), color = VG.muted, fontSize = 13.sp)
    }
}

// ------------------------------------------------------------------ After the scam (20-23)
@Composable
fun EvidenceListScreen(nav: NavHostController, back: () -> Unit) {
    var items by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var err by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) { runCatching { Api.get("/api/evidence/user/${Prefs.userId}").asList() }.onSuccess { items = it }.onFailure { err = it.message } }
    Screen(tr("Evidence & reporting", "सबूत व रिपोर्टिंग"), back) {
        ErrorBox(err)
        if (items.isEmpty()) Text(tr("No saved evidence. After a suspicious call, tap 'Save evidence' in the report.",
            "कोई सबूत सेव नहीं है। संदिग्ध कॉल के बाद रिपोर्ट में 'सबूत सेव करें' दबाएं।"), color = VG.muted)
        items.forEach { e ->
            Section(Numbers.pretty(e.str("number")) + (e.str("claimed_name")?.let { " → “$it”" } ?: ""), Icons.Default.Folder, VG.level(e.str("level"))) {
                Kv(tr("Risk", "जोखिम"), "${e.int("risk_score") ?: "-"}/100 ${e.str("level") ?: ""}")
                Kv(tr("When", "कब"), e.str("created_at")?.take(16)?.replace('T', ' ') + " UTC")
                Kv(tr("Source", "स्रोत"), e.str("source"))
                SmallButton(tr("Open & report", "खोलें व रिपोर्ट करें")) { nav.navigate("evidence/${e.str("id")}") }
            }
        }
    }
}

@Composable
fun EvidenceDetailScreen(id: String, back: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var e by remember { mutableStateOf<JsonObject?>(null) }
    var ch by remember { mutableStateOf<JsonObject?>(null) }
    var msg by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(id) {
        e = runCatching { Api.get("/api/evidence/$id").asObj() }.getOrNull()
        ch = runCatching { Api.get("/api/evidence/$id/chakshu").asObj() }.getOrNull()
    }
    val clip = ctx.getSystemService(ClipboardManager::class.java)
    Screen(tr("Evidence", "सबूत") + " #$id", back) {
        val ev = e ?: run { Busy("…"); return@Screen }
        RiskGauge(ev.int("risk_score") ?: 0, ev.str("level"), 150.dp)
        Section(tr("Saved evidence", "सेव सबूत"), Icons.Default.Description) {
            Kv(tr("Caller", "कॉलर"), Numbers.pretty(ev.str("number")))
            Kv(tr("Pretended to be", "किसका नाटक"), ev.str("claimed_name"))
            Kv(tr("Audio saved", "ऑडियो सेव"), if (ev.bool("has_audio") == true) "✓" else "–")
            ev.str("transcript")?.let { Text("“$it”", color = VG.muted) }
        }
        Section(tr("Report it", "रिपोर्ट करें"), Icons.Default.LocalPolice, VG.red) {
            BigButton(tr("One-tap call 1930", "1930 पर एक-टैप कॉल"), Icons.Default.LocalPolice, VG.red) { dial(ctx, "1930") }
            SmallButton(tr("Open printable report", "प्रिंट योग्य रिपोर्ट खोलें"), Icons.Default.OpenInBrowser) { openUrl(ctx, Api.url("/api/evidence/$id/report.html")) }
            SmallButton(tr("Share report text", "रिपोर्ट शेयर करें"), Icons.Default.Share) { shareText(ctx, ch.str("complaint_text") ?: "") }
            SmallButton("cybercrime.gov.in", Icons.Default.OpenInBrowser) { openUrl(ctx, ch.str("cybercrime_url") ?: "https://cybercrime.gov.in/") }
        }
        ch?.let { c ->
            Section(tr("Report to Chakshu (Sanchar Saathi)", "चक्षु (संचार साथी) पर रिपोर्ट"), Icons.Default.Shield, VG.blue) {
                Text(tr("Chakshu has no public API, so VoiceGuard pre-fills the answers. Copy each one into the form.",
                    "चक्षु का कोई API नहीं है, इसलिए जवाब पहले से तैयार हैं। हर जवाब कॉपी करके फ़ॉर्म में डालें।"), color = VG.muted, fontSize = 13.sp)
                c.obj("fields")?.forEach { (k, v) ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("$k: ", fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                        Text(v.toString().trim('"').take(80), color = VG.muted, fontSize = 13.sp, modifier = Modifier.weight(1f), maxLines = 2)
                        IconButton({ clip.setPrimaryClip(ClipData.newPlainText(k, v.toString().trim('"'))); msg = tr("Copied $k", "$k कॉपी हुआ") }) {
                            Icon(Icons.Default.ContentCopy, null)
                        }
                    }
                }
                BigButton(tr("Open Chakshu", "चक्षु खोलें"), Icons.Default.OpenInBrowser, VG.blue) { openUrl(ctx, c.str("url") ?: "https://sancharsaathi.gov.in/sfc/") }
            }
        }
        Section(tr("Family Calls Cyber Cell", "परिवार साइबर सेल को कॉल करे"), Icons.Default.FamilyRestroom, VG.amber) {
            Text(tr("Ask a family member to report for you. They get the evidence and a one-tap 1930 button.",
                "परिवार के सदस्य से अपनी ओर से रिपोर्ट करवाएं।"), color = VG.muted, fontSize = 13.sp)
            SmallButton(tr("Ask family to report", "परिवार से रिपोर्ट करवाएं")) {
                scope.launch {
                    msg = runCatching { Api.post("/api/evidence/cyber-cell", json("user_id" to Prefs.userId, "evidence_id" to id)); tr("Family asked to report.", "परिवार को बता दिया।") }
                        .getOrElse { it.message }
                }
            }
        }
        msg?.let { Text(it, color = VG.green) }
    }
}

// ------------------------------------------------------------------ Future features (27-32)
@Composable
fun FutureLabScreen(back: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var auto by remember { mutableStateOf(Prefs.autoCheck) }
    var police by remember { mutableStateOf<JsonObject?>(null) }
    var policeStep by remember { mutableIntStateOf(-1) }
    var telNum by remember { mutableStateOf("+919000000101") }
    var tel by remember { mutableStateOf<JsonObject?>(null) }
    var shieldMsg by remember { mutableStateOf<String?>(null) }
    var shieldBusy by remember { mutableStateOf(false) }
    var fed by remember { mutableStateOf<JsonObject?>(null) }
    var amount by remember { mutableStateOf("50000") }
    var bank by remember { mutableStateOf<JsonObject?>(null) }
    var err by remember { mutableStateOf<String?>(null) }

    Screen(tr("Future lab", "भविष्य की सुविधाएँ"), back) {
        Text(tr("Working prototypes of the roadmap features.", "रोडमैप सुविधाओं के चालू प्रोटोटाइप।"), color = VG.muted)

        Section(tr("27 · Auto Check Every Call", "27 · हर कॉल की ऑटो जाँच"), Icons.Default.AutoMode, VG.green) {
            ToggleRow(tr("Check number + voice automatically", "नंबर व आवाज़ अपने आप जाँचें"), auto) { auto = it; Prefs.autoCheck = it }
            Text(tr("Number Info runs on every incoming call; voice checks run automatically on demo and HD calls. Full auto voice checks on normal calls need telecom-level access.",
                "हर कॉल पर नंबर जाँच होती है; डेमो व HD कॉल पर आवाज़ जाँच अपने आप।"), color = VG.muted, fontSize = 13.sp)
        }

        Section(tr("28 · Police Join the Call", "28 · पुलिस कॉल से जुड़े"), Icons.Default.LocalPolice, VG.red) {
            Text(tr("Simulation: a cyber-cell officer is bridged into the live call by the telecom.", "सिमुलेशन: साइबर सेल अधिकारी कॉल से जुड़ते हैं।"), color = VG.muted, fontSize = 13.sp)
            SmallButton(tr("Request police", "पुलिस बुलाएं")) {
                scope.launch {
                    runCatching { Api.post("/api/future/police-join", json("user_id" to Prefs.userId, "number" to Prefs.lastRiskyNumber)).asObj() }
                        .onSuccess { r -> police = r; for (i in r.objs("steps").indices) { policeStep = i; delay(2500) } }
                        .onFailure { err = it.message }
                }
            }
            police?.let { p ->
                Text(tr("Ticket ", "टिकट ") + p.str("ticket"), fontWeight = FontWeight.Bold)
                p.objs("steps").forEachIndexed { i, s -> if (i <= policeStep) Text((if (i == policeStep) "⏳ " else "✓ ") + s.bi("en", "hi")) }
            }
        }

        Section(tr("29 · Telecom Partnership", "29 · टेलीकॉम साझेदारी"), Icons.Default.CellTower, VG.blue) {
            Text(tr("What an operator would see before the phone even rings.", "फ़ोन बजने से पहले ऑपरेटर क्या देखेगा।"), color = VG.muted, fontSize = 13.sp)
            OutlinedTextField(telNum, { telNum = it }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SmallButton(tr("Network lookup", "नेटवर्क जाँच"), modifier = Modifier.weight(1f)) {
                    scope.launch { tel = runCatching { Api.get("/api/v1/telecom/lookup/${Uri.encode(telNum)}").asObj() }.getOrNull() }
                }
                SmallButton(tr("Flag as operator", "ऑपरेटर फ़्लैग"), modifier = Modifier.weight(1f)) {
                    scope.launch {
                        runCatching { Api.post("/api/v1/telecom/flag", json("number" to telNum, "reason" to "mass-calling pattern"), mapOf("X-API-Key" to "demo-telecom-key")) }
                        tel = runCatching { Api.get("/api/v1/telecom/lookup/${Uri.encode(telNum)}").asObj() }.getOrNull()
                    }
                }
            }
            tel?.let { Text(tr("Caller ID label: ", "कॉलर ID लेबल: ") + it.str("label") + " (${it.int("reports")} " + tr("reports)", "रिपोर्ट)"), fontWeight = FontWeight.SemiBold) }
        }

        Section(tr("30 · Voice Shield", "30 · वॉइस शील्ड"), Icons.Default.Shield, VG.violet) {
            Text(tr("Adds an inaudible watermark to your voice notes before you share them, so clones made from them can be traced back.",
                "शेयर करने से पहले आपके वॉइस नोट में न सुनाई देने वाला वॉटरमार्क जोड़ता है।"), color = VG.muted, fontSize = 13.sp)
            if (shieldBusy) Busy(tr("Recording 5 s & shielding…", "5 सेकंड रिकॉर्ड व शील्ड…"))
            else SmallButton(tr("Record & shield 5 s", "5 सेकंड रिकॉर्ड व शील्ड"), Icons.Default.Mic) {
                shieldBusy = true
                scope.launch {
                    runCatching {
                        val pcm = Recorder().record(5)
                        val wav = Api.uploadForBytes("/api/future/voice-shield/protect", mapOf("user_id" to Prefs.userId), Wav.encode(pcm))
                        val det = Api.upload("/api/future/voice-shield/detect", mapOf("user_id" to Prefs.userId), wav).asObj()
                        val f = File(ctx.cacheDir, "shielded.wav").apply { writeBytes(wav) }
                        val uri = FileProvider.getUriForFile(ctx, "${ctx.packageName}.files", f)
                        ctx.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("audio/wav").putExtra(Intent.EXTRA_STREAM, uri)
                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION), "Share shielded voice note").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                        shieldMsg = tr("Shielded ✓ watermark check z=", "शील्ड ✓ वॉटरमार्क z=") + det.num("z_score")
                    }.onFailure { err = it.message }
                    shieldBusy = false
                }
            }
            shieldMsg?.let { Text(it, color = VG.green) }
        }

        Section(tr("31 · Smart Learning (federated)", "31 · स्मार्ट लर्निंग (फ़ेडरेटेड)"), Icons.Default.Hub, VG.amber) {
            Text(tr("Phones learn together without sharing audio: only model weights are averaged.", "फ़ोन बिना आवाज़ शेयर किए साथ सीखते हैं।"), color = VG.muted, fontSize = 13.sp)
            SmallButton(tr("Run 8 learning rounds", "8 राउंड चलाएं")) { scope.launch { fed = runCatching { Api.get("/api/future/federated").asObj() }.getOrNull() } }
            fed?.let { f ->
                val acc = f.objs("rounds").mapNotNull { it.num("accuracy") }
                Canvas(Modifier.fillMaxWidth().height(90.dp)) {
                    if (acc.size > 1) for (i in 1 until acc.size) {
                        val x0 = size.width * (i - 1) / (acc.size - 1); val x1 = size.width * i / (acc.size - 1)
                        val y = { a: Double -> size.height * (1 - ((a - 0.5) / 0.5).toFloat().coerceIn(0f, 1f)) }
                        drawLine(VG.amber, Offset(x0, y(acc[i - 1])), Offset(x1, y(acc[i])), strokeWidth = 6f)
                    }
                }
                Text(tr("Shared model accuracy: ", "साझा मॉडल सटीकता: ") + "${((f.num("final_accuracy") ?: 0.0) * 100).toInt()}%", fontWeight = FontWeight.Bold)
                f.objs("clients").forEach { c -> Kv("📱 ${c.str("codec")}", tr("alone ", "अकेले ") + "${((c.num("local_only_accuracy") ?: 0.0) * 100).toInt()}%") }
                Text(f.bi("privacy", "privacy_hi").orEmpty(), color = VG.muted, fontSize = 12.sp)
            }
        }

        Section(tr("32 · Bank & Enterprise API", "32 · बैंक व एंटरप्राइज़ API"), Icons.Default.AccountBalance, VG.green) {
            Text(tr("Your bank asks VoiceGuard before releasing a payment: was this customer just on a risky call?",
                "बैंक पेमेंट से पहले VoiceGuard से पूछता है: क्या ग्राहक अभी ख़तरनाक कॉल पर था?"), color = VG.muted, fontSize = 13.sp)
            OutlinedTextField(amount, { amount = it }, label = { Text("₹") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            SmallButton(tr("Simulate UPI payment from my account", "मेरे खाते से UPI पेमेंट सिमुलेट करें")) {
                scope.launch {
                    bank = runCatching {
                        Api.post("/api/v1/enterprise/transaction-check", json("customer_phone" to Prefs.phone, "amount" to (amount.toDoubleOrNull() ?: 0.0),
                            "beneficiary" to "unknown-upi@ybl"), mapOf("X-API-Key" to "demo-bank-key")).asObj()
                    }.onFailure { err = it.message }.getOrNull()
                }
            }
            bank?.let { b ->
                val a = b.str("action")
                Banner(tr("Bank decision: ", "बैंक का फ़ैसला: ") + a?.uppercase(), when (a) { "hold" -> VG.red; "call_customer" -> VG.amber; else -> VG.green }, b.str("reason"))
            }
        }
        ErrorBox(err)
    }
}
