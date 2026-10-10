package com.voiceguard.app.ui

import android.Manifest
import android.app.role.RoleManager
import android.net.Uri
import android.provider.CallLog
import android.text.format.DateUtils
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.CallMade
import androidx.compose.material.icons.automirrored.filled.CallMissed
import androidx.compose.material.icons.automirrored.filled.CallReceived
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.Contacts
import androidx.compose.material.icons.filled.Dialpad
import androidx.compose.material.icons.filled.FamilyRestroom
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Sms
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.navigation.NavHostController
import com.voiceguard.app.data.CallEntry
import com.voiceguard.app.data.Contact
import com.voiceguard.app.data.Contacts
import com.voiceguard.app.data.Live
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sms
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.str
import com.voiceguard.app.service.CallListenService
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

/** What the app opens to: a phone app with VoiceGuard built in – Calls · Messages · Contacts · Family · Protect. */
@Composable
fun MainTabs(nav: NavHostController) {
    val ctx = LocalContext.current
    var tab by rememberSaveable { mutableIntStateOf(0) }
    var unread by remember { mutableIntStateOf(0) }
    var tick by remember { mutableIntStateOf(0) }
    val place = rememberPlacer()
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { tick++ }
    LaunchedEffect(tick) { unread = Sms.unread(ctx) }

    Scaffold(containerColor = VG.bg, bottomBar = {
        NavigationBar(containerColor = VG.surface) {
            val colors = NavigationBarItemDefaults.colors(selectedIconColor = VG.blue, selectedTextColor = VG.blue,
                indicatorColor = VG.blue.copy(alpha = 0.16f), unselectedIconColor = VG.muted, unselectedTextColor = VG.muted)
            @Composable
            fun item(i: Int, icon: ImageVector, label: String, badge: Int = 0) = NavigationBarItem(tab == i, { tab = i }, icon = {
                if (badge > 0) BadgedBox(badge = { Badge { Text(if (badge > 99) "99+" else "$badge") } }) { Icon(icon, label) }
                else Icon(icon, label)
            }, label = { Text(label, fontSize = 11.sp, maxLines = 1) }, colors = colors)
            item(0, Icons.Default.Call, tr("Calls", "कॉल"))
            item(1, Icons.Default.Sms, tr("Messages", "मैसेज"), unread)
            item(2, Icons.Default.Contacts, tr("Contacts", "संपर्क"))
            item(3, Icons.Default.FamilyRestroom, tr("Family", "परिवार"))
            item(4, Icons.Default.Shield, tr("Protect", "सुरक्षा"))
        }
    }) { pad ->
        Box(Modifier.padding(pad).consumeWindowInsets(pad).fillMaxSize()) {
            when (tab) {
                0 -> CallsTab(nav, place) { tab = 4 }
                1 -> MessagesTab(nav)
                2 -> ContactsPage(nav, place)
                3 -> FamilyScreen(nav, null)
                else -> HomeScreen(nav)
            }
        }
    }
}

private enum class CallFilter { ALL, OUTGOING, INCOMING, MISSED, BLOCKED, STARRED }

/** Calls tab, like the phone's own: search, frequently called, call history, dial-pad button. */
@Composable
private fun CallsTab(nav: NavHostController, place: (String) -> Unit, openProtect: () -> Unit) {
    val ctx = LocalContext.current
    var log by remember { mutableStateOf<List<CallEntry>>(emptyList()) }
    var contacts by remember { mutableStateOf<List<Contact>>(emptyList()) }
    var q by rememberSaveable { mutableStateOf("") }
    var filter by rememberSaveable { mutableStateOf(CallFilter.ALL) }
    var menu by remember { mutableStateOf(false) }
    var tick by remember { mutableIntStateOf(0) }
    var loaded by remember { mutableStateOf(false) }
    val connected by Live.connected.collectAsState()
    val perms = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { tick++ }
    val role = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { tick++ }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { tick++ }          // a call just ended → fresh history
    LaunchedEffect(tick) { contacts = Contacts.all(ctx, refresh = true); log = Contacts.callLog(ctx, 400); loaded = true }

    val nameOf = remember(contacts) { contacts.associate { it.normalized to it.name } }
    val starred = remember(contacts) { contacts.filter { it.starred }.map { it.normalized }.toSet() }
    fun nameFor(e: CallEntry) = Sync.memberByPhone(e.number)?.str("name")
        ?: e.name?.takeIf { it.isNotBlank() } ?: nameOf[Numbers.normalize(e.number)]
    val shown = remember(log, filter, q, contacts) {
        log.filter { e ->
            when (filter) {
                CallFilter.ALL -> true
                CallFilter.OUTGOING -> e.type == CallLog.Calls.OUTGOING_TYPE
                CallFilter.INCOMING -> e.type == CallLog.Calls.INCOMING_TYPE
                CallFilter.MISSED -> e.type == CallLog.Calls.MISSED_TYPE || e.type == CallLog.Calls.REJECTED_TYPE
                CallFilter.BLOCKED -> e.type == CallLog.Calls.BLOCKED_TYPE || Numbers.normalize(e.number) in Prefs.blockedNumbers
                CallFilter.STARRED -> Numbers.normalize(e.number) in starred
            }
        }.filter { e -> q.isBlank() || (nameFor(e) ?: "").contains(q, true) || e.number.filter(Char::isDigit).contains(q.filter(Char::isDigit).ifEmpty { "\u0000" }) }
    }
    val frequent = remember(log, contacts) {
        log.groupBy { Numbers.normalize(it.number) }.filterKeys { it.length >= 10 && it !in Prefs.blockedNumbers && it !in Prefs.scamNumbers }
            .entries.sortedByDescending { it.value.size }.take(10).map { it.value.first() }
    }
    val matches = remember(q, contacts) { if (q.length < 2) emptyList() else contacts.filter { it.name.contains(q, true) || Contacts.matches(it, q.filter(Char::isDigit)) }.take(5) }

    Box(Modifier.fillMaxSize()) {
        LazyColumn(contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 96.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp)) {
            item {
                Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(28.dp)).background(VG.surface).padding(start = 6.dp, end = 2.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Avatar(Prefs.name ?: "V", 36.dp, VG.blue)
                    Spacer(Modifier.width(10.dp))
                    Box(Modifier.weight(1f).padding(vertical = 14.dp)) {
                        if (q.isEmpty()) Text(tr("Search numbers, names…", "नंबर, नाम खोजें…"), color = VG.muted)
                        BasicTextField(q, { q = it }, singleLine = true, textStyle = TextStyle(color = VG.text, fontSize = 16.sp),
                            cursorBrush = SolidColor(VG.blue), modifier = Modifier.fillMaxWidth())
                    }
                    Icon(Icons.Default.Search, null, tint = VG.muted)
                    Box {
                        IconButton({ menu = true }) { Icon(Icons.Default.MoreVert, tr("Menu", "मेनू"), tint = VG.text) }
                        DropdownMenu(menu, { menu = false }, containerColor = VG.surface2) {
                            listOf(CallFilter.STARRED to (Icons.Default.Star to tr("Starred calls", "स्टार कॉल")),
                                CallFilter.OUTGOING to (Icons.AutoMirrored.Filled.CallMade to tr("Outgoing calls", "आउटगोइंग कॉल")),
                                CallFilter.INCOMING to (Icons.AutoMirrored.Filled.CallReceived to tr("Incoming calls", "इनकमिंग कॉल")),
                                CallFilter.MISSED to (Icons.AutoMirrored.Filled.CallMissed to tr("Missed calls", "मिस्ड कॉल")),
                                CallFilter.BLOCKED to (Icons.Default.Block to tr("Blocked calls", "ब्लॉक कॉल")),
                                CallFilter.ALL to (Icons.Default.Call to tr("All calls", "सभी कॉल"))).forEach { (f, v) ->
                                DropdownMenuItem(text = { Text(v.second, color = if (filter == f) VG.blue else VG.text) },
                                    leadingIcon = { Icon(v.first, null, tint = if (f == CallFilter.MISSED || f == CallFilter.BLOCKED) VG.red else VG.text) },
                                    onClick = { filter = f; menu = false })
                            }
                            DropdownMenuItem(text = { Text(tr("Settings", "सेटिंग्स")) }, leadingIcon = { Icon(Icons.Default.Settings, null) },
                                onClick = { menu = false; nav.navigate("settings") })
                        }
                    }
                }
                Spacer(Modifier.height(10.dp))
            }
            item { ProtectionCard(connected, onRole = {
                role.launch(ctx.getSystemService(RoleManager::class.java).createRequestRoleIntent(RoleManager.ROLE_DIALER))
            }, openProtect) }
            if (!Contacts.canReadLog(ctx)) item {
                BigButton(tr("Allow contacts & call history", "संपर्क व कॉल इतिहास की अनुमति दें"), Icons.Default.Contacts, VG.blue) {
                    perms.launch(arrayOf(Manifest.permission.READ_CONTACTS, Manifest.permission.READ_CALL_LOG))
                }
            }
            if (q.isBlank() && filter == CallFilter.ALL && frequent.isNotEmpty()) item {
                Text(tr("Frequently called", "अक्सर कॉल किए"), fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 12.dp, bottom = 8.dp))
                LazyRow(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    items(frequent) { e ->
                        val name = nameFor(e) ?: Numbers.pretty(e.number)
                        Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.width(68.dp)
                            .clip(RoundedCornerShape(12.dp)).clickable { place(e.number) }) {
                            Avatar(name, 56.dp, avatarColor(name), family = Sync.memberByPhone(e.number) != null)
                            Text(if (name.startsWith("+")) name.removePrefix("+91 ") else name.substringBefore(' '),
                                fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.padding(top = 4.dp))
                        }
                    }
                }
            }
            if (matches.isNotEmpty()) {
                item { Text(tr("Contacts", "संपर्क"), color = VG.muted, fontSize = 13.sp, modifier = Modifier.padding(top = 10.dp)) }
                items(matches) { c -> PersonRow(c.name, c.number, null, onCall = { place(c.number) }) { nav.navigate("number/" + Uri.encode(c.number)) } }
            }
            item {
                Text(when (filter) {
                    CallFilter.ALL -> tr("Recent calls", "हाल की कॉल")
                    CallFilter.OUTGOING -> tr("Outgoing calls", "आउटगोइंग कॉल")
                    CallFilter.INCOMING -> tr("Incoming calls", "इनकमिंग कॉल")
                    CallFilter.MISSED -> tr("Missed calls", "मिस्ड कॉल")
                    CallFilter.BLOCKED -> tr("Blocked calls", "ब्लॉक कॉल")
                    CallFilter.STARRED -> tr("Starred calls", "स्टार कॉल")
                }, color = VG.muted, fontSize = 13.sp, modifier = Modifier.padding(top = 12.dp, bottom = 4.dp))
            }
            items(shown.size) { i -> CallRow(shown[i], nameFor(shown[i]), place) { nav.navigate("number/" + Uri.encode(shown[i].number)) } }
            if (shown.isEmpty() && Contacts.canReadLog(ctx)) item {
                if (loaded) Text(tr("No calls here.", "यहाँ कोई कॉल नहीं।"), color = VG.muted) else Busy(tr("Loading calls…", "कॉल लोड हो रही हैं…"))
            }
        }
        FloatingActionButton({ nav.navigate("dialer") }, shape = CircleShape, containerColor = VG.blue, contentColor = Color.White,
            modifier = Modifier.align(Alignment.BottomEnd).padding(18.dp).size(62.dp)) {
            Icon(Icons.Default.Dialpad, tr("Keypad", "कीपैड"), modifier = Modifier.size(28.dp))
        }
    }
}

/** VoiceGuard's place on the Calls tab (where other dialers put adverts): protection status + one tap to fix. */
@Composable
private fun ProtectionCard(connected: Boolean, onRole: () -> Unit, openProtect: () -> Unit) {
    val ctx = LocalContext.current
    val isDialer = remember { ctx.getSystemService(RoleManager::class.java).isRoleHeld(RoleManager.ROLE_DIALER) }
    val listening = CallListenService.enabled(ctx)
    val ok = connected && isDialer
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(VG.surface).clickable(onClick = openProtect).padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Default.Shield, null, tint = if (ok) VG.green else VG.amber)
            Spacer(Modifier.width(8.dp))
            Text(if (ok) tr("VoiceGuard is protecting your calls", "VoiceGuard आपकी कॉल सुरक्षित रख रहा है")
                 else tr("Protection needs attention", "सुरक्षा पर ध्यान दें"), fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
            Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null, tint = VG.muted)
        }
        Text(when {
            !isDialer -> tr("Make VoiceGuard your Phone app so every call gets the AI check.", "VoiceGuard को फ़ोन ऐप बनाएं ताकि हर कॉल की AI जाँच हो।")
            !connected -> tr("Server offline – start the laptop server (same Wi-Fi or USB).", "सर्वर बंद – लैपटॉप सर्वर चालू करें (वही Wi-Fi या USB)।")
            !listening -> tr("AI checks unknown callers · tip: turn on call listening for the live check.", "AI अनजान कॉलर जाँचता है · सुझाव: कॉल लिसनिंग चालू करें।")
            else -> tr("AI checks unknown callers · family link live · scam SMS warnings on.", "AI अनजान कॉलर जाँचता है · परिवार लिंक चालू · स्कैम SMS चेतावनी चालू।")
        }, color = VG.muted, fontSize = 13.sp)
        if (!isDialer) SmallButton(tr("Set as Phone app", "फ़ोन ऐप बनाएं"), Icons.Default.Call, onClick = onRole)
    }
}

@Composable
private fun CallRow(e: CallEntry, name: String?, place: (String) -> Unit, details: () -> Unit) {
    val n = Numbers.normalize(e.number)
    val danger = n in Prefs.scamNumbers || n in Prefs.blockedNumbers
    val title = name ?: Numbers.pretty(e.number)
    val (icon, color, kind) = when (e.type) {
        CallLog.Calls.MISSED_TYPE, CallLog.Calls.REJECTED_TYPE -> Triple(Icons.AutoMirrored.Filled.CallMissed, VG.red, tr("Missed", "मिस्ड"))
        CallLog.Calls.OUTGOING_TYPE -> Triple(Icons.AutoMirrored.Filled.CallMade, VG.muted, tr("Outgoing", "आउटगोइंग"))
        CallLog.Calls.BLOCKED_TYPE -> Triple(Icons.Default.Block, VG.red, tr("Blocked", "ब्लॉक"))
        else -> Triple(Icons.AutoMirrored.Filled.CallReceived, VG.muted, tr("Incoming", "इनकमिंग"))
    }
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable { place(e.number) }.padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Avatar(title, 46.dp, if (danger) VG.red else avatarColor(title), family = Sync.memberByPhone(e.number) != null)
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(title, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    color = if (e.type == CallLog.Calls.MISSED_TYPE) VG.red else VG.text, modifier = Modifier.weight(1f, fill = false))
                when {
                    n in Prefs.blockedNumbers -> { Spacer(Modifier.width(6.dp)); Chip(tr("Blocked", "ब्लॉक"), VG.red) }
                    n in Prefs.scamNumbers -> { Spacer(Modifier.width(6.dp)); Chip(tr("Scam", "स्कैम"), VG.red) }
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(icon, null, tint = color, modifier = Modifier.size(14.dp))
                Text(" $kind · ${whenText(e.date)}", color = VG.muted, fontSize = 13.sp, maxLines = 1)
            }
        }
        IconButton(details) {
            Box(Modifier.size(30.dp).clip(CircleShape).background(VG.surface2), contentAlignment = Alignment.Center) {
                Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, tr("Details", "जानकारी"), tint = VG.muted)
            }
        }
    }
}

/** "5:10 pm" today, "Yesterday", "Thu" this week, else "31 May". */
fun whenText(t: Long): String {
    val now = Calendar.getInstance()
    val then = Calendar.getInstance().apply { timeInMillis = t }
    val days = ((now.timeInMillis - t) / DateUtils.DAY_IN_MILLIS).toInt()
    return when {
        now.get(Calendar.YEAR) == then.get(Calendar.YEAR) && now.get(Calendar.DAY_OF_YEAR) == then.get(Calendar.DAY_OF_YEAR) ->
            SimpleDateFormat("h:mm a", Locale.ENGLISH).format(Date(t)).lowercase()
        days < 2 && now.get(Calendar.DAY_OF_YEAR) - then.get(Calendar.DAY_OF_YEAR) == 1 -> tr("Yesterday", "कल")
        days < 7 -> SimpleDateFormat("EEE", Locale.ENGLISH).format(Date(t))
        else -> SimpleDateFormat("d MMM", Locale.ENGLISH).format(Date(t))
    }
}

private val AVATAR = listOf(Color(0xFF3B82F6), Color(0xFF22C55E), Color(0xFFA855F7), Color(0xFFF97316), Color(0xFF14B8A6), Color(0xFFEC4899))

fun avatarColor(name: String): Color = AVATAR[(name.hashCode() and 0x7fffffff) % AVATAR.size]

/** Round initial, like the phone's dialer. A small green shield marks verified family. */
@Composable
fun Avatar(name: String, size: androidx.compose.ui.unit.Dp, color: Color, family: Boolean = false) {
    Box(Modifier.size(size)) {
        Box(Modifier.size(size).clip(CircleShape).background(color.copy(alpha = 0.22f)), contentAlignment = Alignment.Center) {
            val letter = name.trim().firstOrNull { it.isLetterOrDigit() }?.uppercase() ?: "?"
            Text(letter, color = color, fontWeight = FontWeight.Bold, fontSize = (size.value * 0.42f).sp)
        }
        if (family) Box(Modifier.align(Alignment.BottomEnd).size(size * 0.36f).clip(CircleShape).background(VG.green),
            contentAlignment = Alignment.Center) {
            Icon(Icons.Default.Shield, null, tint = Color.Black, modifier = Modifier.size(size * 0.24f))
        }
    }
}

/** Contacts tab: family first, then the phone's contacts. */
@Composable
private fun ContactsPage(nav: NavHostController, place: (String) -> Unit) {
    val ctx = LocalContext.current
    var contacts by remember { mutableStateOf<List<Contact>>(emptyList()) }
    var tick by remember { mutableIntStateOf(0) }
    val perms = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { tick++ }
    LaunchedEffect(tick) { contacts = Contacts.all(ctx, refresh = tick > 0) }
    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(tr("Contacts", "संपर्क"), fontSize = 22.sp, fontWeight = FontWeight.Bold)
        if (!Contacts.canReadContacts(ctx)) BigButton(tr("Allow contacts", "संपर्कों की अनुमति दें"), Icons.Default.Contacts, VG.blue) {
            perms.launch(Manifest.permission.READ_CONTACTS)
        }
        Box(Modifier.weight(1f)) { ContactsTab(contacts, nav, place) }
    }
}
