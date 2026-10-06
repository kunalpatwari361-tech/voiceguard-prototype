package com.voiceguard.app.ui

import android.media.AudioAttributes
import android.media.MediaPlayer
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.CallEnd
import androidx.compose.material.icons.filled.PlayCircle
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
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
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.objs
import com.voiceguard.app.data.str
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.json.JsonObject
import java.io.File
import kotlin.coroutines.resume
import kotlin.random.Random

private var clipsCache: List<JsonObject> = emptyList()

@Composable
fun DemoListScreen(nav: NavHostController, back: () -> Unit) {
    var clips by remember { mutableStateOf(clipsCache) }
    var err by remember { mutableStateOf<String?>(null) }
    var claimed by remember { mutableStateOf(Sync.others().firstOrNull()?.str("id").orEmpty()) }
    var phone by remember { mutableStateOf(true) }
    LaunchedEffect(Unit) {
        runCatching { Api.get("/api/demo/clips").asList() }.onSuccess { clips = it; clipsCache = it }.onFailure { err = it.message }
    }
    Screen(tr("Demo scam call", "डेमो स्कैम कॉल"), back) {
        Text(tr("Plays a realistic scam call made with a real AI voice generator, so you can practise using VoiceGuard safely. Answer, talk back, and use the tools.",
            "असली AI आवाज़ से बनी एक ठगी कॉल चलाता है ताकि आप सुरक्षित तरीके से अभ्यास कर सकें।"), color = VG.muted)
        Section(tr("The fake caller pretends to be…", "नकली कॉलर दावा करेगा कि वह है…")) {
            LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                items(Sync.others()) { m -> FilterChip(claimed == m.str("id"), { claimed = m.str("id").orEmpty() }, label = { Text(m.str("name").orEmpty()) }) }
            }
            ToggleRow(tr("Phone-line quality (8 kHz, like a real call)", "फ़ोन-लाइन क्वालिटी (8 kHz)"), phone) { phone = it }
        }
        ErrorBox(err)
        clips.forEach { c ->
            Section(c.bi("title", "title_hi"), Icons.Default.PlayCircle, VG.violet) {
                Kv(tr("Caller number", "कॉलर नंबर"), Numbers.pretty(c.str("number")))
                Kv(tr("Voice made by", "आवाज़ बनाई"), c.str("generator"))
                BigButton(tr("Start demo call", "डेमो कॉल शुरू करें"), Icons.Default.Call, VG.violet) {
                    DemoSession.end()
                    nav.navigate("democall/${c.str("id")}?claimed=${if (c.str("relation") == null) "" else claimed}&phone=$phone")
                }
            }
        }
    }
}

private suspend fun fetchClip(ctx: android.content.Context, name: String): File {
    val f = File(ctx.cacheDir, "demo_$name")
    if (!f.exists()) f.writeBytes(Api.bytes("/demo/$name"))
    return f
}

private suspend fun play(file: File): Unit = suspendCancellableCoroutine { cont ->
    val mp = MediaPlayer()
    mp.setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
    mp.setDataSource(file.absolutePath)
    mp.setOnCompletionListener { it.release(); if (cont.isActive) cont.resume(Unit) }
    mp.setOnErrorListener { p, _, _ -> p.release(); if (cont.isActive) cont.resume(Unit); true }
    mp.prepare()
    mp.start()
    cont.invokeOnCancellation { runCatching { mp.stop(); mp.release() } }
}


/** Demo call state lives outside the screen so opening the full report does not restart the call. */
class DemoState(val id: String, claimedId: String, number: String?) {
    val tools = CallToolsState(number, claimedId, "demo_call")
    var phase by mutableStateOf("ringing")
    val said = mutableStateListOf<String>()
    var yourTurn by mutableStateOf(false)
    var level by mutableDoubleStateOf(-96.0)
    var startedAt by mutableLongStateOf(0L)
    var job: Job? = null
    val pcm = ArrayList<Short>()
}

object DemoSession {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    var current: DemoState? = null

    fun get(id: String, claimedId: String, number: String?): DemoState =
        current?.takeIf { it.id == id } ?: DemoState(id, claimedId, number).also { current = it }

    fun end() {
        current?.job?.cancel()
        current = null
    }
}

@Composable
fun DemoCallScreen(id: String, claimedId: String, phoneQuality: Boolean, nav: NavHostController, back: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val clip = remember { clipsCache.firstOrNull { it.str("id") == id } }
    val number = clip.str("number")
    val st = remember { DemoSession.get(id, claimedId, number) }
    val tools = st.tools
    var info by remember { mutableStateOf<JsonObject?>(null) }
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    val variant = if (phoneQuality) "phone" else "hd"
    val leave = { DemoSession.end(); back() }
    BackHandler { leave() }

    LaunchedEffect(Unit) {
        if (Prefs.autoCheck) info = runCatching { Api.get("/api/numbers/${number}?user_id=${Prefs.userId}").asObj() }.getOrNull()
    }
    LaunchedEffect(st.phase) { while (st.phase == "active") { now = System.currentTimeMillis(); delay(1000) } }

    /** Wait for the user to say something back; returns when they stopped talking (ms) or null. */
    suspend fun listenForReply(): Long? {
        val rec = Recorder()
        var spokeAt = 0L
        var lastLoud = 0L
        val t0 = System.currentTimeMillis()
        st.yourTurn = true
        rec.record(9) { db ->
            st.level = db
            val t = System.currentTimeMillis()
            if (db > -38) { if (spokeAt == 0L) spokeAt = t; lastLoud = t }
            if (spokeAt != 0L && t - lastLoud > 700) rec.stop()
            if (spokeAt == 0L && t - t0 > 6000) rec.stop()
        }
        st.yourTurn = false
        return if (spokeAt != 0L) lastLoud else null
    }

    fun startConversation() {
        st.phase = "active"; st.startedAt = System.currentTimeMillis()
        st.job = DemoSession.scope.launch {
            val turns = clip.objs("turns")
            for ((i, t) in turns.withIndex()) {
                if (!isActive) break
                if (i > 0) {
                    val endedAt = listenForReply()
                    delay(1250L + Random.nextLong(0, 250))   // the AI pipeline: speech-to-text -> LLM -> TTS
                    if (endedAt != null) tools.gaps.add((System.currentTimeMillis() - endedAt) / 1000.0)
                }
                val f = runCatching { fetchClip(ctx, t.str(variant)!!) }.getOrNull() ?: continue
                st.said.add(t.str("text").orEmpty())
                Wav.decode(f.readBytes()).first.forEach { st.pcm.add(it) }
                tools.audio = st.pcm.toShortArray()
                play(f)
                if (Prefs.autoCheck && i == 1) launch { tools.analyze() }   // Auto Check Every Call (27)
            }
            if (Prefs.autoCheck) tools.analyze()
        }
    }

    suspend fun demoAnswer(kind: String): ShortArray? {
        val name = clip.obj("challenge_responses").obj(kind).str(variant) ?: return null
        val f = fetchClip(ctx, name)
        delay(1300)
        play(f)
        return Wav.decode(f.readBytes()).first
    }

    Screen(tr("Demo call", "डेमो कॉल"), leave) {
        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(VG.surface).padding(18.dp),
            horizontalAlignment = Alignment.CenterHorizontally) {
            Text(when (st.phase) { "ringing" -> tr("Incoming call", "इनकमिंग कॉल"); "active" -> tr("On call", "कॉल चालू"); else -> tr("Call ended", "कॉल ख़त्म") },
                color = VG.muted)
            Text(Numbers.pretty(number), fontSize = 26.sp, fontWeight = FontWeight.Bold)
            Text(tr("Unknown number · demo AI-cloned voice", "अनजान नंबर · डेमो AI आवाज़"), color = VG.muted, fontSize = 13.sp)
            if (st.phase == "active") Text("%02d:%02d".format((now - st.startedAt) / 60000, (now - st.startedAt) / 1000 % 60), color = VG.green)
        }
        info?.let { NumberInfoCard(it) }
        when (st.phase) {
            "ringing" -> Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                BigButton(tr("Decline", "काटें"), Icons.Default.CallEnd, VG.red, modifier = Modifier.weight(1f)) { leave() }
                BigButton(tr("Answer", "उठाएं"), Icons.Default.Call, VG.green, modifier = Modifier.weight(1f)) { startConversation() }
            }
            "active" -> {
                st.said.forEach { Text("🗣 “$it”", modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(VG.surface2).padding(10.dp)) }
                if (st.yourTurn) { Text(tr("Your turn – answer the caller out loud", "आपकी बारी – कॉलर को जवाब दें"), color = VG.green); LevelMeter(st.level) }
                BigButton(tr("End call", "कॉल काटें"), Icons.Default.CallEnd, VG.red) {
                    st.job?.cancel(); st.phase = "ended"
                    if (tools.analysis == null && tools.audio != null) scope.launch { tools.analyze() }
                }
            }
            else -> {
                val name = tools.claimedName
                if (name != null) Banner(tr("Call-Back Alert: call $name on their saved number", "कॉल-बैक अलर्ट: $name को सेव नंबर पर कॉल करें"), VG.blue,
                    tr("Never trust a new number. The real $name will pick up their own phone.", "नए नंबर पर भरोसा न करें।"))
                if (name != null) BigButton(tr("Call $name now", "$name को अभी कॉल करें"), Icons.Default.Call, VG.blue) {
                    dial(ctx, Sync.member(tools.claimedId).str("phone") ?: return@BigButton)
                }
            }
        }
        if (st.phase != "ringing") CallTools(tools, nav) { kind -> demoAnswer(kind) }
        if (tools.gaps.isNotEmpty()) Text(tr("Reply delays measured: ", "मापी गई जवाब देरी: ") + tools.gaps.joinToString { "%.1fs".format(it) },
            color = VG.muted, fontSize = 12.sp, textAlign = TextAlign.Start)
    }
}
