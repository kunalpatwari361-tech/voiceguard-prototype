package com.voiceguard.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PersonAdd
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.FamilyRestroom
import androidx.compose.material.icons.filled.HdrOn
import androidx.compose.material.icons.filled.HelpCenter
import androidx.compose.material.icons.filled.LocationOn
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.RecordVoiceOver
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableIntStateOf
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
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.navigation.NavHostController
import com.voiceguard.app.audio.Recorder
import com.voiceguard.app.audio.Wav
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Live
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
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import org.osmdroid.tileprovider.tilesource.TileSourceFactory
import org.osmdroid.util.BoundingBox
import org.osmdroid.util.GeoPoint
import org.osmdroid.views.MapView
import org.osmdroid.views.overlay.Marker

@Composable
fun FamilyScreen(nav: NavHostController, back: (() -> Unit)?) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var fam by remember { mutableStateOf(Sync.cachedFamily()) }
    var err by remember { mutableStateOf<String?>(null) }
    var verify by remember { mutableStateOf<Pair<String, JsonObject>?>(null) }
    var busy by remember { mutableStateOf<String?>(null) }
    var adding by remember { mutableStateOf(false) }
    suspend fun refresh() { runCatching { Sync.family() }.onSuccess { fam = it }.onFailure { err = it.message } }
    LaunchedEffect(Unit) { refresh() }
    LaunchedEffect(Unit) { Live.events.collect { if (it.str("type") == "family_updated") refresh() } }

    Screen(tr("Family Circle", "परिवार सर्कल"), back, actions = {
        IconButton({ scope.launch { refresh() } }) { Icon(Icons.Default.Refresh, null) }
    }) {
        InvitesCard { fam = it }
        if (Prefs.familyId == null) {
            NoFamilyPanel { fam = it }
            return@Screen
        }
        BigButton(tr("Add family member", "परिवार का सदस्य जोड़ें"), Icons.Default.PersonAdd, VG.green) { adding = true }
        Section(tr("Or share your family code", "या परिवार कोड भेजें"), Icons.Default.FamilyRestroom, VG.green) {
            Text(tr("On their phone: install VoiceGuard → sign in → Family → join with this code.", "उनके फ़ोन पर: VoiceGuard → साइन इन → परिवार → यह कोड डालें।"),
                color = VG.muted, fontSize = 13.sp)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(fam.str("invite_code").orEmpty().chunked(3).joinToString(" "), fontSize = 30.sp, fontWeight = FontWeight.Bold, color = VG.green,
                    modifier = Modifier.weight(1f))
                IconButton({ shareText(ctx, inviteText(tr("there", "जी"), Prefs.phone.orEmpty(), fam.str("invite_code"))) }) {
                    Icon(Icons.Default.Share, tr("Share", "शेयर"), tint = VG.blue)
                }
            }
        }
        ErrorBox(err)
        if (adding) AddMemberSheet(onChanged = { fam = it }, onDismiss = { adding = false })
        val members = fam.objs("members")
        val located = members.filter { it.obj("location") != null }
        if (located.isNotEmpty()) FamilyMap(located)

        members.forEach { m ->
            val me = m.str("id") == Prefs.userId
            val online = m.bool("online") == true
            Section {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(12.dp).clip(CircleShape).background(if (online) VG.green else VG.muted))
                    Spacer(Modifier.width(10.dp))
                    Column(Modifier.weight(1f)) {
                        Text((m.str("name") ?: "") + if (me) tr(" (you)", " (आप)") else "", fontWeight = FontWeight.Bold, fontSize = 17.sp)
                        Text(Numbers.pretty(m.str("phone")) + " · " + (m.str("role") ?: ""), color = VG.muted, fontSize = 13.sp)
                    }
                    if (m.bool("voiceprint_enrolled") == true) Chip(tr("voice print ✓", "वॉइस प्रिंट ✓"), VG.green)
                    else Chip(tr("no voice print", "वॉइस प्रिंट नहीं"), VG.amber)
                }
                Text(when (m.str("call_state")) {
                    "offhook" -> tr("📞 On a call right now", "📞 अभी कॉल पर हैं")
                    "ringing" -> tr("📳 Phone ringing", "📳 फ़ोन बज रहा है")
                    "idle" -> tr("Not on a call", "किसी कॉल पर नहीं")
                    else -> tr("Call status unknown", "कॉल स्थिति अज्ञात")
                } + if (!online) tr(" · app offline", " · ऐप ऑफ़लाइन") else "", color = VG.muted, fontSize = 13.sp)
                m.obj("location")?.let { l ->
                    Text("📍 " + (l.str("place") ?: "%.4f, %.4f".format(l.num("lat"), l.num("lon"))) +
                        tr(" · ${l.int("age_min") ?: "?"} min ago", " · ${l.int("age_min") ?: "?"} मिनट पहले"), fontSize = 14.sp)
                }
                if (!me) Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    SmallButton(tr("Are you calling?", "कॉल कर रहे हो?"), Icons.Default.HelpCenter, Modifier.weight(1f)) {
                        scope.launch {
                            busy = tr("Asking ${m.str("name")}'s phone…", "${m.str("name")} के फ़ोन से पूछ रहे हैं…")
                            runCatching { Api.post("/api/verify/ask", json("asker_id" to Prefs.userId, "claimed_user_id" to m.str("id"))).asObj()!! }
                                .onSuccess { verify = m.str("id")!! to it }.onFailure { err = it.message }
                            busy = null
                        }
                    }
                    IconButton({
                        scope.launch {
                            busy = tr("Getting live location…", "लाइव लोकेशन ले रहे हैं…")
                            runCatching { Api.post("/api/location/request", json("asker_id" to Prefs.userId, "target_id" to m.str("id"))) }
                            refresh(); busy = null
                        }
                    }) { Icon(Icons.Default.LocationOn, "Location", tint = VG.blue) }
                    IconButton({ nav.navigate("hdcall/${m.str("id")}") }) { Icon(Icons.Default.HdrOn, "HD call", tint = VG.violet) }
                    IconButton({ dial(ctx, m.str("phone") ?: return@IconButton) }) { Icon(Icons.Default.Call, "Call", tint = VG.green) }
                }
                verify?.takeIf { it.first == m.str("id") }?.second?.let { r ->
                    Banner(r.bi("message", "message_hi").orEmpty(), when (r.str("answer")) { "yes" -> VG.green; "no" -> VG.red; else -> VG.amber },
                        r.obj("auto")?.bi("en", "hi"))
                }
            }
        }
        val pending = fam.objs("pending")
        if (pending.isNotEmpty()) Text(tr("Waiting to join", "जुड़ने का इंतज़ार"), color = VG.amber, fontWeight = FontWeight.SemiBold)
        pending.forEach { p -> PendingMemberCard(p, fam.str("invite_code")) { fam = it } }
        if (members.size <= 1 && pending.isEmpty()) Text(tr("Only you are here yet – tap \"Add family member\".", "अभी सिर्फ़ आप हैं – \"परिवार का सदस्य जोड़ें\" दबाएं।"),
            color = VG.muted)
        busy?.let { Busy(it) }
        SmallButton(tr("Record / update MY voice print", "मेरा वॉइस प्रिंट रिकॉर्ड करें"), Icons.Default.RecordVoiceOver) { nav.navigate("voiceprint") }
    }
}

@Composable
private fun FamilyMap(members: List<JsonObject>) {
    AndroidView(factory = { c ->
        MapView(c).apply {
            setTileSource(TileSourceFactory.MAPNIK)
            setMultiTouchControls(true)
            controller.setZoom(11.0)
        }
    }, update = { map ->
        map.overlays.clear()
        members.forEach { m ->
            val l = m.obj("location") ?: return@forEach
            val p = GeoPoint(l.num("lat") ?: return@forEach, l.num("lon") ?: return@forEach)
            map.overlays.add(Marker(map).apply { position = p; title = m.str("name"); snippet = l.str("place") })
        }
        val pts = members.mapNotNull { m -> m.obj("location")?.let { l -> GeoPoint(l.num("lat") ?: return@let null, l.num("lon") ?: return@let null) } }
        if (pts.size == 1) map.controller.setCenter(pts[0])
        else if (pts.size > 1) map.post { map.zoomToBoundingBox(BoundingBox.fromGeoPoints(pts).increaseByScale(1.4f), false) }
        map.invalidate()
    }, modifier = Modifier.fillMaxWidth().height(220.dp).clip(RoundedCornerShape(16.dp)))
}

// ------------------------------------------------------------------ Voice Print (feature 10)
private val PROMPTS = listOf(
    "Namaste, main VoiceGuard par apni awaaz save kar raha hoon. Aaj mausam accha hai." to "नमस्ते, मैं VoiceGuard पर अपनी आवाज़ सेव कर रहा हूँ। आज मौसम अच्छा है।",
    "My family can trust my voice. I will never ask for money on a new number." to "मेरा परिवार मेरी आवाज़ पर भरोसा कर सकता है। मैं कभी नए नंबर से पैसे नहीं माँगूँगा।",
    "Ek do teen chaar paanch, mera naam aur mera ghar mujhe yaad hai." to "एक दो तीन चार पाँच, मेरा नाम और मेरा घर मुझे याद है।",
)

@Composable
fun VoicePrintScreen(back: () -> Unit) {
    val scope = rememberCoroutineScope()
    var i by remember { mutableIntStateOf(0) }
    var recording by remember { mutableStateOf(false) }
    var level by remember { mutableDoubleStateOf(-96.0) }
    var busy by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf<String?>(null) }
    var err by remember { mutableStateOf<String?>(null) }
    val rec = remember { Recorder() }
    Screen(tr("My Voice Print", "मेरा वॉइस प्रिंट"), back) {
        Text(tr("Read 3 short sentences. VoiceGuard saves your voice as numbers (not a recording) so your family's phone can check if a caller really sounds like you.",
            "3 छोटे वाक्य पढ़ें। VoiceGuard आपकी आवाज़ को नंबरों के रूप में सेव करता है (रिकॉर्डिंग नहीं)।"), color = VG.muted)
        Section(tr("Sentence ${i + 1} of 3", "वाक्य ${i + 1} / 3"), Icons.Default.RecordVoiceOver, VG.violet) {
            Text(PROMPTS[i % 3].first, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            Text(PROMPTS[i % 3].second, color = VG.muted)
            LevelMeter(level)
            if (busy) Busy(tr("Saving…", "सेव हो रहा है…"))
            else if (!recording) BigButton(tr("Hold phone normally and record", "रिकॉर्ड करें"), Icons.Default.Mic) {
                recording = true; err = null
                scope.launch {
                    val pcm = rec.record(12) { level = it }
                    recording = false; busy = true
                    runCatching { Api.upload("/api/voiceprint/${Prefs.userId}", mapOf("reset" to (i == 0)), Wav.encode(pcm)).asObj() }
                        .onSuccess { msg = tr("Saved ✓ (${it.int("samples")} samples)", "सेव ✓ (${it.int("samples")} नमूने)"); i++ }
                        .onFailure { err = it.message }
                    busy = false
                    runCatching { Sync.family() }
                }
            } else BigButton(tr("Done", "हो गया"), Icons.Default.Stop, VG.red) { rec.stop() }
        }
        msg?.let { Text(it, color = VG.green) }
        ErrorBox(err)
        if (i >= 3) Banner(tr("Voice print ready. Your family can now verify your voice.", "वॉइस प्रिंट तैयार है।"), VG.green)
        SmallButton(tr("Delete my voice print", "मेरा वॉइस प्रिंट हटाएं"), Icons.Default.Delete) {
            scope.launch { runCatching { Api.delete("/api/voiceprint/${Prefs.userId}") }; msg = tr("Deleted", "हटा दिया"); i = 0 }
        }
    }
}

// ------------------------------------------------------------------ Alerts (feature 17)
@Composable
fun AlertsScreen(back: () -> Unit) {
    val ctx = LocalContext.current
    val alerts = remember { mutableStateListOf<JsonObject>() }
    var err by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        runCatching { Api.get("/api/alerts/${Prefs.userId}").asList() }.onSuccess { alerts.clear(); alerts.addAll(it) }.onFailure { err = it.message }
    }
    LaunchedEffect(Unit) { Live.events.collect { e -> if (e.str("type") == "alert") e.obj("alert")?.let { alerts.add(0, it) } } }
    Screen(tr("Family alerts", "परिवार अलर्ट"), back) {
        ErrorBox(err)
        if (alerts.isEmpty()) Text(tr("No alerts yet. When someone in your family gets a scam call, you will see it here.",
            "अभी कोई अलर्ट नहीं।"), color = VG.muted)
        alerts.forEach { a ->
            val kind = a.str("kind")
            val c = when (kind) { "impersonation", "panic", "scam_call", "bank_hold" -> VG.red; "cyber_cell", "police_join" -> VG.amber; else -> VG.blue }
            Section(a.str("title"), Icons.Default.Notifications, c) {
                Text(a.str("body").orEmpty())
                Text((a.str("created_at") ?: "").take(16).replace('T', ' ') + " UTC · " + kind, color = VG.muted, fontSize = 12.sp)
                val p = a.obj("payload")
                when (kind) {
                    "impersonation" -> p.str("asker_phone")?.let { ph -> SmallButton(tr("Call ${p.str("asker_name")} now", "${p.str("asker_name")} को कॉल करें"), Icons.Default.Call) { dial(ctx, ph) } }
                    "cyber_cell" -> SmallButton(tr("Call 1930 for them", "उनके लिए 1930 कॉल करें"), Icons.Default.Call) { dial(ctx, "1930") }
                    else -> Sync.member(a.str("from_user_id"))?.str("phone")?.let { ph ->
                        if (a.str("from_user_id") != Prefs.userId) SmallButton(tr("Call ${p.str("from_name") ?: "them"}", "कॉल करें"), Icons.Default.Call) { dial(ctx, ph) }
                    }
                }
            }
        }
    }
}
