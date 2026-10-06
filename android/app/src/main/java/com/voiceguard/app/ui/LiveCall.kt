package com.voiceguard.app.ui

import android.media.MediaRecorder
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.voiceguard.app.audio.Recorder
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlin.math.abs

/**
 * Live Call Check (feature 6) + real-time Reverse Engineering (feature 4) on a normal phone call.
 * Android 10+ does not give normal apps the call audio stream, so the phone listens to the caller through
 * the loudspeaker in 6-second windows. Every window gets a fast check (AI voice, fingerprints, voice print);
 * every third window also runs speech-to-text for scam words. If Android hands back silence, we say so.
 */
class LiveCallMonitor(private val tools: CallToolsState) {
    var running by mutableStateOf(false)
    var blocked by mutableStateOf(false)
    var level by mutableDoubleStateOf(-96.0)
    var windows by mutableIntStateOf(0)
    var status by mutableStateOf("")
    private var job: Job? = null
    private var recorder: Recorder? = null

    fun start(scope: CoroutineScope, ensureSpeaker: () -> Unit) {
        if (running) return
        running = true; blocked = false
        ensureSpeaker()
        job = scope.launch {
            val all = ArrayList<Short>()
            var silent = 0
            val sources = listOf(MediaRecorder.AudioSource.VOICE_RECOGNITION, MediaRecorder.AudioSource.MIC)
            var src = 0
            while (isActive && running) {
                status = tr("Listening to the caller…", "कॉलर को सुन रहे हैं…")
                val rec = Recorder(sources[src]).also { recorder = it }
                val pcm = runCatching { rec.record(6) { level = it } }.getOrNull() ?: ShortArray(0)
                if (!running) break
                val peak = pcm.maxOfOrNull { abs(it.toInt()) } ?: 0
                if (rec.allSilent || peak < 60) {
                    silent++
                    if (silent == 2 && src < sources.size - 1) { src++; continue }   // try the other microphone path
                    if (silent >= 3) { blocked = true; running = false; break }
                    continue
                }
                silent = 0
                pcm.forEach { all.add(it) }
                while (all.size > 16000 * 30) all.removeAt(0)               // keep the last 30 s
                tools.audio = all.toShortArray()
                windows++
                status = tr("Checking window $windows…", "विंडो $windows जाँच रहे हैं…")
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
}

@Composable
fun LiveCheckCard(m: LiveCallMonitor, onStart: () -> Unit) {
    Section(tr("Live voice check (real time)", "लाइव आवाज़ जाँच (रियल टाइम)"), Icons.Default.GraphicEq, VG.violet) {
        when {
            m.blocked -> Banner(tr("This phone blocks call audio for apps", "यह फ़ोन ऐप्स को कॉल ऑडियो नहीं देता"), VG.amber,
                tr("Android gave VoiceGuard silence during the call. Use 'Are You Really Calling?' and 'Verify with HD call' – they work without call audio.",
                    "कॉल के दौरान Android ने आवाज़ नहीं दी। 'क्या सच में आप कॉल कर रहे हैं?' और 'HD कॉल' इस्तेमाल करें।"))
            m.running -> {
                Text(m.status + "  ·  " + tr("${m.windows} checked", "${m.windows} जाँचे"), color = VG.muted, fontSize = 13.sp)
                LevelMeter(m.level)
                Text(tr("Speaker is on. Let the caller talk; the risk score below updates every few seconds.",
                    "स्पीकर चालू है। कॉलर को बोलने दें; नीचे का स्कोर हर कुछ सेकंड में बदलेगा।"), color = VG.muted, fontSize = 12.sp)
                SmallButton(tr("Stop live check", "लाइव जाँच रोकें"), Icons.Default.Stop) { m.stop() }
            }
            else -> Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                BigButton(tr("Start live check", "लाइव जाँच शुरू करें"), Icons.Default.GraphicEq, VG.violet, onClick = onStart)
            }
        }
    }
}

