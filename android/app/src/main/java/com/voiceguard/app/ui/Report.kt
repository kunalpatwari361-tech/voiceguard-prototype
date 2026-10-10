package com.voiceguard.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.Fingerprint
import androidx.compose.material.icons.filled.FamilyRestroom
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.LocalPolice
import androidx.compose.material.icons.filled.ManageSearch
import androidx.compose.material.icons.filled.PersonSearch
import androidx.compose.material.icons.filled.Science
import androidx.compose.material.icons.filled.RecordVoiceOver
import androidx.compose.material.icons.filled.Save
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material.icons.filled.Subtitles
import androidx.compose.material.icons.filled.Timer
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.navigation.NavHostController
import com.voiceguard.app.audio.Wav
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.arr
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.bi
import com.voiceguard.app.data.bool
import com.voiceguard.app.data.int
import com.voiceguard.app.data.json
import com.voiceguard.app.data.num
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.objs
import com.voiceguard.app.data.str
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject

/** Everything needed to act on an analysis (save evidence, alert family, call back...). */
data class ReportCtx(val number: String?, val claimedId: String?, val source: String, val audio: ShortArray?)

@Composable
fun ReportScreen(nav: NavHostController, back: () -> Unit) {
    val r = Shared.report
    Screen(tr("Voice check report", "आवाज़ जाँच रिपोर्ट"), back) {
        if (r == null) Text("No report") else ReportView(r, Shared.reportCtx ?: ReportCtx(null, null, "voice_note", null), nav)
    }
}

/** Full analysis report: Final Risk Score (16) + every check that produced it. */
@Composable
fun ReportView(r: JsonObject, rc: ReportCtx, nav: NavHostController?, compact: Boolean = false) {
    if (r.bool("ok") == false) {
        ErrorBox(r.bi("message", "message_hi"))
        return
    }
    val risk = r.obj("risk")
    val score = risk.int("score") ?: 0
    val level = risk.str("level")
    LaunchedEffect(r) { if (level == "danger") Prefs.markRisky(rc.number, score, rc.claimedId) }

    Section(if (compact) null else tr("Final Risk Score", "अंतिम जोखिम स्कोर"), Icons.Default.ManageSearch, VG.level(level)) {
        RiskGauge(score, level)
        Text(risk.bi("advice", "advice_hi").orEmpty(), color = VG.level(level), fontWeight = FontWeight.SemiBold)
        risk.objs("reasons").filter { !it.str("en").isNullOrBlank() }.take(if (compact) 4 else 10).forEach { x ->
            Row(Modifier.fillMaxWidth()) {
                Text("• " + (if (Prefs.hindi) x.str("label_hi") else x.str("label")).orEmpty() + ": ", fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                Text(x.bi("en", "hi").orEmpty(), color = VG.muted, fontSize = 14.sp, modifier = Modifier.weight(1f))
            }
        }
    }
    r.obj("voice_id")?.takeIf { it.bool("ignored") != true }?.let { ScamVoiceCard(it, compact) }
    r.obj("reverse_engineering")?.let { ReverseVerdict(it, compact) }
    r.obj("voice_id_saved")?.takeIf { it.bool("saved") == true }?.let {
        Text("🔒 " + it.bi("en", "hi"), color = VG.violet, fontSize = 13.sp)
    }
    if (compact) return

    val ai = r.obj("ai_voice")
    Section(tr("AI Voice Detector", "AI आवाज़ जाँच"), Icons.Default.SmartToy, VG.score(ai.num("fake_prob"))) {
        ScoreRow(tr("Chance the voice is AI-made", "आवाज़ AI से बनी होने की संभावना"), ai.num("fake_prob"),
            tr("Model: ", "मॉडल: ") + ai.str("model"))
    }
    val fp = r.obj("fingerprints")
    Section(tr("Voice fingerprints (Reverse Engineering)", "आवाज़ के निशान"), Icons.Default.Fingerprint, VG.score(fp.num("score"))) {
        if (fp.bool("narrowband") == true) Text(tr("Phone-quality audio: using the checks that survive a phone line.",
            "फ़ोन-क्वालिटी ऑडियो: वही जाँचें जो फ़ोन लाइन में बचती हैं।"), color = VG.blue, fontSize = 13.sp)
        val feats = fp.obj("features")
        feats?.keys?.forEach { k ->
            val f = feats.obj(k)
            ScoreRow(f.str("label") ?: k, f.num("score"), f.bi("detail", "detail_hi"), f.str("note"))
        }
        if (feats.isNullOrEmpty()) Text(tr("Not enough speech for fingerprints.", "निशानों के लिए बोली कम है।"), color = VG.muted)
    }
    val st = r.obj("source_trace")
    Section(tr("Source Tracing", "स्रोत पहचान"), Icons.Default.GraphicEq, VG.violet) {
        Text(st.bi("label", "label_hi").orEmpty(), fontWeight = FontWeight.SemiBold)
        st.objs("candidates").take(5).forEach { c -> ScoreRow(c.bi("label", "label_hi").orEmpty(), c.num("prob"), null) }
        Text(st.str("note").orEmpty(), color = VG.muted, fontSize = 12.sp)
    }
    r.obj("voice_print")?.let { vp ->
        val v = vp.str("verdict")
        val c = when (v) { "match" -> VG.green; "uncertain" -> VG.amber; else -> VG.red }
        Section(tr("Voice Print Match", "वॉइस प्रिंट मिलान"), Icons.Default.RecordVoiceOver, c) {
            Text(when (v) {
                "match" -> tr("Matches ${vp.str("name")}'s saved voice", "${vp.str("name")} की सेव आवाज़ से मेल खाती है")
                "uncertain" -> tr("Only partly like ${vp.str("name")}", "${vp.str("name")} से थोड़ी ही मिलती है")
                else -> tr("Does NOT match ${vp.str("name")}", "${vp.str("name")} से मेल नहीं खाती")
            }, color = c, fontWeight = FontWeight.Bold)
            Kv(tr("Similarity", "समानता"), "%.2f".format(vp.num("similarity") ?: 0.0))
        }
    } ?: r.obj("claimed")?.let { cl ->
        if (cl.bool("voiceprint_enrolled") == false) Text(tr("${cl.str("name")} has no voice print yet – ask them to record one.",
            "${cl.str("name")} का वॉइस प्रिंट नहीं है – उनसे रिकॉर्ड करवाएं।"), color = VG.amber, fontSize = 13.sp)
    }
    val sw = r.obj("scam_words")
    val tr0 = r.obj("transcript")
    if (tr0 != null || sw != null) Section(tr("What the caller said · Scam Words", "कॉलर ने क्या कहा · ठगी के शब्द"), Icons.Default.Subtitles,
        VG.score(sw.num("score"))) {
        tr0?.str("text")?.let { Text("“$it”", fontSize = 15.sp) }
        if (sw != null) {
            ScoreRow(tr("Scam-talk score", "ठगी बातचीत स्कोर"), sw.num("score"), null)
            sw.obj("rules").objs("tips").forEach { Text("⚠ " + it.bi("en", "hi"), color = VG.amber, fontSize = 14.sp) }
            val hits = sw.obj("rules").objs("hits").mapNotNull { it.str("phrase") }.distinct()
            if (hits.isNotEmpty()) Text(tr("Words: ", "शब्द: ") + hits.joinToString(", "), color = VG.muted, fontSize = 13.sp)
            sw.obj("claude")?.let { c -> Text("Claude: " + c.bi("summary_en", "summary_hi"), color = VG.blue, fontSize = 14.sp) }
        }
    }
    r.obj("reply_delay")?.let { rd ->
        Section(tr("Reply Delay Check", "जवाब में देरी"), Icons.Default.Timer, VG.score(rd.num("score"))) {
            ScoreRow(tr("AI-agent timing", "AI एजेंट जैसा समय"), rd.num("score"), rd.bi("detail", "detail_hi"))
            rd.num("mean_s")?.let { Kv(tr("Average delay", "औसत देरी"), "%.2f s ± %.2f".format(it, rd.num("std_s") ?: 0.0)) }
        }
    }
    r.obj("number_info")?.let { NumberInfoCard(it) }
    Text(tr("Checked in ", "जाँच का समय ") + "${r.obj("timings_s").num("total") ?: "?"} s", color = VG.muted, fontSize = 12.sp)
    ReportActions(r, rc, nav)
}

@Composable
fun ReportActions(r: JsonObject, rc: ReportCtx, nav: NavHostController?) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var msg by remember { mutableStateOf<String?>(null) }
    val risk = r.obj("risk")
    val claimed = Sync.member(rc.claimedId)
    Section(tr("What to do now", "अब क्या करें"), Icons.Default.LocalPolice, VG.red) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SmallButton(tr("Save evidence", "सबूत सेव करें"), Icons.Default.Save, Modifier.weight(1f)) {
                scope.launch {
                    msg = runCatching {
                        val e = saveEvidence(r, rc)
                        tr("Evidence saved (#${e.str("id")}).", "सबूत सेव हुआ (#${e.str("id")})।")
                    }.getOrElse { it.message }
                }
            }
            SmallButton(tr("Alert family", "परिवार को बताएं"), Icons.Default.FamilyRestroom, Modifier.weight(1f)) {
                scope.launch {
                    msg = notifyFamily("${Prefs.name} got a suspected scam call",
                        "Risk ${risk.int("score")}/100 from ${rc.number ?: "unknown number"}" +
                            (claimed?.str("name")?.let { ", caller pretended to be $it" } ?: "") + ". Please call ${Prefs.name}.",
                        mapOf("number" to rc.number, "score" to risk.int("score"), "victim_phone" to Prefs.phone, "from_name" to Prefs.name)).second
                }
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (rc.number != null) SmallButton(tr("Block & report", "ब्लॉक व रिपोर्ट"), Icons.Default.Block, Modifier.weight(1f)) {
                scope.launch {
                    msg = runCatching {
                        Api.post("/api/blocked", json("user_id" to Prefs.userId, "number" to rc.number))
                        val res = Api.post("/api/scamlist/report", json("number" to rc.number, "reporter_id" to Prefs.userId,
                            "reason" to "VoiceGuard risk ${risk.int("score")}/100" + (claimed?.str("name")?.let { ", impersonated $it" } ?: ""))).asObj()
                        tr("Blocked and reported.", "ब्लॉक और रिपोर्ट हो गया।") +
                            (res.obj("voice_id")?.bi("en", "hi")?.let { "\n" + it } ?: "")
                    }.getOrElse { it.message }
                }
            }
            SmallButton(tr("Call 1930", "1930 कॉल"), Icons.Default.LocalPolice, Modifier.weight(1f)) { dial(ctx, "1930") }
        }
        if (claimed != null) BigButton(tr("Call ${claimed.str("name")} on saved number", "${claimed.str("name")} को सेव नंबर पर कॉल करें"),
            Icons.Default.Call, VG.blue) { dial(ctx, claimed.str("phone") ?: return@BigButton) }
        msg?.let { Text(it, color = VG.green) }
        if (nav != null) SmallButton(tr("Open evidence & reporting", "सबूत व रिपोर्टिंग खोलें")) { nav.navigate("evidence") }
    }
}

suspend fun saveEvidence(r: JsonObject, rc: ReportCtx): JsonObject {
    val risk = r.obj("risk")
    return Api.upload("/api/evidence", mapOf(
        "user_id" to Prefs.userId, "number" to rc.number, "claimed_name" to Sync.member(rc.claimedId)?.str("name"),
        "source" to rc.source, "risk_score" to risk.int("score"), "level" to risk.str("level"),
        "transcript" to r.obj("transcript").str("text"), "report_json" to r.toString(),
    ), rc.audio?.let { Wav.encode(it) }) as JsonObject
}

/** Reverse Engineering verdict (4): every model's vote combined into "what made this voice". */
@Composable
fun ReverseVerdict(rev: JsonObject, compact: Boolean) {
    val kind = rev.str("kind")
    val c = when (kind) { "human" -> VG.green; "unclear" -> VG.amber; else -> VG.red }
    if (compact) {
        if (kind != "human") Text(tr("Voice: ", "आवाज़: ") + rev.bi("label", "label_hi"), color = c,
            fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
        return
    }
    Section(tr("Reverse Engineering: what made this voice", "रिवर्स इंजीनियरिंग: यह आवाज़ किसने बनाई"), Icons.Default.Science, c) {
        Text(rev.bi("label", "label_hi").orEmpty(), color = c, fontWeight = FontWeight.Bold, fontSize = 17.sp)
        Text(rev.bi("meaning", "meaning_hi").orEmpty(), fontSize = 14.sp)
        rev.objs("evidence").forEach { Text("• " + it.bi("en", "hi"), color = VG.muted, fontSize = 13.sp) }
        Kv(tr("Confidence", "भरोसा"), "${((rev.num("confidence") ?: 0.0) * 100).toInt()}%")
        Text(tr("Models combined: ", "मिलाए गए मॉडल: ") + rev.arr("models").mapNotNull { it.str() }.joinToString(" + "),
            color = VG.muted, fontSize = 12.sp)
    }
}

/** Scam Voice ID: this caller's voice was already confirmed as a scammer's - even if the number is new. */
@Composable
fun ScamVoiceCard(v: JsonObject, compact: Boolean) {
    val nums = v.arr("numbers").mapNotNull { it.str() }
    val calls = v.int("calls") ?: 0
    if (compact) {
        Text(tr("⚠ Known scam voice ${v.str("id")}: heard in $calls reported scam call(s)",
            "⚠ पहचानी हुई ठग की आवाज़ ${v.str("id")}: $calls रिपोर्ट हुई ठगी कॉल में"), color = VG.red,
            fontWeight = FontWeight.Bold, fontSize = 14.sp)
        return
    }
    Section(tr("Known scam voice · ${v.str("id")}", "पहचानी हुई ठग की आवाज़ · ${v.str("id")}"), Icons.Default.PersonSearch, VG.red) {
        Text(tr("This voice was already reported in $calls scam call(s) from ${nums.size} number(s).",
            "यह आवाज़ पहले $calls ठगी कॉल में रिपोर्ट हो चुकी है (${nums.size} नंबर)।"), color = VG.red, fontWeight = FontWeight.Bold)
        if (nums.isNotEmpty()) Kv(tr("Numbers used", "इस्तेमाल हुए नंबर"), nums.take(4).joinToString(", ") + if (nums.size > 4) " …" else "")
        v.str("clone_of")?.let { Kv(tr("Imitated", "नकल की"), it) }
        Kv(tr("Voice similarity", "आवाज़ समानता"), "%.2f".format(v.num("similarity") ?: 0.0))
        Text(tr("Scammers change numbers, not voices. Do not trust this caller.",
            "ठग नंबर बदलते हैं, आवाज़ नहीं। इस कॉलर पर भरोसा न करें।"), color = VG.muted, fontSize = 13.sp)
    }
}
