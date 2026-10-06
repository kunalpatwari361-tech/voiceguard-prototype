package com.voiceguard.app.ui

import android.Manifest
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.CallLog
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
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Sms
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.navigation.NavHostController
import com.voiceguard.app.data.Contact
import com.voiceguard.app.data.Contacts
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Sms
import com.voiceguard.app.data.SmsGuard
import com.voiceguard.app.data.SmsMsg
import com.voiceguard.app.data.SmsThread
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private val SMS_PERMS = arrayOf(Manifest.permission.READ_SMS, Manifest.permission.RECEIVE_SMS)

/** Messages tab: the phone's SMS, each conversation checked for scam tricks on the phone. */
@Composable
fun MessagesTab(nav: NavHostController) {
    val ctx = LocalContext.current
    var threads by remember { mutableStateOf<List<SmsThread>?>(null) }
    var contacts by remember { mutableStateOf<List<Contact>>(emptyList()) }
    var q by rememberSaveable { mutableStateOf("") }
    var filter by rememberSaveable { mutableIntStateOf(0) }       // 0 all · 1 people · 2 suspicious
    var tick by remember { mutableIntStateOf(0) }
    val perms = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { tick++ }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { tick++ }
    LaunchedEffect(tick) { contacts = Contacts.all(ctx, refresh = true); threads = Sms.threads(ctx) }
    val nameOf = remember(contacts) { contacts.associate { it.normalized to it.name } }
    fun name(t: SmsThread) = if (t.address.any { it.isLetter() }) t.address else nameOf[Numbers.normalize(t.address)]

    val all = threads.orEmpty()
    val suspicious = all.count { it.worst.level != "safe" }
    val shown = all.filter { t ->
        when (filter) { 1 -> SmsGuard.isPersonalNumber(t.address); 2 -> t.worst.level != "safe"; else -> true }
    }.filter { t -> q.isBlank() || (name(t) ?: t.address).contains(q, true) || t.last.body.contains(q, true) }

    LazyColumn(contentPadding = PaddingValues(16.dp, 8.dp, 16.dp, 24.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        item {
            Text(tr("Messages", "मैसेज"), fontSize = 22.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(q, { q = it }, singleLine = true, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(28.dp),
                leadingIcon = { Icon(Icons.Default.Search, null) }, placeholder = { Text(tr("Search messages", "मैसेज खोजें")) })
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(vertical = 8.dp)) {
                FilterChip(filter == 0, { filter = 0 }, label = { Text(tr("All", "सभी")) })
                FilterChip(filter == 1, { filter = 1 }, label = { Text(tr("People", "लोग")) })
                FilterChip(filter == 2, { filter = 2 }, label = { Text(tr("Suspicious", "संदिग्ध") + if (suspicious > 0) " ($suspicious)" else "") })
            }
        }
        if (!Sms.canRead(ctx)) item {
            Banner(tr("See and check your SMS", "अपने SMS देखें और जाँचें"), VG.blue,
                tr("VoiceGuard reads messages on this phone only (nothing is uploaded), marks scam tricks, and warns you when a caller sends a code or link during a call.",
                    "VoiceGuard मैसेज सिर्फ़ इसी फ़ोन पर पढ़ता है (कुछ अपलोड नहीं होता), ठगी के संकेत दिखाता है, और कॉल के दौरान कोड/लिंक आने पर चेतावनी देता है।"))
            Spacer(Modifier.height(8.dp))
            BigButton(tr("Allow SMS", "SMS की अनुमति दें"), Icons.Default.Sms, VG.blue) { perms.launch(SMS_PERMS) }
        }
        if (threads == null && Sms.canRead(ctx)) item { Busy(tr("Reading messages…", "मैसेज पढ़ रहे हैं…")) }
        items(shown, key = { it.thread }) { t ->
            ThreadRow(t, name(t)) { nav.navigate("sms/" + Uri.encode(t.address)) }
        }
        if (threads != null && shown.isEmpty() && Sms.canRead(ctx)) item { Text(tr("No messages here.", "यहाँ कोई मैसेज नहीं।"), color = VG.muted) }
    }
}

@Composable
private fun ThreadRow(t: SmsThread, name: String?, onClick: () -> Unit) {
    val title = name ?: Numbers.pretty(t.address)
    val risk = t.worst.level
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable(onClick = onClick).padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Avatar(title, 46.dp, if (risk == "danger") VG.red else avatarColor(title))
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(title, fontWeight = if (t.unread > 0) FontWeight.Bold else FontWeight.SemiBold, fontSize = 16.sp, maxLines = 1,
                    overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                when (risk) {
                    "danger" -> { Spacer(Modifier.width(6.dp)); Chip(tr("Scam?", "स्कैम?"), VG.red) }
                    "caution" -> { Spacer(Modifier.width(6.dp)); Chip(tr("Careful", "सावधान"), VG.amber) }
                }
            }
            Text((if (!t.last.incoming) tr("You: ", "आप: ") else "") + t.last.body.replace('\n', ' '), color = if (t.unread > 0) VG.text else VG.muted,
                fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(whenText(t.last.date), color = if (t.unread > 0) VG.blue else VG.muted, fontSize = 12.sp)
            if (t.unread > 0) Box(Modifier.padding(top = 4.dp).size(20.dp).clip(CircleShape).background(VG.blue), contentAlignment = Alignment.Center) {
                Text("${t.unread}", color = Color.White, fontSize = 11.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}

/** One conversation: bubbles, scam warnings under risky messages, calls with this number, reply by SMS. */
@Composable
fun ConversationScreen(address: String, nav: NavHostController, back: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val place = rememberPlacer()
    var msgs by remember { mutableStateOf<List<SmsMsg>?>(null) }
    var calls by remember { mutableStateOf(0) }
    var text by remember { mutableStateOf("") }
    var note by remember { mutableStateOf<String?>(null) }
    var tick by remember { mutableIntStateOf(0) }
    val list = rememberLazyListState()
    val personal = SmsGuard.isPersonalNumber(address)
    val name = remember(address) { if (personal) Contacts.nameFor(ctx, address) else null }
    val perms = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { tick++ }
    val smsPerm = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    LaunchedEffect(tick) {
        msgs = Sms.withAddress(ctx, address)
        calls = Contacts.callLog(ctx, 400).count { Numbers.normalize(it.number) == Numbers.normalize(address) &&
            System.currentTimeMillis() - it.date < 7 * 86_400_000L }
        msgs?.let { if (it.isNotEmpty()) list.scrollToItem(it.size - 1) }
    }
    val verdicts = remember(msgs) { msgs.orEmpty().associate { it.id to if (it.incoming) SmsGuard.check(it.body, it.address) else SmsGuard.Verdict.NONE } }
    val risky = verdicts.values.count { it.level != "safe" }

    Screen(name ?: if (personal) Numbers.pretty(address) else address, back, scroll = false, actions = {
        if (personal) {
            IconButton({ place(address) }) { Icon(Icons.Default.Call, tr("Call", "कॉल"), tint = VG.green) }
            IconButton({ nav.navigate("number/" + Uri.encode(address)) }) { Icon(Icons.Default.Info, tr("Number info", "नंबर जानकारी")) }
        }
    }) {
        if (!Sms.canRead(ctx)) BigButton(tr("Allow SMS", "SMS की अनुमति दें"), Icons.Default.Sms, VG.blue) { perms.launch(SMS_PERMS) }
        if (risky > 0) Banner(tr("VoiceGuard found scam signs in $risky message(s)", "$risky मैसेज में ठगी के संकेत मिले"), VG.red,
            tr("Don't click links, don't share OTPs, don't pay. Report the number so your family is warned too.",
                "लिंक न खोलें, OTP न बताएं, पैसे न भेजें। नंबर रिपोर्ट करें ताकि परिवार भी सतर्क रहे।"))
        if (calls > 0) Text(tr("This number called you $calls time(s) in the last 7 days", "इस नंबर से पिछले 7 दिन में $calls बार कॉल आई"),
            color = VG.muted, fontSize = 13.sp)
        LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list, verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(msgs.orEmpty(), key = { it.id }) { m -> Bubble(m, verdicts[m.id] ?: SmsGuard.Verdict.NONE) }
        }
        if (risky > 0 && personal) SmallButton(tr("Report / block this number", "यह नंबर रिपोर्ट / ब्लॉक करें"), Icons.Default.Warning) {
            nav.navigate("number/" + Uri.encode(address))
        }
        note?.let { Text(it, color = VG.amber, fontSize = 13.sp) }
        if (personal) Row(Modifier.fillMaxWidth().imePadding(), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(text, { text = it }, modifier = Modifier.weight(1f), shape = RoundedCornerShape(24.dp), maxLines = 4,
                placeholder = { Text(tr("Text message", "मैसेज लिखें")) })
            IconButton({
                if (ctx.checkSelfPermission(Manifest.permission.SEND_SMS) != PackageManager.PERMISSION_GRANTED) {
                    smsPerm.launch(Manifest.permission.SEND_SMS); return@IconButton
                }
                val body = text.trim()
                if (body.isEmpty()) return@IconButton
                text = ""
                scope.launch {
                    val failed = FamilyMessage.sendSms(ctx, listOf((name ?: address) to address), body)
                    note = if (failed.isEmpty()) null else tr("Not sent – check signal / SMS balance.", "नहीं गया – सिग्नल / SMS बैलेंस जाँचें।")
                    delay(800); tick++
                }
            }, enabled = text.isNotBlank()) { Icon(Icons.AutoMirrored.Filled.Send, tr("Send", "भेजें"), tint = VG.blue) }
        } else Text(tr("This sender can't receive replies.", "इस भेजने वाले को जवाब नहीं जा सकता।"), color = VG.muted, fontSize = 12.sp)
    }
}

@Composable
private fun Bubble(m: SmsMsg, v: SmsGuard.Verdict) {
    val mine = !m.incoming
    Column(Modifier.fillMaxWidth(), horizontalAlignment = if (mine) Alignment.End else Alignment.Start) {
        Column(Modifier.widthIn(max = 300.dp).clip(RoundedCornerShape(18.dp))
            .background(if (mine) VG.blue.copy(alpha = 0.85f) else if (v.level == "danger") VG.red.copy(alpha = 0.18f) else VG.surface)
            .padding(horizontal = 14.dp, vertical = 10.dp)) {
            Text(m.body, color = if (mine) Color.White else VG.text, fontSize = 15.sp)
            Text(whenText(m.date), color = if (mine) Color.White.copy(alpha = 0.75f) else VG.muted, fontSize = 11.sp,
                modifier = Modifier.align(Alignment.End).padding(top = 2.dp))
        }
        if (v.level != "safe") Text("⚠ " + v.reasons.joinToString(" · "), color = if (v.level == "danger") VG.red else VG.amber,
            fontSize = 12.sp, modifier = Modifier.widthIn(max = 300.dp).padding(top = 2.dp, start = 4.dp))
        else if (v.isOtp) Text(tr("Code – never read it out to a caller.", "कोड – किसी कॉलर को कभी न बताएं।"), color = VG.amber,
            fontSize = 12.sp, modifier = Modifier.padding(top = 2.dp, start = 4.dp))
    }
}
