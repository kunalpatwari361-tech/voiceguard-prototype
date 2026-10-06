package com.voiceguard.app.ui

import android.Manifest
import android.app.role.RoleManager
import android.net.Uri
import android.provider.CallLog
import android.telecom.TelecomManager
import android.text.format.DateUtils
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.CallMade
import androidx.compose.material.icons.automirrored.filled.CallMissed
import androidx.compose.material.icons.automirrored.filled.CallReceived
import androidx.compose.material.icons.filled.Backspace
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.Contacts
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.navigation.NavHostController
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.CallEntry
import com.voiceguard.app.data.Contact
import com.voiceguard.app.data.Contacts
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.str
import kotlinx.coroutines.delay
import kotlinx.serialization.json.JsonObject

/** VoiceGuard Dialer (feature 1): keypad, real call log and phone contacts, with scam labels. */
@Composable
fun DialerScreen(initial: String, nav: NavHostController, back: () -> Unit) {
    val ctx = LocalContext.current
    var tab by remember { mutableIntStateOf(if (initial.isNotBlank()) 0 else 1) }
    var contacts by remember { mutableStateOf<List<Contact>>(emptyList()) }
    var log by remember { mutableStateOf<List<CallEntry>>(emptyList()) }
    var permTick by remember { mutableIntStateOf(0) }
    val isDialer = remember { ctx.getSystemService(RoleManager::class.java).isRoleHeld(RoleManager.ROLE_DIALER) }
    val perms = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { permTick++ }
    val roleLauncher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { }

    LaunchedEffect(permTick) {
        contacts = Contacts.all(ctx, refresh = permTick > 0)
        log = Contacts.callLog(ctx)
    }

    fun place(n: String) {
        if (n.isBlank()) return
        if (isDialer) {
            runCatching { ctx.getSystemService(TelecomManager::class.java).placeCall(Uri.fromParts("tel", n, null), null) }
                .onFailure { dial(ctx, n) }
        } else dial(ctx, n)
    }

    val nameOf = remember(contacts) { contacts.associate { it.normalized to it.name } }

    Screen(tr("VoiceGuard Dialer", "VoiceGuard डायलर"), back, scroll = false) {
        if (!isDialer) Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(VG.surface).clickable {
            roleLauncher.launch(ctx.getSystemService(RoleManager::class.java).createRequestRoleIntent(RoleManager.ROLE_DIALER))
        }.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Default.Info, null, tint = VG.amber)
            Spacer(Modifier.width(8.dp))
            Text(tr("Tap to make VoiceGuard your Phone app – real calls then open with scam tools and live voice check.",
                "VoiceGuard को फ़ोन ऐप बनाने के लिए टैप करें – हर कॉल में स्कैम टूल और लाइव जाँच।"), fontSize = 13.sp, color = VG.amber)
        }
        TabRow(selectedTabIndex = tab, containerColor = VG.bg) {
            listOf(tr("Keypad", "कीपैड"), tr("Recents", "हाल के"), tr("Contacts", "संपर्क")).forEachIndexed { i, t ->
                Tab(tab == i, { tab = i }, text = { Text(t) })
            }
        }
        if ((tab == 1 && !Contacts.canReadLog(ctx)) || (tab == 2 && !Contacts.canReadContacts(ctx))) {
            BigButton(tr("Allow contacts & call history", "संपर्क व कॉल इतिहास की अनुमति दें"), Icons.Default.Contacts, VG.blue) {
                perms.launch(arrayOf(Manifest.permission.READ_CONTACTS, Manifest.permission.READ_CALL_LOG))
            }
        }
        when (tab) {
            0 -> KeypadTab(initial, contacts, nav, ::place)
            1 -> RecentsTab(log, nameOf, nav, ::place)
            else -> ContactsTab(contacts, nav, ::place)
        }
    }
}

@Composable
private fun KeypadTab(initial: String, contacts: List<Contact>, nav: NavHostController, place: (String) -> Unit) {
    var number by remember { mutableStateOf(initial) }
    var info by remember { mutableStateOf<JsonObject?>(null) }
    val digits = number.filter { it.isDigit() }
    val suggestions = remember(digits, contacts) { if (digits.length >= 2) contacts.filter { Contacts.matches(it, digits) }.take(4) else emptyList() }
    LaunchedEffect(number) {
        info = null
        if (digits.length >= 10) {
            delay(400)
            info = runCatching { Api.get("/api/numbers/${Uri.encode(number)}?user_id=${Prefs.userId}").asObj() }.getOrNull()
        }
    }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.verticalScroll(rememberScrollState())) {
        OutlinedTextField(number, { number = it }, singleLine = true, modifier = Modifier.fillMaxWidth(),
            textStyle = androidx.compose.ui.text.TextStyle(fontSize = 26.sp, fontWeight = FontWeight.Bold),
            trailingIcon = { IconButton({ number = number.dropLast(1) }) { Icon(Icons.Default.Backspace, null) } })
        suggestions.forEach { c -> PersonRow(c.name, c.number, null, onCall = { place(c.number) }) { number = c.number } }
        info?.let { NumberInfoCard(it) }
        listOf("123", "456", "789", "*0#").forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                row.forEach { ch ->
                    Box(Modifier.weight(1f).aspectRatio(2.3f).clip(RoundedCornerShape(14.dp)).background(VG.surface)
                        .clickable { number += ch }, contentAlignment = Alignment.Center) {
                        Text("$ch", fontSize = 26.sp, fontWeight = FontWeight.SemiBold)
                    }
                }
            }
        }
        BigButton(tr("Call", "कॉल करें"), Icons.Default.Call, enabled = number.isNotBlank()) { place(number) }
        if (number.length >= 5) SmallButton(tr("Number details", "नंबर जानकारी"), Icons.Default.Info) { nav.navigate("number/" + Uri.encode(number)) }
    }
}

@Composable
private fun RecentsTab(log: List<CallEntry>, nameOf: Map<String, String>, nav: NavHostController, place: (String) -> Unit) {
    val scam = Prefs.scamNumbers
    val blocked = Prefs.blockedNumbers
    androidx.compose.foundation.lazy.LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        items(log.size) { i ->
            val e = log[i]
            val n = Numbers.normalize(e.number)
            val label = when {
                n in blocked -> tr("Blocked", "ब्लॉक") to VG.red
                n in scam -> tr("Reported scam", "रिपोर्टेड स्कैम") to VG.red
                Sync.memberByPhone(n) != null -> tr("Family", "परिवार") to VG.green
                else -> null
            }
            val (icon, color) = when (e.type) {
                CallLog.Calls.MISSED_TYPE, CallLog.Calls.REJECTED_TYPE -> Icons.AutoMirrored.Filled.CallMissed to VG.red
                CallLog.Calls.OUTGOING_TYPE -> Icons.AutoMirrored.Filled.CallMade to VG.blue
                CallLog.Calls.BLOCKED_TYPE -> Icons.Default.Block to VG.muted
                else -> Icons.AutoMirrored.Filled.CallReceived to VG.green
            }
            val sub = DateUtils.getRelativeTimeSpanString(e.date).toString() +
                (if (e.durationS > 0) " · ${e.durationS / 60}:%02d".format(e.durationS % 60) else "")
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(icon, null, tint = color, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(6.dp))
                Box(Modifier.weight(1f)) {
                    PersonRow(e.name ?: nameOf[n] ?: Numbers.pretty(n), if (e.name != null || nameOf[n] != null) Numbers.pretty(n) + " · " + sub else sub,
                        label, onCall = { place(e.number) }) { nav.navigate("number/" + Uri.encode(e.number)) }
                }
            }
        }
        if (log.isEmpty()) item { Text(tr("No calls yet.", "अभी कोई कॉल नहीं।"), color = VG.muted) }
    }
}

@Composable
private fun ContactsTab(contacts: List<Contact>, nav: NavHostController, place: (String) -> Unit) {
    var q by remember { mutableStateOf("") }
    val fam = Sync.others()
    val shown = remember(q, contacts) {
        if (q.isBlank()) contacts else contacts.filter { it.name.contains(q, true) || it.number.filter(Char::isDigit).contains(q.filter(Char::isDigit).ifEmpty { "\u0000" }) }
    }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(q, { q = it }, singleLine = true, modifier = Modifier.fillMaxWidth(),
            leadingIcon = { Icon(Icons.Default.Search, null) }, placeholder = { Text(tr("Search ${contacts.size} contacts", "${contacts.size} संपर्क खोजें")) })
        androidx.compose.foundation.lazy.LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            if (q.isBlank() && fam.isNotEmpty()) {
                item { Text(tr("Family circle", "परिवार सर्कल"), color = VG.green, fontWeight = FontWeight.SemiBold) }
                items(fam.size) { i ->
                    val m = fam[i]
                    PersonRow(m.str("name").orEmpty(), Numbers.pretty(m.str("phone")), tr("Verified family", "परिवार") to VG.green,
                        onCall = { place(m.str("phone").orEmpty()) }) { nav.navigate("family") }
                }
                item { Text(tr("All contacts", "सभी संपर्क"), color = VG.muted, fontWeight = FontWeight.SemiBold) }
            }
            items(shown.size) { i ->
                val c = shown[i]
                PersonRow(c.name, c.number, if (c.starred) "★" to VG.amber else null, onCall = { place(c.number) }) {
                    nav.navigate("number/" + Uri.encode(c.number))
                }
            }
        }
    }
}

@Composable
private fun PersonRow(name: String, sub: String, label: Pair<String, Color>?, onCall: () -> Unit, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(VG.surface).clickable(onClick = onClick)
        .padding(horizontal = 10.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(38.dp).clip(CircleShape).background(VG.surface2), contentAlignment = Alignment.Center) {
            Text(name.trim().take(1).uppercase(), fontWeight = FontWeight.Bold, color = VG.blue)
        }
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(name, fontWeight = FontWeight.SemiBold, maxLines = 1, modifier = Modifier.weight(1f, fill = false))
                if (label != null) { Spacer(Modifier.width(6.dp)); Chip(label.first, label.second) }
            }
            Text(sub, color = VG.muted, fontSize = 12.sp, maxLines = 1)
        }
        IconButton(onCall) { Icon(Icons.Default.Call, "Call", tint = VG.green) }
    }
}
