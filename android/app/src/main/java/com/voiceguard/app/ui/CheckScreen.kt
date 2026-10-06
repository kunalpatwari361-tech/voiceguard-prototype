package com.voiceguard.app.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AudioFile
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.FilterChip
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.unit.sp
import androidx.compose.ui.unit.dp
import androidx.navigation.NavHostController
import com.voiceguard.app.audio.Decoder
import com.voiceguard.app.audio.Recorder
import com.voiceguard.app.audio.Wav
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.str
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject

/** Voice Note Check (7) and Live Call Check in speaker mode (6). */
@Composable
fun CheckScreen(mode: String, nav: NavHostController, back: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var audio by remember { mutableStateOf<ShortArray?>(null) }
    var label by remember { mutableStateOf<String?>(null) }
    var claimed by remember { mutableStateOf<String?>(null) }
    var number by remember { mutableStateOf("") }
    var phoneSim by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf<String?>(null) }
    var err by remember { mutableStateOf<String?>(null) }
    var result by remember { mutableStateOf<JsonObject?>(null) }
    var recording by remember { mutableStateOf(false) }
    var level by remember { mutableDoubleStateOf(-96.0) }
    val recorder = remember { Recorder() }

    suspend fun load(uri: android.net.Uri) {
        busy = tr("Reading audio…", "ऑडियो पढ़ रहे हैं…"); err = null; result = null
        runCatching { Decoder.decode(ctx, uri) }
            .onSuccess { audio = it; label = tr("Audio file", "ऑडियो फ़ाइल") + " · ${it.size / Wav.SR} s" }
            .onFailure { err = tr("Could not read this audio: ", "यह ऑडियो नहीं पढ़ पाए: ") + it.message }
        busy = null
    }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> uri?.let { scope.launch { load(it) } } }
    LaunchedEffect(mode) {
        if (mode == "shared") Shared.sharedAudio?.let { load(it) }
    }

    Screen(if (mode == "record") tr("Live Call Check", "लाइव कॉल जाँच") else tr("Voice Note Check", "वॉइस नोट जाँच"), back) {
        if (mode == "record") {
            Text(tr("Put the suspicious call on SPEAKER and hold this phone near it, or record the caller in person. Android does not let apps record normal phone calls directly.",
                "संदिग्ध कॉल को स्पीकर पर रखें और यह फ़ोन पास रखें। Android ऐप्स को सीधे कॉल रिकॉर्ड नहीं करने देता।"), color = VG.muted, fontSize = 14.sp)
            LevelMeter(level)
            if (!recording) BigButton(tr("Record caller (max 30 s)", "कॉलर रिकॉर्ड करें (30 सेकंड)"), Icons.Default.Mic) {
                recording = true; result = null; err = null
                scope.launch {
                    val pcm = recorder.record(30) { level = it }
                    recording = false
                    if (recorder.allSilent) err = tr("Android blocked the microphone (silent recording). During a phone call, use the VoiceGuard HD Call or share a voice note instead.",
                        "Android ने माइक रोक दिया। कॉल के दौरान HD कॉल या वॉइस नोट इस्तेमाल करें।")
                    else { audio = pcm; label = tr("Recording", "रिकॉर्डिंग") + " · ${pcm.size / Wav.SR} s" }
                }
            } else BigButton(tr("Stop", "रोकें"), Icons.Default.Stop, VG.red) { recorder.stop() }
        } else {
            Text(tr("Pick a WhatsApp voice note or any recording. Tip: in WhatsApp, long-press a voice note → Share → VoiceGuard.",
                "WhatsApp वॉइस नोट चुनें। टिप: WhatsApp में वॉइस नोट दबाकर रखें → शेयर → VoiceGuard।"), color = VG.muted, fontSize = 14.sp)
            BigButton(tr("Choose audio file", "ऑडियो फ़ाइल चुनें"), Icons.Default.AudioFile, VG.blue) { picker.launch(arrayOf("audio/*", "application/ogg")) }
        }
        busy?.let { Busy(it) }
        label?.let { Chip("✓ $it", VG.green) }

        Section(tr("Who does the voice claim to be?", "आवाज़ किसकी होने का दावा है?")) {
            LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                item { FilterChip(claimed == null, { claimed = null }, label = { Text(tr("Not sure", "पता नहीं")) }) }
                items(Sync.others()) { m -> FilterChip(claimed == m.str("id"), { claimed = m.str("id") }, label = { Text(m.str("name").orEmpty()) }) }
            }
            OutlinedTextField(number, { number = it }, label = { Text(tr("Caller number (optional)", "कॉलर नंबर (वैकल्पिक)")) },
                singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone), modifier = Modifier.fillMaxWidth())
            ToggleRow(tr("Test as phone-quality audio (8 kHz)", "फ़ोन-क्वालिटी (8 kHz) में जाँचें"), phoneSim) { phoneSim = it }
        }

        BigButton(tr("Check this voice", "यह आवाज़ जाँचें"), Icons.Default.Search, enabled = audio != null && busy == null) {
            scope.launch {
                busy = tr("AI is checking: voice detector, fingerprints, voice print, scam words… (10-30 s)",
                    "AI जाँच रहा है: आवाज़, निशान, वॉइस प्रिंट, ठगी शब्द… (10-30 सेकंड)")
                err = null
                runCatching {
                    Api.upload("/api/analyze", mapOf("user_id" to Prefs.userId, "claimed_user_id" to claimed,
                        "number" to number.ifBlank { null }, "source" to if (mode == "record") "live_call" else "voice_note",
                        "simulate_phone" to phoneSim), Wav.encode(audio!!)).asObj()
                }.onSuccess { result = it }.onFailure { err = it.message }
                busy = null
            }
        }
        ErrorBox(err)
        result?.let { ReportView(it, ReportCtx(number.ifBlank { null }, claimed, if (mode == "record") "live_call" else "voice_note", audio), nav) }
    }
}
