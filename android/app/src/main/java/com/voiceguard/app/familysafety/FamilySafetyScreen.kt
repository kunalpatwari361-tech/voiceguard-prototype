package com.voiceguard.app.familysafety

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Flag
import androidx.compose.material.icons.filled.Group
import androidx.compose.material.icons.filled.LocationOn
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.PrivacyTip
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
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
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.arr
import com.voiceguard.app.data.Live
import com.voiceguard.app.data.asList
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.int
import com.voiceguard.app.data.json
import com.voiceguard.app.data.num
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.objs
import com.voiceguard.app.data.str
import com.voiceguard.app.service.Loc
import com.voiceguard.app.ui.BigButton
import com.voiceguard.app.ui.Chip
import com.voiceguard.app.ui.ErrorBox
import com.voiceguard.app.ui.Screen
import com.voiceguard.app.ui.Section
import com.voiceguard.app.ui.SmallButton
import com.voiceguard.app.ui.VG
import com.voiceguard.app.ui.openUrl
import com.voiceguard.app.ui.shareText
import com.voiceguard.app.ui.toast
import com.voiceguard.app.ui.tr
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import org.osmdroid.tileprovider.tilesource.TileSourceFactory
import org.osmdroid.util.GeoPoint
import org.osmdroid.views.MapView
import org.osmdroid.views.overlay.Marker
import org.osmdroid.views.overlay.Polygon

private const val B = FamilySafety.BASE

private fun granted(ctx: Context, p: String) = ctx.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED
private fun notifPermNeeded() = Build.VERSION.SDK_INT >= 33
private fun hasNotif(ctx: Context) = !notifPermNeeded() || granted(ctx, Manifest.permission.POST_NOTIFICATIONS)

@Composable
fun FamilySafetyScreen(back: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var st by remember { mutableStateOf(FamilySafety.status.value) }
    var group by remember { mutableStateOf<JsonObject?>(null) }
    val members = remember { mutableStateListOf<JsonObject>() }
    var zones by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var arrivals by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var events by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var err by remember { mutableStateOf<String?>(null) }
    var showConsent by remember { mutableStateOf(false) }
    var mapRef by remember { mutableStateOf<MapView?>(null) }

    suspend fun load() {
        err = null
        st = FamilySafety.refresh()
        if (st?.str("enabled") != "true") return
        group = runCatching { Api.get("$B/group").asObj() }.getOrNull()
        if (group == null) { members.clear(); return }
        runCatching { Api.get("$B/map").asObj() }.onSuccess { m -> members.clear(); members.addAll(m.objs("members")) }
            .onFailure { err = it.message }
        zones = runCatching { Api.get("$B/zones").asList() }.getOrDefault(emptyList())
        arrivals = runCatching { Api.get("$B/arrivals").asList() }.getOrDefault(emptyList())
        events = runCatching { Api.get("$B/events?active=true").asList() }.getOrDefault(emptyList())
        // the server is the authority: if it says sharing is off, stop sending here too
        if (st?.str("sharing") == "off" && FamilySafety.wantSharing) { FamilySafety.wantSharing = false; FamilySafety.stopService(ctx) }
    }

    LaunchedEffect(Unit) { load() }
    // realtime: while this screen is open the server pushes each member's update over the live link
    LaunchedEffect(group?.str("id")) {
        if (group == null) return@LaunchedEffect
        while (true) { runCatching { Api.post("$B/map/watch") }; delay(4 * 60_000L) }
    }
    DisposableEffect(Unit) { onDispose { FamilySafety.background { Api.post("$B/map/unwatch") } } }
    LaunchedEffect(Unit) {
        Live.events.collect { e ->
            when (e.str("type")) {
                "family_safety_member" -> e.obj("member")?.let { m ->
                    val i = members.indexOfFirst { it.str("user_id") == m.str("user_id") }
                    if (m.str("status") in setOf("left", "removed")) { if (i >= 0) members.removeAt(i) }
                    else if (i >= 0) members[i] = JsonObject(members[i] + m) else members.add(m)
                }
                "family_updated", "family_safety_event", "alert" -> load()
            }
        }
    }

    Screen(tr("Family Safety", "परिवार सुरक्षा"), back, actions = {
        IconButton({ scope.launch { load() } }) { Icon(Icons.Default.Refresh, null) }
    }) {
        ErrorBox(err)
        when {
            st == null -> Text(tr("Cannot reach the VoiceGuard server.", "VoiceGuard सर्वर से संपर्क नहीं हो पा रहा।"), color = VG.muted)
            st?.str("enabled") != "true" -> Text(tr("Family Safety is switched off on this server.", "इस सर्वर पर परिवार सुरक्षा बंद है।"), color = VG.muted)
            group == null -> NoCircle { scope.launch { load() } }
            else -> {
                SharingCard(st!!, onTurnOn = { showConsent = true }, onChanged = { scope.launch { load() } })
                SosCard(events, onChanged = { scope.launch { load() } })
                MapCard(members) { mapRef = it }
                ArrivalCard(members, arrivals, st!!, { mapRef?.mapCenter?.let { c -> c.latitude to c.longitude } }) { scope.launch { load() } }
                ZonesCard(members, zones, { mapRef?.mapCenter?.let { c -> c.latitude to c.longitude } }) { scope.launch { load() } }
                CircleCard(group!!) { scope.launch { load() } }
            }
        }
    }
    if (showConsent) ConsentFlow(onDone = { showConsent = false; scope.launch { load() } })
}

// ------------------------------------------------------------------ consent + permissions (prominent disclosure first)
@Composable
private fun ConsentFlow(onDone: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var info by remember { mutableStateOf<JsonObject?>(null) }
    var battery by remember { mutableStateOf(false) }
    var zoneAlerts by remember { mutableStateOf(true) }
    var threshold by remember { mutableStateOf(15f) }
    var agreed by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) { info = runCatching { Api.get("$B/consent").asObj() }.getOrNull() }

    suspend fun finish() {
        val step = FamilySafetyLogic.nextStep(agreed, Loc.granted(ctx), notifPermNeeded(), hasNotif(ctx))
        if (step != FamilySafetyLogic.Step.READY) return
        runCatching {
            Api.post("$B/consent", json("version" to info.str("version"), "accept" to true, "share_location" to true,
                "share_battery" to battery, "zone_alerts" to zoneAlerts, "battery_threshold" to threshold.toInt()))
        }.onSuccess {
            FamilySafety.shareBattery = battery
            FamilySafety.refresh()
            FamilySafety.startSharing(ctx)
            onDone()
        }.onFailure { msg = it.message }
    }
    val notifLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (ok) scope.launch { finish() } else msg = tr("Without notifications you could not see that sharing is on, so it stays off.",
            "सूचना के बिना आप नहीं देख पाएंगे कि शेयरिंग चालू है, इसलिए यह बंद रहेगी।")
    }
    val locLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { r ->
        if (r.values.any { it }) {
            if (notifPermNeeded() && !hasNotif(ctx)) notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS) else scope.launch { finish() }
        } else msg = tr("Location permission was not given, so nothing is shared. You can allow it later in Android Settings.",
            "स्थान अनुमति नहीं मिली, इसलिए कुछ भी शेयर नहीं होगा।")
    }

    AlertDialog(onDismissRequest = onDone, confirmButton = {
        TextButton(enabled = info != null, onClick = {
            agreed = true
            msg = null
            when (FamilySafetyLogic.nextStep(true, Loc.granted(ctx), notifPermNeeded(), hasNotif(ctx))) {
                FamilySafetyLogic.Step.LOCATION_PERMISSION -> locLauncher.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION))
                FamilySafetyLogic.Step.NOTIFICATION_PERMISSION -> notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                else -> scope.launch { finish() }
            }
        }) { Text(tr("I agree - share my location", "मैं सहमत हूँ - मेरा स्थान शेयर करें")) }
    }, dismissButton = { TextButton(onDone) { Text(tr("Not now", "अभी नहीं")) } },
        icon = { Icon(Icons.Default.PrivacyTip, null) },
        title = { Text(tr("Share your location with your family?", "परिवार के साथ अपना स्थान शेयर करें?")) },
        text = {
            Column(Modifier.heightIn(max = 440.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                val t = info?.obj("text")
                for ((k, label) in listOf("what" to "What is shared", "why" to "Why", "who" to "Who can see it", "when" to "When (also in the background)", "keep" to "How long it is kept")) {
                    Text(label, fontWeight = FontWeight.SemiBold)
                    Text(t.str(k) ?: "…", fontSize = 13.sp, color = VG.muted)
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(battery, { battery = it }); Text(tr("Also share my battery level (low-battery alerts)", "मेरी बैटरी भी शेयर करें"), fontSize = 13.sp)
                }
                if (battery) {
                    Text(tr("Alert my family below ${threshold.toInt()}%", "${threshold.toInt()}% से कम पर परिवार को बताएं"), fontSize = 13.sp)
                    Slider(threshold, { threshold = it }, valueRange = 5f..50f, steps = 8)
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(zoneAlerts, { zoneAlerts = it }); Text(tr("Allow safe-zone alerts about me (I can see every zone that watches me)", "मेरे बारे में सेफ-ज़ोन अलर्ट की अनुमति दें"), fontSize = 13.sp)
                }
                Text(tr("Next, Android asks for location and notification permission. A notification is always shown while sharing runs.",
                    "अब Android स्थान और सूचना की अनुमति मांगेगा। शेयरिंग के दौरान सूचना हमेशा दिखेगी।"), fontSize = 12.sp, color = VG.muted)
                if (msg != null) {
                    Text(msg!!, color = VG.red, fontSize = 13.sp)
                    TextButton({ ctx.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + ctx.packageName))
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }) { Text(tr("Open Android settings", "Android सेटिंग्स खोलें")) }
                }
            }
        })
}

// ------------------------------------------------------------------ sharing status
@Composable
private fun SharingCard(st: JsonObject, onTurnOn: () -> Unit, onChanged: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val state = st.str("sharing") ?: "off"
    val permOk = Loc.granted(ctx)
    Section(tr("My location sharing", "मेरा स्थान शेयर"), Icons.Default.LocationOn, if (state == "sharing") VG.green else VG.amber) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Chip(when (state) { "sharing" -> tr("SHARING", "शेयर हो रहा"); "paused" -> tr("PAUSED", "रुका"); else -> tr("OFF", "बंद") },
                when (state) { "sharing" -> VG.green; "paused" -> VG.amber; else -> VG.muted })
            Spacer(Modifier.width(8.dp))
            Text(when (state) {
                "sharing" -> tr("Your family circle can see your latest location.", "आपका परिवार आपका ताज़ा स्थान देख सकता है।")
                "paused" -> tr("Paused - your family sees 'paused', not your location.", "रुका है - परिवार को स्थान नहीं दिखता।")
                else -> tr("Nobody can see your location.", "कोई आपका स्थान नहीं देख सकता।")
            }, fontSize = 13.sp, color = VG.muted)
        }
        if (state != "off" && !permOk) Text(tr("Location permission is off in Android settings - nothing is being sent.", "Android सेटिंग्स में स्थान अनुमति बंद है।"), color = VG.red, fontSize = 13.sp)
        if (state == "sharing" && permOk && !FamilySafety.wantSharing) Text(tr("Not sending from this phone. Tap Resume.", "इस फ़ोन से नहीं भेजा जा रहा।"), color = VG.amber, fontSize = 13.sp)
        when (state) {
            "off" -> BigButton(tr("Turn on location sharing", "स्थान शेयरिंग चालू करें"), Icons.Default.LocationOn, VG.green, onClick = onTurnOn)
            else -> Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (state == "sharing") SmallButton(tr("Pause 1 h", "1 घंटा रोकें"), Icons.Default.Pause) {
                    scope.launch { runCatching { FamilySafety.pause(ctx, 60) }.onFailure { toast(ctx, it.message ?: "") }; onChanged() }
                }
                if (state == "paused" || !FamilySafety.wantSharing) SmallButton(tr("Resume", "फिर शुरू"), Icons.Default.PlayArrow) {
                    scope.launch {
                        runCatching { if (state == "paused") Api.post("$B/sharing/resume") }
                            .onSuccess { if (Loc.granted(ctx) && hasNotif(ctx)) FamilySafety.startSharing(ctx) else onTurnOn() }
                            .onFailure { toast(ctx, it.message ?: "") }
                        onChanged()
                    }
                }
                SmallButton(tr("Stop", "बंद करें"), Icons.Default.Stop) {
                    scope.launch { runCatching { FamilySafety.stopSharing(ctx) }.onFailure { toast(ctx, it.message ?: "") }; onChanged() }
                }
            }
        }
        Text(tr("Only your latest position is kept. Stop deletes it from the server at once.", "केवल ताज़ा स्थान रखा जाता है। बंद करने पर तुरंत हट जाता है।"),
            fontSize = 12.sp, color = VG.muted)
    }
}

// ------------------------------------------------------------------ SOS
@Composable
private fun SosCard(events: List<JsonObject>, onChanged: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var confirm by remember { mutableStateOf(false) }
    var sending by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<String?>(null) }
    Section(tr("SOS", "SOS"), Icons.Default.Warning, VG.red) {
        BigButton(if (sending) tr("Sending…", "भेज रहे हैं…") else tr("SOS - alert my family", "SOS - परिवार को सूचना"), Icons.Default.Warning, VG.red,
            enabled = !sending) { confirm = true }
        Text(tr("Alerts your family circle with your location. VoiceGuard does NOT call police or 112 - call 112 yourself in an emergency.",
            "आपके स्थान के साथ परिवार को सूचना। VoiceGuard पुलिस/112 को कॉल नहीं करता - आपात स्थिति में 112 पर खुद कॉल करें।"), fontSize = 12.sp, color = VG.muted)
        result?.let { Text(it, color = VG.green, fontSize = 13.sp) }
        for (e in events.filter { it.str("kind") == "sos" }) {
            val mine = e.obj("from")?.str("user_id") == com.voiceguard.app.data.Prefs.userId
            Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp))) {
                Text((if (mine) tr("Your SOS", "आपका SOS") else "SOS: " + (e.obj("from")?.str("name") ?: "")) + " · " + (e.str("status") ?: ""),
                    fontWeight = FontWeight.SemiBold, color = VG.red)
                e.str("detail")?.takeIf { it.isNotBlank() }?.let { Text(it, fontSize = 13.sp) }
                if (mine) for (d in e.objs("deliveries")) Text("• ${d.str("to")}: ${d.str("status")}", fontSize = 12.sp, color = VG.muted)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    e.obj("location")?.let { l -> SmallButton(tr("Map", "नक्शा"), Icons.Default.Map) { openUrl(ctx, "https://maps.google.com/?q=${l.num("lat")},${l.num("lon")}") } }
                    if (e.str("status") == "active") SmallButton(if (mine) tr("I'm safe - cancel", "मैं सुरक्षित हूँ") else tr("Mark resolved", "हल हुआ")) {
                        scope.launch { runCatching { Api.post("$B/events/${e.str("id")}/${if (mine) "cancel" else "resolve"}", json("note" to "")) }; onChanged() }
                    }
                }
            }
        }
    }
    if (confirm) AlertDialog(onDismissRequest = { confirm = false },
        title = { Text(tr("Send SOS to your family now?", "अभी परिवार को SOS भेजें?")) },
        text = { Text(tr("Everyone in your family circle gets an alert with your current location.", "परिवार के सभी लोगों को आपके स्थान के साथ सूचना जाएगी।")) },
        confirmButton = { TextButton({
            confirm = false
            sending = true
            scope.launch {
                val loc = runCatching { Loc.current(ctx) }.getOrNull()
                runCatching { FamilySafety.sos("", loc) }
                    .onSuccess { r -> result = if (r.str("duplicate") == "true") tr("SOS already sent - family alerted.", "SOS पहले ही भेजा गया।")
                        else tr("SOS sent to ${r.int("recipients") ?: 0} family member(s).", "SOS ${r.int("recipients") ?: 0} सदस्यों को भेजा गया।") }
                    .onFailure { result = null; toast(ctx, tr("SOS not sent: ", "SOS नहीं गया: ") + it.message + tr(" - call 112 or your family directly.", " - 112 या परिवार को सीधे कॉल करें।")) }
                sending = false
                onChanged()
            }
        }) { Text(tr("Send SOS", "SOS भेजें"), color = VG.red) } },
        dismissButton = { TextButton({ confirm = false }) { Text(tr("Cancel", "रद्द करें")) } })
}

// ------------------------------------------------------------------ live family map
@Composable
private fun MapCard(members: List<JsonObject>, onMap: (MapView) -> Unit) {
    Section(tr("Family map", "परिवार का नक्शा"), Icons.Default.Map) {
        val withLoc = members.filter { it.obj("location") != null }
        AndroidView(factory = { c ->
            MapView(c).apply { setTileSource(TileSourceFactory.MAPNIK); setMultiTouchControls(true); controller.setZoom(14.0); onMap(this) }
        }, update = { map ->
            map.overlays.clear()
            for (m in withLoc) {
                val l = m.obj("location")!!
                val p = GeoPoint(l.num("lat") ?: continue, l.num("lon") ?: continue)
                l.num("accuracy")?.takeIf { it > 0 }?.let { a ->
                    map.overlays.add(Polygon(map).apply { points = Polygon.pointsAsCircle(p, a); fillPaint.color = 0x222196F3; outlinePaint.color = 0x662196F3; outlinePaint.strokeWidth = 2f })
                }
                map.overlays.add(Marker(map).apply {
                    position = p; setAnchor(Marker.ANCHOR_CENTER, Marker.ANCHOR_BOTTOM)
                    title = m.str("name"); snippet = "${m.str("status")} · ${FamilySafetyLogic.ageText(m.int("age_s"))}"
                })
            }
            withLoc.firstOrNull()?.obj("location")?.let { l -> if (map.tag == null) { map.controller.setCenter(GeoPoint(l.num("lat")!!, l.num("lon")!!)); map.tag = "centred" } }
            map.invalidate()
        }, modifier = Modifier.fillMaxWidth().height(300.dp).clip(RoundedCornerShape(16.dp)))
        for (m in members) {
            val status = m.str("status") ?: "off"
            val color = when (status) { "live" -> VG.green; "stale" -> VG.amber; "offline" -> VG.red; else -> VG.muted }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text((m.str("name") ?: "") + if (m.str("is_me") == "true") tr(" (you)", " (आप)") else "", fontWeight = FontWeight.SemiBold)
                    val l = m.obj("location")
                    Text(if (l != null) "${FamilySafetyLogic.ageText(m.int("age_s"))} · ± ${l.num("accuracy")?.toInt() ?: "?"} m" +
                        (m.obj("battery")?.let { b -> " · 🔋 ${b.int("level")}%" + if (b.str("charging") == "true") "⚡" else "" } ?: "")
                    else when (status) { "paused" -> tr("paused sharing", "शेयरिंग रोकी"); "no_location" -> tr("waiting for first location", "पहले स्थान का इंतज़ार"); else -> tr("not sharing", "शेयर नहीं कर रहे") },
                        fontSize = 12.sp, color = VG.muted)
                }
                Chip(when (status) { "live" -> tr("live", "लाइव"); "stale" -> tr("old", "पुराना"); "offline" -> tr("offline", "ऑफ़लाइन"); "paused" -> tr("paused", "रुका"); else -> tr("off", "बंद") }, color)
            }
        }
        Text(tr("Grey / old locations are not current - check the time.", "पुराने स्थान अभी के नहीं हैं - समय देखें।"), fontSize = 12.sp, color = VG.muted)
    }
}

// ------------------------------------------------------------------ arrival alerts
@Composable
private fun Recipients(members: List<JsonObject>, picked: MutableList<String>, includeMe: Boolean = false) {
    for (m in members.filter { includeMe || it.str("is_me") != "true" }) {
        val id = m.str("user_id") ?: continue
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(id in picked, { if (it) picked.add(id) else picked.remove(id) })
            Text(m.str("name") ?: id, fontSize = 14.sp)
        }
    }
}

@Composable
private fun ArrivalCard(members: List<JsonObject>, arrivals: List<JsonObject>, st: JsonObject, center: () -> Pair<Double, Double>?, onChanged: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var open by remember { mutableStateOf(false) }
    var name by remember { mutableStateOf("") }
    val picked = remember { mutableStateListOf<String>() }
    Section(tr("Tell family when I arrive", "पहुँचने पर परिवार को बताएं"), Icons.Default.Flag) {
        for (a in arrivals.filter { it.str("status") == "active" }) Row(verticalAlignment = Alignment.CenterVertically) {
            Text("→ ${a.str("name")}", Modifier.weight(1f))
            SmallButton(tr("Cancel", "रद्द")) { scope.launch { runCatching { Api.delete("$B/arrivals/${a.str("id")}") }; onChanged() } }
        }
        if (st.str("sharing") == "off") Text(tr("Turn on location sharing to use arrival alerts.", "पहुँच सूचना के लिए स्थान शेयरिंग चालू करें।"), fontSize = 13.sp, color = VG.muted)
        else if (!open) SmallButton(tr("Set a destination", "गंतव्य चुनें"), Icons.Default.Flag) { open = true }
        else {
            Text(tr("Move the map so its centre is the destination.", "नक्शे का केंद्र गंतव्य पर रखें।"), fontSize = 12.sp, color = VG.muted)
            OutlinedTextField(name, { name = it.take(40) }, label = { Text(tr("Place name (e.g. Home)", "जगह का नाम")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Text(tr("Who should be told?", "किसे बताएं?"), fontWeight = FontWeight.SemiBold)
            Recipients(members, picked)
            BigButton(tr("Start", "शुरू करें"), enabled = name.isNotBlank() && picked.isNotEmpty()) {
                val c = center() ?: return@BigButton toast(ctx, tr("Map not ready", "नक्शा तैयार नहीं"))
                scope.launch {
                    runCatching { Api.post("$B/arrivals", json("name" to name.trim(), "lat" to c.first, "lon" to c.second, "radius_m" to 150, "recipients" to picked.toList())) }
                        .onSuccess { open = false; name = ""; picked.clear(); onChanged() }.onFailure { toast(ctx, it.message ?: "") }
                }
            }
        }
    }
}

// ------------------------------------------------------------------ safe zones
@Composable
private fun ZonesCard(members: List<JsonObject>, zones: List<JsonObject>, center: () -> Pair<Double, Double>?, onChanged: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var open by remember { mutableStateOf(false) }
    var name by remember { mutableStateOf("") }
    var radius by remember { mutableStateOf(200f) }
    val subjects = remember { mutableStateListOf<String>() }
    val recipients = remember { mutableStateListOf<String>() }
    val names = members.associate { (it.str("user_id") ?: "") to (it.str("name") ?: "") }
    Section(tr("Safe zones", "सुरक्षित ज़ोन"), Icons.Default.Group) {
        for (z in zones) Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("${z.str("name")} · ${z.int("radius_m")} m", Modifier.weight(1f), fontWeight = FontWeight.SemiBold)
                if (z.str("mine") == "true") Switch(z.str("enabled") == "true", { on ->
                    scope.launch { runCatching { Api.post("$B/zones/${z.str("id")}/edit", json("enabled" to on)) }; onChanged() } })
            }
            Text(tr("Watches: ", "देखता है: ") + z.arr2("subjects").joinToString { names[it] ?: "?" } +
                tr(" · alerts to: ", " · सूचना: ") + z.arr2("recipients").joinToString { names[it] ?: "?" }, fontSize = 12.sp, color = VG.muted)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (z.str("mine") == "true") SmallButton(tr("Delete", "हटाएं")) { scope.launch { runCatching { Api.delete("$B/zones/${z.str("id")}") }; onChanged() } }
                if (z.str("watches_me") == "true" && z.str("mine") != "true") SmallButton(tr("Stop watching me", "मुझे न देखें")) {
                    scope.launch { runCatching { Api.post("$B/zones/${z.str("id")}/leave") }; onChanged() }
                }
            }
        }
        if (!open) SmallButton(tr("Add a safe zone", "सुरक्षित ज़ोन जोड़ें")) { open = true }
        else {
            Text(tr("Move the map so its centre is the zone's centre.", "नक्शे का केंद्र ज़ोन के बीच में रखें।"), fontSize = 12.sp, color = VG.muted)
            OutlinedTextField(name, { name = it.take(40) }, label = { Text(tr("Zone name (e.g. School)", "ज़ोन का नाम")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Text(tr("Radius ${radius.toInt()} m", "दायरा ${radius.toInt()} m"))
            Slider(radius, { radius = it }, valueRange = 50f..2000f)
            Text(tr("Report entering / leaving for:", "किसके आने/जाने की सूचना:"), fontWeight = FontWeight.SemiBold)
            Recipients(members, subjects, includeMe = true)
            Text(tr("Send alerts to:", "सूचना किसे:"), fontWeight = FontWeight.SemiBold)
            Recipients(members, recipients, includeMe = true)
            Text(tr("Watched members see this zone and can leave it. Alerts only work while they share their location.",
                "जिन्हें देखा जाता है वे यह ज़ोन देख सकते हैं और हट सकते हैं।"), fontSize = 12.sp, color = VG.muted)
            BigButton(tr("Save zone", "ज़ोन सेव करें"), enabled = name.isNotBlank() && subjects.isNotEmpty() && recipients.isNotEmpty()) {
                val c = center() ?: return@BigButton toast(ctx, tr("Map not ready", "नक्शा तैयार नहीं"))
                scope.launch {
                    runCatching { Api.post("$B/zones", json("name" to name.trim(), "lat" to c.first, "lon" to c.second, "radius_m" to radius.toInt(),
                        "subjects" to subjects.toList(), "recipients" to recipients.toList())) }
                        .onSuccess { open = false; name = ""; subjects.clear(); recipients.clear(); onChanged() }.onFailure { toast(ctx, it.message ?: "") }
                }
            }
        }
    }
}

private fun JsonObject.arr2(k: String): List<String> = arr(k).mapNotNull { it.str() }

// ------------------------------------------------------------------ family circle: invites, join, remove, leave
@Composable
private fun CircleCard(group: JsonObject, onChanged: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var invite by remember { mutableStateOf<String?>(null) }
    var leave by remember { mutableStateOf(false) }
    val owner = group.str("i_am_owner") == "true"
    Section(group.str("name") ?: tr("Family circle", "परिवार सर्कल"), Icons.Default.Group) {
        for (m in group.objs("members")) Row(verticalAlignment = Alignment.CenterVertically) {
            Text((m.str("name") ?: "") + (if (m.str("owner") == "true") tr(" · owner", " · मालिक") else ""), Modifier.weight(1f))
            Chip(m.str("sharing") ?: "off", if (m.str("sharing") == "sharing") VG.green else VG.muted)
            if (owner && m.str("is_me") != "true") TextButton({
                scope.launch { runCatching { Api.post("$B/group/members/${m.str("user_id")}/remove") }.onFailure { toast(ctx, it.message ?: "") }; onChanged() }
            }) { Text(tr("Remove", "हटाएं"), color = VG.red) }
        }
        SmallButton(tr("Invite someone (code valid 48 h)", "आमंत्रण कोड (48 घंटे)"), Icons.Default.Share) {
            scope.launch {
                runCatching { Api.post("$B/group/invites", json("expires_hours" to 48, "max_uses" to 1)).asObj() }
                    .onSuccess { r -> invite = r.str("token"); shareText(ctx, "Join my VoiceGuard family circle: open VoiceGuard → Family Safety → Join, and enter this code (valid 48 hours, one person): ${r.str("token")}") }
                    .onFailure { toast(ctx, it.message ?: "") }
            }
        }
        invite?.let { Text(it, fontSize = 13.sp, color = VG.blue) }
        TextButton({ leave = true }) { Text(tr("Leave this family circle", "परिवार सर्कल छोड़ें"), color = VG.red) }
    }
    if (leave) AlertDialog(onDismissRequest = { leave = false },
        title = { Text(tr("Leave the family circle?", "परिवार सर्कल छोड़ें?")) },
        text = { Text(tr("Your location sharing stops and your latest location is deleted.", "आपका स्थान शेयर बंद होगा और ताज़ा स्थान हट जाएगा।")) },
        confirmButton = { TextButton({
            leave = false
            scope.launch {
                runCatching { Api.post("$B/group/leave") }.onSuccess { FamilySafety.wantSharing = false; FamilySafety.stopService(ctx); onChanged() }
                    .onFailure { toast(ctx, it.message ?: "") }
            }
        }) { Text(tr("Leave", "छोड़ें"), color = VG.red) } },
        dismissButton = { TextButton({ leave = false }) { Text(tr("Cancel", "रद्द करें")) } })
}

@Composable
private fun NoCircle(onChanged: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var name by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var preview by remember { mutableStateOf<JsonObject?>(null) }
    Section(tr("Join a family circle", "परिवार सर्कल से जुड़ें"), Icons.Default.Group) {
        OutlinedTextField(code, { code = it.trim(); preview = null }, label = { Text(tr("Invitation code", "आमंत्रण कोड")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
        val p = preview
        if (p == null) SmallButton(tr("Check code", "कोड जाँचें"), enabled = code.length >= 10) {
            scope.launch { runCatching { Api.post("$B/invites/preview", json("token" to code)).asObj() }.onSuccess { preview = it }.onFailure { toast(ctx, it.message ?: "") } }
        } else {
            Text(tr("Join '${p.str("family_name")}' (invited by ${p.str("invited_by")}, ${p.int("members")} members)?",
                "'${p.str("family_name")}' से जुड़ें?"), fontWeight = FontWeight.SemiBold)
            BigButton(tr("Yes, join", "हाँ, जुड़ें")) {
                scope.launch { runCatching { Api.post("$B/invites/accept", json("token" to code)) }.onSuccess { onChanged() }.onFailure { toast(ctx, it.message ?: "") } }
            }
        }
    }
    Section(tr("Or start your own", "या अपना बनाएं"), Icons.Default.Group) {
        OutlinedTextField(name, { name = it.take(40) }, label = { Text(tr("Family name", "परिवार का नाम")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
        BigButton(tr("Create family circle", "परिवार सर्कल बनाएं"), enabled = name.trim().length >= 2) {
            scope.launch { runCatching { Api.post("$B/group", json("name" to name.trim())) }.onSuccess { onChanged() }.onFailure { toast(ctx, it.message ?: "") } }
        }
    }
}
