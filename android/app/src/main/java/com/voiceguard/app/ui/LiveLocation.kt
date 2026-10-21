package com.voiceguard.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.Directions
import androidx.compose.material.icons.filled.LocationOn
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Live
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.bool
import com.voiceguard.app.data.int
import com.voiceguard.app.data.json
import com.voiceguard.app.data.num
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.str
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import org.osmdroid.tileprovider.tilesource.TileSourceFactory
import org.osmdroid.util.GeoPoint
import org.osmdroid.views.MapView
import org.osmdroid.views.overlay.Marker
import org.osmdroid.views.overlay.Polygon

/**
 * Live Location: a family member's position, updating every few seconds while this screen is open (server
 * live_location.py). Their phone shows a notification that they are being watched, with a Stop button.
 */
@Composable
fun LiveLocationScreen(memberId: String, back: () -> Unit) {
    val ctx = LocalContext.current
    val member = remember(memberId) { Sync.member(memberId) }
    val name = member?.str("name") ?: tr("Family member", "परिवार का सदस्य")
    var loc by remember { mutableStateOf(member.obj("location")) }
    var fixAt by remember { mutableLongStateOf(member.obj("location")?.int("age_min")?.let { System.currentTimeMillis() - it * 60_000L } ?: 0L) }
    var until by remember { mutableLongStateOf(0L) }
    var note by remember { mutableStateOf<String?>(null) }
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }

    LaunchedEffect(Unit) { while (true) { delay(1000); now = System.currentTimeMillis() } }
    LaunchedEffect(memberId) {
        runCatching { Api.post("/api/location/live/start", json("target_id" to memberId, "minutes" to 15)).asObj() }
            .onSuccess { r ->
                until = System.currentTimeMillis() + (r.int("seconds") ?: 900) * 1000L
                r.obj("user").obj("location")?.let { loc = it; fixAt = System.currentTimeMillis() - (it.int("age_min") ?: 0) * 60_000L }
                if (r.bool("online") == false) note = tr("$name's phone is offline right now – showing the last known place.",
                    "$name का फ़ोन अभी ऑफ़लाइन है – आख़िरी पता दिखा रहे हैं।")
            }
            .onFailure { note = it.message }
        Live.events.collect { e ->
            if (e.str("user_id") != memberId) return@collect
            when (e.str("type")) {
                "family_location" -> { loc = e.obj("location"); fixAt = System.currentTimeMillis(); note = null }
                "location_live_ended" -> {
                    until = 0L
                    note = tr("$name stopped sharing their live location.", "$name ने लाइव लोकेशन शेयर करना बंद किया।")
                }
            }
        }
    }
    DisposableEffect(memberId) {
        onDispose {   // closing the map ends the fast updates on their phone
            CoroutineScope(Dispatchers.IO).launch { runCatching { Api.post("/api/location/live/stop", json("target_id" to memberId)) } }
        }
    }

    Screen(tr("Live location", "लाइव लोकेशन"), back) {
        Text(name, fontWeight = FontWeight.Bold, fontSize = 22.sp)
        val l = loc
        if (l?.num("lat") != null) LiveMap(l)
        else Section { Text(tr("No location from $name's phone yet.", "$name के फ़ोन से अभी लोकेशन नहीं मिली।"), color = VG.muted) }
        if (l?.num("lat") != null) Section(tr("Where", "कहाँ"), Icons.Default.LocationOn, VG.blue) {
            Text(l.str("place") ?: "%.5f, %.5f".format(l.num("lat"), l.num("lon")), fontWeight = FontWeight.SemiBold, fontSize = 17.sp)
            val ago = ((now - fixAt) / 1000).coerceAtLeast(0)
            Text(tr("Updated ", "अपडेट: ") + if (ago < 60) tr("$ago s ago", "$ago सेकंड पहले") else tr("${ago / 60} min ago", "${ago / 60} मिनट पहले"),
                color = if (ago < 60) VG.green else VG.muted)
            l.num("accuracy")?.let { a -> Text(tr("Accurate to about ${a.toInt()} m", "लगभग ${a.toInt()} मीटर तक सही"), color = VG.muted) }
            l.int("battery")?.let { b -> Text(tr("Battery $b%", "बैटरी $b%"), color = if (b <= 15) VG.red else VG.muted) }
            Text(if (until > now) tr("● Live – ${(until - now) / 60_000 + 1} more min", "● लाइव – ${(until - now) / 60_000 + 1} मिनट और")
                 else tr("○ Not live", "○ लाइव नहीं"), color = if (until > now) VG.green else VG.muted)
        }
        note?.let { Banner(it, VG.amber) }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            BigButton(tr("Directions", "रास्ता"), Icons.Default.Directions, VG.blue, enabled = l?.num("lat") != null,
                modifier = Modifier.weight(1f)) {
                openUrl(ctx, "https://www.google.com/maps/dir/?api=1&destination=${l?.num("lat")},${l?.num("lon")}")
            }
            BigButton(tr("Call", "कॉल"), Icons.Default.Call, VG.green, enabled = member?.str("phone") != null,
                modifier = Modifier.weight(1f)) { member?.str("phone")?.let { dial(ctx, it) } }
        }
        Text(tr("$name sees a notification while you watch, and can stop sharing at any time.",
            "आपके देखते समय $name को सूचना दिखती है, और वे कभी भी शेयर करना बंद कर सकते हैं।"), color = VG.muted, fontSize = 13.sp)
    }
}

@Composable
private fun LiveMap(l: JsonObject) {
    val lat = l.num("lat") ?: return
    val lon = l.num("lon") ?: return
    AndroidView(factory = { c ->
        MapView(c).apply {
            setTileSource(TileSourceFactory.MAPNIK)
            setMultiTouchControls(true)
            controller.setZoom(16.0)
        }
    }, update = { map ->
        val p = GeoPoint(lat, lon)
        map.overlays.clear()
        l.num("accuracy")?.takeIf { it > 0 }?.let { a ->   // the "about this far" circle
            map.overlays.add(Polygon(map).apply {
                points = Polygon.pointsAsCircle(p, a)
                fillPaint.color = 0x332196F3
                outlinePaint.color = 0x882196F3.toInt()
                outlinePaint.strokeWidth = 2f
            })
        }
        map.overlays.add(Marker(map).apply { position = p; setAnchor(Marker.ANCHOR_CENTER, Marker.ANCHOR_BOTTOM); title = l.str("place") })
        map.controller.animateTo(p)
        map.invalidate()
    }, modifier = Modifier.fillMaxWidth().height(360.dp).clip(RoundedCornerShape(16.dp)))
}
