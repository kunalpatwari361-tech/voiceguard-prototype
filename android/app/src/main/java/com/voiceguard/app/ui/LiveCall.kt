package com.voiceguard.app.ui

import android.content.Context
import android.media.AudioManager
import android.media.MediaRecorder
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Accessibility
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import com.voiceguard.app.audio.Recorder
import com.voiceguard.app.data.bi
import com.voiceguard.app.data.bool
import com.voiceguard.app.data.int
import com.voiceguard.app.data.num
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.objs
import com.voiceguard.app.data.str
import com.voiceguard.app.service.CallListenService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.math.abs

/**
 * Live Call Check (feature 6) + real-time Reverse Engineering (feature 4) on a normal phone call.
 * Android does not give apps the call's audio stream, so the call goes on speaker and the microphone listens in
 * 6-second windows. Every window gets a fast check (AI voice, fingerprints, voice print); every third window also
 * runs speech-to-text for scam words. The server cuts out the phone owner's own voice (voice print) first.
 * During calls Android mutes the microphone for normal apps – "VoiceGuard call listening" (an accessibility
 * service) is the exception, so without it the check may hear only silence; we detect that and say so.
 */
class LiveCallMonitor(private val tools: CallToolsState) {
    var running by mutableStateOf(false)
    var blocked by mutableStateOf(false)
    var hearing by mutableStateOf(false)
    var level by mutableDoubleStateOf(-96.0)
    var windows by mutableIntStateOf(0)
    var status by mutableStateOf("")
    private var job: Job? = null
    private var recorder: Recorder? = null

    private fun sources(ctx: Context): List<Int> = buildList {
        add(MediaRecorder.AudioSource.VOICE_RECOGNITION)   // the one Android allows for accessibility apps in a call
        add(MediaRecorder.AudioSource.MIC)
        val am = ctx.getSystemService(AudioManager::class.java)
        if (am.getProperty(AudioManager.PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED) == "true") add(MediaRecorder.AudioSource.UNPROCESSED)
        add(MediaRecorder.AudioSource.CAMCORDER)
    }

    fun start(ctx: Context, scope: CoroutineScope, ensureSpeaker: () -> Unit) {
        if (running) return
        running = true; blocked = false; hearing = false
        ensureSpeaker()
        job = scope.launch {
            var buf = ShortArray(0)
            val srcs = sources(ctx)
            var src = 0
            var silentInARow = 0
            while (isActive && running) {
                status = if (blocked) tr("Android is muting the microphone – retrying…", "Android माइक बंद कर रहा है – फिर कोशिश…")
                         else tr("Listening to the caller…", "कॉलर को सुन रहे हैं…")
                val rec = Recorder(srcs[src]).also { recorder = it }
                val pcm = runCatching { rec.record(6) { level = it } }.getOrNull() ?: ShortArray(0)
                if (!running) break
                val peak = pcm.maxOfOrNull { abs(it.toInt()) } ?: 0
                if (pcm.isEmpty() || rec.allSilent || peak < 60) {
                    hearing = false
                    silentInARow++
                    src = (src + 1) % srcs.size                    // try the next microphone path
                    if (silentInARow >= srcs.size) blocked = true  // every path gave silence: Android blocks it
                    continue
                }
                silentInARow = 0; blocked = false; hearing = true
                // keep the last 30 s (array copy off the main thread – the old list version froze after 30 s)
                buf = withContext(Dispatchers.Default) {
                    val joined = buf + pcm
                    if (joined.size > MAX) joined.copyOfRange(joined.size - MAX, joined.size) else joined
                }
                tools.audio = buf
                windows++
                status = tr("Checked $windows times · listening…", "$windows बार जाँचा · सुन रहे हैं…")
                launch { tools.analyze(withWords = windows % 3 == 0) }
            }
            running = false
        }
    }

    fun stop() {
        running = false
        recorder?.stop()
        job?.cancel()
    }

    companion object {
        const val MAX = 16000 * 30
    }
}

/** The live AI check, at the top of the call screen: score, verdict, what the AI heard, start/stop and fixes. */
@Composable
fun LiveRiskPanel(m: LiveCallMonitor, tools: CallToolsState, onStart: () -> Unit) {
    val ctx = LocalContext.current
    var a11y by remember { mutableStateOf(CallListenService.enabled(ctx)) }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { a11y = CallListenService.enabled(ctx) }

    val a = tools.analysis?.takeIf { it.bool("ok") != false }
    val risk = a.obj("risk")
    val score = risk.int("score")
    val level = risk.str("level")
    val color = if (score != null) VG.level(level) else VG.violet
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(color.copy(alpha = 0.13f)).padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(70.dp).clip(CircleShape).background(color.copy(alpha = 0.25f)), contentAlignment = Alignment.Center) {
                Text(score?.toString() ?: "–", fontSize = 28.sp, fontWeight = FontWeight.Bold, color = color)
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(tr("AI live check", "AI लाइव जाँच"), fontWeight = FontWeight.Bold, fontSize = 16.sp)
                Text(when {
                    score == null && m.running -> tr("Listening… first result in ~10 s", "सुन रहे हैं… पहला नतीजा ~10 सेकंड में")
                    score == null -> tr("Not started", "शुरू नहीं हुई")
                    level == "danger" -> tr("DANGER – likely scam / AI voice", "ख़तरा – शायद ठगी / AI आवाज़")
                    level == "caution" -> tr("Be careful – some warning signs", "सावधान – कुछ चेतावनी संकेत")
                    else -> tr("No AI voice found so far", "अब तक AI आवाज़ नहीं मिली")
                }, color = color, fontWeight = FontWeight.SemiBold)
                if (a != null) {
                    val ai = a.obj("ai_voice").num("fake_prob")
                    val vp = a.obj("voice_print")
                    val focus = a.obj("caller_focus")
                    Text(listOfNotNull(
                        ai?.let { tr("AI voice ", "AI आवाज़ ") + "${(it * 100).toInt()}%" },
                        vp?.let { tr("voice print: ", "वॉइस प्रिंट: ") + (it.str("verdict") ?: "?") },
                        focus?.takeIf { it.bool("used") == true && (it.num("owner_s") ?: 0.0) > 0 }?.let { tr("your voice removed", "आपकी आवाज़ हटाई") },
                    ).joinToString(" · "), color = VG.muted, fontSize = 12.sp)
                }
            }
        }
        risk.objs("reasons").firstOrNull { !it.str("en").isNullOrBlank() }?.let {
            Text("• " + it.bi("en", "hi"), fontSize = 13.sp)
        }
        when {
            m.blocked -> {
                Text(if (a11y) tr("The microphone is still silent. Make sure the call is on speaker; some phones block this completely – then use 'Are you really calling?' below.",
                        "माइक अब भी चुप है। कॉल स्पीकर पर रखें; कुछ फ़ोन इसे पूरी तरह रोकते हैं – तब नीचे 'क्या सच में आप कॉल कर रहे हैं?' इस्तेमाल करें।")
                     else tr("Android is muting the microphone for apps during this call. Turn on \"VoiceGuard call listening\" in Accessibility (one time) and come back – the check restarts by itself.",
                        "इस कॉल में Android ऐप्स का माइक बंद कर रहा है। Accessibility में \"VoiceGuard call listening\" एक बार चालू करें और वापस आएं।"),
                    color = VG.amber, fontSize = 13.sp)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (!a11y) SmallButton(tr("Turn on", "चालू करें"), Icons.Default.Accessibility, Modifier.weight(1f)) { CallListenService.openSettings(ctx) }
                    SmallButton(tr("Stop", "रोकें"), Icons.Default.Stop, Modifier.weight(1f)) { m.stop() }
                }
            }
            m.running -> {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.weight(1f)) { LevelMeter(m.level) }
                    Spacer(Modifier.width(8.dp))
                    SmallButton(tr("Stop", "रोकें"), Icons.Default.Stop) { m.stop() }
                }
                Text(m.status + if (tools.analyzing) tr(" · AI checking", " · AI जाँच रहा है") else "", color = VG.muted, fontSize = 12.sp)
            }
            else -> Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                BigButton(if (score == null) tr("Start live check", "लाइव जाँच शुरू करें") else tr("Check again", "फिर जाँचें"),
                    if (score == null) Icons.Default.GraphicEq else Icons.Default.Refresh, VG.violet, onClick = onStart)
            }
        }
        tools.note?.let { Text(it, color = VG.amber, fontSize = 13.sp) }
        if (!a11y && !m.blocked) Row(verticalAlignment = Alignment.CenterVertically) {
            Text(tr("Tip: turn on \"VoiceGuard call listening\" so Android doesn't mute the mic during calls.",
                "सुझाव: \"VoiceGuard call listening\" चालू करें ताकि कॉल में माइक बंद न हो।"), color = VG.muted, fontSize = 12.sp, modifier = Modifier.weight(1f))
            SmallButton(tr("Turn on", "चालू करें")) { CallListenService.openSettings(ctx) }
        }
    }
}
