package com.voiceguard.app.ui

import android.content.Context
import android.content.Intent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.HdrOn
import androidx.compose.material.icons.filled.FamilyRestroom
import androidx.compose.material.icons.filled.HelpCenter
import androidx.compose.material.icons.filled.ManageSearch
import androidx.compose.material.icons.filled.PanTool
import androidx.compose.material.icons.filled.Quiz
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
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
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.bi
import com.voiceguard.app.data.bool
import com.voiceguard.app.data.int
import com.voiceguard.app.data.json
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.objs
import com.voiceguard.app.data.str
import com.voiceguard.app.data.toJsonElement
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject

/**
 * The scam tools available during any call (real, demo, HD): Are You Really Calling? (8),
 * Voice Test (12), VoiceGuard HD Call (11), Final Risk Score (16), Panic Pause (19), Family Alert (17).
 */
class CallToolsState(val number: String?, claimedId: String?, val source: String) {
    var claimedId by mutableStateOf(claimedId?.takeIf { it.isNotBlank() } ?: Sync.others().firstOrNull()?.str("id"))
    var really by mutableStateOf<JsonObject?>(null)
    var reallyBusy by mutableStateOf(false)
    var challenge by mutableStateOf<JsonObject?>(null)
    var challengeResult by mutableStateOf<JsonObject?>(null)
    var testBusy by mutableStateOf<String?>(null)
    var analysis by mutableStateOf<JsonObject?>(null)
    var analyzing by mutableStateOf(false)
    var err by mutableStateOf<String?>(null)
    /** Why the last check gave no score (e.g. only the phone owner's own voice was heard). */
    var note by mutableStateOf<String?>(null)
    val gaps = mutableListOf<Double>()
    var audio: ShortArray? = null

    val claimedName get() = Sync.member(claimedId)?.str("name")

    suspend fun askReallyCalling() {
        val cid = claimedId ?: run { err = tr("Pick who the caller claims to be.", "चुनें कि कॉलर कौन होने का दावा कर रहा है।"); return }
        reallyBusy = true; err = null
        runCatching { Api.post("/api/verify/ask", json("asker_id" to Prefs.userId, "claimed_user_id" to cid, "number" to number, "wait_s" to 30)).asObj() }
            .onSuccess {
                really = it
                if (it.str("answer") == "no") Prefs.markRisky(number, 96, cid)
            }.onFailure { err = it.message }
        reallyBusy = false
        if (audio != null) analyze()
    }

    var phase by mutableStateOf("")

    /** Quick pass first (voice checks, ~5 s), then the full pass with speech-to-text + scam words. */
    private var rerun = false

    /** [withWords] = false skips speech-to-text (fast, used every few seconds by the live call check). */
    suspend fun analyze(withWords: Boolean = true) {
        val a = audio ?: run { err = tr("No caller audio yet.", "अभी कॉलर की आवाज़ नहीं है।"); return }
        if (analyzing) { if (withWords) rerun = true; return }   // newer audio/signals arrived: run again when this pass ends
        analyzing = true; err = null
        val wav = Wav.encode(a)
        val fields = mapOf(
            "user_id" to Prefs.userId, "claimed_user_id" to claimedId, "number" to number, "source" to source,
            "reply_gaps" to if (gaps.size >= 3) gaps.toJsonElement().toString() else null,
            "really_calling" to really.str("answer"), "voice_test_passed" to challengeResult.bool("passed"),
        )
        phase = tr("AI is checking the voice…", "AI आवाज़ जाँच रहा है…")
        fun take(r: JsonObject) {
            val ok = r.bool("ok") != false
            note = if (ok) null else r.bi("message", "message_hi")
            if (ok || analysis == null) analysis = r
        }
        runCatching { Api.upload("/api/analyze", fields + ("skip_asr" to true), wav).asObj() }
            .onSuccess { it?.let(::take) }.onFailure { err = it.message }
        if (withWords && analysis?.bool("ok") != false) {
            phase = tr("Listening to what the caller said (scam words)…", "कॉलर की बातें सुन रहे हैं (ठगी शब्द)…")
            runCatching { Api.upload("/api/analyze", fields, wav).asObj() }
                .onSuccess { it?.let(::take) }.onFailure { err = it.message }
        }
        analyzing = false
        if (rerun) { rerun = false; analyze() }
    }

    suspend fun newChallenge() {
        challengeResult = null
        runCatching { Api.get("/api/challenge/new").asObj() }.onSuccess { challenge = it }.onFailure { err = it.message }
    }

    suspend fun checkChallenge(pcm: ShortArray) {
        val c = challenge ?: return
        testBusy = tr("Checking the answer…", "जवाब जाँच रहे हैं…")
        runCatching { Api.upload("/api/challenge/${c.str("id")}/verify", mapOf("claimed_user_id" to claimedId), Wav.encode(pcm)).asObj() }
            .onSuccess { challengeResult = it }.onFailure { err = it.message }
        testBusy = null
    }
}

/**
 * Every call tool in one scrolling list (demo calls). The real call screen shows the same pieces behind icons.
 * [captureAnswer] records (real call) or plays + returns (demo call) the caller's answer to a Voice Test.
 */
@Composable
fun CallTools(s: CallToolsState, nav: NavHostController?, showNotify: Boolean = true, captureAnswer: suspend (kind: String) -> ShortArray?) {
    ReallyCallingSection(s)
    if (showNotify) TellFamilySection(s)
    VoiceTestSection(s, captureAnswer)
    HdCallSection(s, nav)
    RiskSection(s, nav)
    PanicButton(s)
}

/** Who the caller claims to be (family member) – used by Are You Really Calling?, voice print and HD call. */
@Composable
fun ClaimedPicker(s: CallToolsState) {
    val members = Sync.others()
    Text(tr("Caller claims to be…", "कॉलर दावा करता है कि वह है…"), color = VG.muted, fontSize = 13.sp)
    if (members.isEmpty()) Text(tr("Add family in Family Circle first.", "पहले परिवार सर्कल में सदस्य जोड़ें।"), color = VG.muted)
    LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        items(members) { m -> FilterChip(s.claimedId == m.str("id"), { s.claimedId = m.str("id") }, label = { Text(m.str("name").orEmpty()) }) }
    }
}

/** Are You Really Calling? (8): asks the claimed person's own phone. */
@Composable
fun ReallyCallingSection(s: CallToolsState) {
    val scope = rememberCoroutineScope()
    Section(tr("Are You Really Calling?", "क्या सच में आप कॉल कर रहे हैं?"), Icons.Default.HelpCenter, VG.green) {
        ClaimedPicker(s)
        val who = s.claimedName
        Text(if (who != null) tr("Asks $who's own phone. No audio needed.", "$who के फ़ोन से सीधे पूछता है।")
             else tr("Pick who the caller claims to be – VoiceGuard asks that person's own phone.", "चुनें कि कॉलर कौन होने का दावा कर रहा है – VoiceGuard उसी के फ़ोन से पूछेगा।"),
            color = VG.muted, fontSize = 13.sp)
        if (s.reallyBusy) Busy(tr("Asking $who's phone…", "$who के फ़ोन से पूछ रहे हैं…"))
        else BigButton(if (who != null) tr("Ask $who now", "$who से अभी पूछें") else tr("Ask now", "अभी पूछें"), color = VG.green,
            enabled = who != null) { scope.launch { s.askReallyCalling() } }
        s.really?.let { r ->
            val ans = r.str("answer")
            Banner(r.bi("message", "message_hi").orEmpty(), when (ans) { "yes" -> VG.green; "no" -> VG.red; else -> VG.amber },
                r.obj("auto")?.bi("en", "hi"))
        }
    }
}

/** Family Alert (17) on demand during the call: app alert, SMS or WhatsApp. */
@Composable
fun TellFamilySection(s: CallToolsState) {
    val ctx = LocalContext.current
    TellFamilyBar(message = {
        FamilyMessage.text(ctx, s.number, s.claimedName, s.analysis.obj("risk").int("score"))
    }) {
        notifyFamily("${Prefs.name} is on a suspicious call",
            "Caller ${s.number ?: "unknown"}" + (s.claimedName?.let { " says they are $it" } ?: "") +
                (s.analysis.obj("risk").int("score")?.let { ". Risk $it/100" } ?: "") + ". Call ${Prefs.name} now.",
            mapOf("number" to s.number, "victim_phone" to Prefs.phone, "from_name" to Prefs.name))
    }
}

/** Voice Test / Voice CAPTCHA (12). */
@Composable
fun VoiceTestSection(s: CallToolsState, captureAnswer: suspend (kind: String) -> ShortArray?) {
    val scope = rememberCoroutineScope()
    Section(tr("Voice Test (Voice CAPTCHA)", "वॉइस टेस्ट"), Icons.Default.Quiz, VG.violet) {
        Text(tr("Ask the caller to do something an AI voice finds hard (laugh, sing, answer a family question) – the AI checks the answer.",
            "कॉलर से कुछ ऐसा करवाएं जो AI आवाज़ के लिए मुश्किल हो – AI जवाब जाँचेगा।"), color = VG.muted, fontSize = 13.sp)
        val c = s.challenge
        if (c == null) SmallButton(tr("Give the caller a test", "कॉलर को टेस्ट दें")) { scope.launch { s.newChallenge() } }
        else {
            Text(c.bi("en", "hi").orEmpty(), fontWeight = FontWeight.Bold, fontSize = 17.sp, color = VG.violet)
            if (s.testBusy != null) Busy(s.testBusy!!)
            else Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SmallButton(tr("Check caller's answer", "कॉलर का जवाब जाँचें"), modifier = Modifier.weight(1f)) {
                    scope.launch {
                        s.testBusy = tr("Listening to the answer…", "जवाब सुन रहे हैं…")
                        val pcm = captureAnswer(c.str("kind").orEmpty())
                        if (pcm != null) s.checkChallenge(pcm) else s.testBusy = null
                    }
                }
                SmallButton(tr("Other test", "दूसरा टेस्ट")) { scope.launch { s.newChallenge() } }
            }
            s.challengeResult?.let { r ->
                val ok = r.bool("passed") == true
                Banner(if (ok) tr("Passed: natural human response", "पास: इंसानी जवाब") else tr("FAILED the Voice Test", "वॉइस टेस्ट में फेल"),
                    if (ok) VG.green else VG.red, r.objs("reasons").joinToString(" ") { it.bi("en", "hi").orEmpty() })
                r.obj("task").str("heard")?.let { Text(tr("Heard: ", "सुना: ") + "“$it”", color = VG.muted, fontSize = 13.sp) }
            }
        }
    }
}

/** VoiceGuard HD Call (11). Without [nav] (real call screen) it opens the app's HD call screen. */
@Composable
fun HdCallSection(s: CallToolsState, nav: NavHostController?) {
    val ctx = LocalContext.current
    Section(tr("VoiceGuard HD Call", "VoiceGuard HD कॉल"), Icons.Default.HdrOn, VG.blue) {
        Text(tr("Calls ${s.claimedName ?: "them"}'s registered phone over the internet in HD. Only the real person can answer. If the caller refuses to switch, that is a warning sign.",
            "${s.claimedName ?: "उनके"} रजिस्टर्ड फ़ोन पर HD कॉल करता है। असली व्यक्ति ही जवाब दे सकता है।"), color = VG.muted, fontSize = 13.sp)
        SmallButton(tr("Verify with HD call", "HD कॉल से पुष्टि करें"), Icons.Default.HdrOn, enabled = s.claimedId != null) {
            if (nav != null) nav.navigate("hdcall/${s.claimedId}")
            else ctx.startActivity(Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                .putExtra("nav", "hdcall").putExtra("peer_id", s.claimedId))
        }
    }
}

/** Final Risk Score (16) with the reasons. */
@Composable
fun RiskSection(s: CallToolsState, nav: NavHostController?) {
    val scope = rememberCoroutineScope()
    Section(tr("Final Risk Score", "अंतिम जोखिम स्कोर"), Icons.Default.ManageSearch) {
        if (s.analyzing) Busy(s.phase)
        else SmallButton(tr("Check voice now", "अभी आवाज़ जाँचें"), enabled = s.audio != null) { scope.launch { s.analyze() } }
        s.analysis?.let { a ->
            ReportView(a, ReportCtx(s.number, s.claimedId, s.source, s.audio), nav, compact = true)
            if (nav != null) SmallButton(tr("Full report & actions", "पूरी रिपोर्ट व कार्रवाई")) {
                Shared.report = a; Shared.reportCtx = ReportCtx(s.number, s.claimedId, s.source, s.audio); nav.navigate("report")
            }
        }
        ErrorBox(s.err)
    }
}

/** Panic (19): alert the family and open Panic Pause. */
fun panic(ctx: Context, s: CallToolsState) {
    Prefs.markRisky(s.number, (s.analysis.obj("risk").int("score") ?: 80), s.claimedId)
    kotlinx.coroutines.MainScope().launch {
        runCatching {
            Api.post("/api/alerts", json("from_user_id" to Prefs.userId, "kind" to "panic",
                "title" to "${Prefs.name} pressed PANIC during a call",
                "body" to "Caller ${s.number ?: "unknown"}" + (s.claimedName?.let { " claims to be $it" } ?: "") + ". Call ${Prefs.name} now!"))
        }
    }
    ctx.startActivity(Intent(ctx, PanicPauseActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).putExtra("app", "panic button"))
}

@Composable
fun PanicButton(s: CallToolsState) {
    val ctx = LocalContext.current
    BigButton(tr("I feel pressured – PANIC", "मुझ पर दबाव है – पैनिक"), Icons.Default.PanTool, VG.red) { panic(ctx, s) }
}

/**
 * Family Alert (17): push an alert to the whole family circle. Returns (delivered, message for the user),
 * e.g. "Sent to 2 family members (1 online now)" or how to add family when the circle is empty.
 */
suspend fun notifyFamily(title: String, body: String, payload: Map<String, Any?> = emptyMap()): Pair<Boolean, String> {
    if (Prefs.familyId == null) return false to tr("You are not in a family circle yet. Open Family Circle to create or join one.",
        "आप अभी किसी परिवार सर्कल में नहीं हैं। परिवार सर्कल खोलकर बनाएं या जुड़ें।")
    return runCatching {
        val r = Api.post("/api/alerts", json("from_user_id" to Prefs.userId, "kind" to "scam_call", "title" to title,
            "body" to body, "payload" to payload)).asObj()
        val sent = r.int("sent_to") ?: 0
        val online = r.int("online") ?: 0
        val code = Sync.cachedFamily().str("invite_code") ?: "—"
        if (sent == 0) false to tr("Nobody else is in your family circle yet, so no one was notified. On a family member's phone: install VoiceGuard → Join → code $code.",
            "आपके परिवार सर्कल में अभी कोई और नहीं है, इसलिए किसी को सूचना नहीं गई। परिवार के फ़ोन पर: VoiceGuard → जुड़ें → कोड $code।")
        else true to tr("Sent to $sent family member(s) – $online online now.", "$sent परिवार सदस्य(ों) को भेजा – $online अभी ऑनलाइन।")
    }.getOrElse { false to (it.message ?: "Could not send") }
}
