package com.voiceguard.app.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.filled.Contacts
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.FamilyRestroom
import androidx.compose.material.icons.filled.Group
import androidx.compose.material.icons.filled.PersonAdd
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.filled.Sms
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Contact
import com.voiceguard.app.data.Contacts
import com.voiceguard.app.data.Live
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.asList
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.bool
import com.voiceguard.app.data.json
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.str
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject

/**
 * Adding family by phone number. The person joins only after verifying that number on their own phone (OTP)
 * and tapping Join – joining shares call status and location, so it must be their choice. Until then they are
 * "waiting to join", and SMS / WhatsApp alerts from "Tell family" already reach them.
 */
private val RELATIONS = listOf("Parent" to ("Parent" to "माता-पिता"), "Child" to ("Son/Daughter" to "बेटा/बेटी"),
    "Spouse" to ("Husband/Wife" to "पति/पत्नी"), "Sibling" to ("Brother/Sister" to "भाई/बहन"), "Other" to ("Other" to "अन्य"))

fun relationLabel(r: String?): String = RELATIONS.firstOrNull { it.first == r }?.second?.let { tr(it.first, it.second) } ?: (r ?: "")

/** Saves the family the server returned, so every screen sees the change at once. */
fun keepFamily(f: JsonObject) {
    Prefs.familyId = f.str("id")
    Prefs.familyJson = f.toString()
}

fun inviteText(name: String, phone: String, code: String?): String = if (Prefs.hindi)
    "नमस्ते $name, मैंने आपको अपने VoiceGuard परिवार सर्कल में जोड़ा है ताकि हम AI आवाज़-क्लोन ठगी कॉल से एक-दूसरे को बचा सकें। " +
        "VoiceGuard इंस्टॉल करें, अपने नंबर ${Numbers.pretty(phone)} से साइन इन करें और 'Join' दबाएं" + (code?.let { " (या परिवार कोड $it डालें)" } ?: "") + "। – ${Prefs.name}"
else "Hi $name, I've added you to our VoiceGuard family circle so we can protect each other from AI voice-clone scam calls. " +
    "Install VoiceGuard, sign in with your number ${Numbers.pretty(phone)} and tap Join" + (code?.let { " (or enter family code $it)" } ?: "") + ". – ${Prefs.name}"

/** SMS · WhatsApp · Share buttons that send someone their invitation. */
@Composable
fun InviteButtons(name: String, phone: String, code: String?) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var note by remember { mutableStateOf<Pair<Boolean, String>?>(null) }
    val text = inviteText(name, phone, code)
    fun sms() = scope.launch {
        note = true to tr("Sending SMS…", "SMS भेज रहे हैं…")
        val failed = FamilyMessage.sendSms(ctx, listOf(name to phone), text)
        note = if (failed.isEmpty()) true to tr("Invite sent to $name by SMS.", "$name को SMS से न्योता भेजा।")
               else false to tr("SMS not sent – check signal / SMS balance.", "SMS नहीं गया – सिग्नल / बैलेंस जाँचें।")
    }
    val perm = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok -> if (ok) sms() }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        SmallButton("SMS", Icons.Default.Sms) {
            if (ctx.checkSelfPermission(Manifest.permission.SEND_SMS) == PackageManager.PERMISSION_GRANTED) sms()
            else perm.launch(Manifest.permission.SEND_SMS)
        }
        SmallButton("WhatsApp", Icons.AutoMirrored.Filled.Chat) {
            if (!FamilyMessage.whatsApp(ctx, phone, text)) note = false to tr("WhatsApp is not installed.", "WhatsApp इंस्टॉल नहीं है।")
        }
        IconButton({ shareText(ctx, text) }) { Icon(Icons.Default.Share, tr("Share", "शेयर"), tint = VG.blue) }
    }
    note?.let { (ok, t) -> Text(t, color = if (ok) VG.green else VG.amber, fontSize = 12.sp) }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AddMemberSheet(onChanged: (JsonObject) -> Unit, onDismiss: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var contacts by remember { mutableStateOf<List<Contact>>(emptyList()) }
    var tick by remember { mutableIntStateOf(0) }
    var q by remember { mutableStateOf("") }
    var name by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf("") }
    var relation by remember { mutableStateOf("Parent") }
    var busy by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf<String?>(null) }
    var added by remember { mutableStateOf<JsonObject?>(null) }
    val perm = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { tick++ }
    LaunchedEffect(tick) { contacts = Contacts.all(ctx, refresh = tick > 0) }
    val shown = remember(q, contacts) {
        contacts.filter { q.isBlank() || it.name.contains(q, true) || it.number.filter(Char::isDigit).contains(q.filter(Char::isDigit).ifEmpty { "\u0000" }) }.take(40)
    }

    ModalBottomSheet(onDismissRequest = onDismiss, containerColor = VG.bg, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp).padding(bottom = 28.dp).imePadding(),
            verticalArrangement = Arrangement.spacedBy(10.dp)) {
            val a = added
            if (a == null) {
                Text(tr("Add family member", "परिवार का सदस्य जोड़ें"), fontSize = 20.sp, fontWeight = FontWeight.Bold)
                Text(tr("Pick from your contacts or type their number. They join after confirming on their own phone.",
                    "संपर्कों से चुनें या नंबर लिखें। वे अपने फ़ोन पर पुष्टि करके जुड़ेंगे।"), color = VG.muted, fontSize = 13.sp)
                if (!Contacts.canReadContacts(ctx)) SmallButton(tr("Pick from contacts", "संपर्कों से चुनें"), Icons.Default.Contacts) {
                    perm.launch(Manifest.permission.READ_CONTACTS)
                } else {
                    OutlinedTextField(q, { q = it }, singleLine = true, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(28.dp),
                        leadingIcon = { Icon(Icons.Default.Search, null) }, placeholder = { Text(tr("Search ${contacts.size} contacts", "${contacts.size} संपर्क खोजें")) })
                    LazyColumn(Modifier.fillMaxWidth().heightIn(max = 230.dp)) {
                        items(shown) { c ->
                            val picked = Numbers.normalize(c.number) == Numbers.normalize(phone)
                            Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(if (picked) VG.green.copy(alpha = 0.15f) else VG.bg)
                                .clickable { name = c.name; phone = c.number }.padding(vertical = 6.dp, horizontal = 4.dp),
                                verticalAlignment = Alignment.CenterVertically) {
                                Avatar(c.name, 38.dp, avatarColor(c.name))
                                Spacer(Modifier.width(10.dp))
                                Column(Modifier.weight(1f)) {
                                    Text(c.name, fontWeight = FontWeight.SemiBold, maxLines = 1)
                                    Text(Numbers.pretty(c.number), color = VG.muted, fontSize = 12.sp)
                                }
                                if (picked) Text("✓", color = VG.green, fontWeight = FontWeight.Bold)
                            }
                        }
                    }
                }
                OutlinedTextField(name, { name = it }, label = { Text(tr("Name", "नाम")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(phone, { phone = it }, label = { Text(tr("Mobile number", "मोबाइल नंबर")) }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone), modifier = Modifier.fillMaxWidth())
                LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(RELATIONS) { (k, l) -> FilterChip(relation == k, { relation = k }, label = { Text(tr(l.first, l.second)) }) }
                }
                ErrorBox(err)
                if (busy) Busy(tr("Adding…", "जोड़ रहे हैं…"))
                else BigButton(tr("Add to family", "परिवार में जोड़ें"), Icons.Default.PersonAdd, VG.green,
                    enabled = name.isNotBlank() && phone.count { it.isDigit() } >= 10) {
                    scope.launch {
                        busy = true; err = null
                        runCatching {
                            Api.post("/api/family/${Prefs.familyId}/members", json("name" to name.trim(), "phone" to phone, "relation" to relation)).asObj()!!
                        }.onSuccess { r -> added = r; keepFamily(r); onChanged(r) }.onFailure { err = it.message }
                        busy = false
                    }
                }
            } else {
                val p = a.obj("added")
                val who = p.str("name").orEmpty()
                Banner(tr("✓ $who added", "✓ $who जोड़े गए"), VG.green,
                    if (a.bool("on_voiceguard") == true) tr("$who already uses VoiceGuard – a Join request is on their phone now.",
                        "$who पहले से VoiceGuard इस्तेमाल करते हैं – उनके फ़ोन पर Join का अनुरोध पहुँच गया है।")
                    else tr("$who is not on VoiceGuard yet. Send the invite below. SMS and WhatsApp alerts already reach them.",
                        "$who अभी VoiceGuard पर नहीं हैं। नीचे से न्योता भेजें। SMS और WhatsApp अलर्ट उन्हें अभी से मिलेंगे।"))
                Text(tr("Send the invite", "न्योता भेजें"), fontWeight = FontWeight.SemiBold)
                InviteButtons(who, p.str("phone").orEmpty(), a.str("invite_code"))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SmallButton(tr("Add another", "और जोड़ें"), Icons.Default.PersonAdd) { added = null; name = ""; phone = ""; q = "" }
                    SmallButton(tr("Done", "हो गया")) { onDismiss() }
                }
            }
        }
    }
}

/** Someone added this number to their family: Join / Not now. */
@Composable
fun InvitesCard(onJoined: (JsonObject) -> Unit) {
    val scope = rememberCoroutineScope()
    var invites by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var tick by remember { mutableIntStateOf(0) }
    var err by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(tick) { invites = runCatching { Api.get("/api/invites").asList() }.getOrDefault(emptyList()) }
    LaunchedEffect(Unit) { Live.events.collect { if (it.str("type") == "family_invite") tick++ } }
    invites.forEach { i ->
        Section(tr("Family invitation", "परिवार का न्योता"), Icons.Default.FamilyRestroom, VG.green) {
            Text(tr("${i.str("invited_by")} added you to “${i.str("family_name")}”.", "${i.str("invited_by")} ने आपको “${i.str("family_name")}” में जोड़ा है।"),
                fontWeight = FontWeight.SemiBold)
            Text(if (Prefs.familyId != null) tr("Joining moves you out of your current family circle.", "जुड़ने पर आप अपने मौजूदा परिवार सर्कल से निकल जाएंगे।")
                 else tr("Your family will see whether you are on a call and where you are, and get an alert if a scammer calls you.",
                    "परिवार देख पाएगा कि आप कॉल पर हैं या नहीं और कहाँ हैं, और ठगी कॉल पर उन्हें अलर्ट मिलेगा।"),
                color = VG.muted, fontSize = 13.sp)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                BigButton(tr("Join", "जुड़ें"), Icons.Default.Group, VG.green, modifier = Modifier.weight(1f)) {
                    scope.launch {
                        runCatching { Api.post("/api/invites/${i.str("id")}/accept").asObj()!! }
                            .onSuccess { keepFamily(it); Live.restart(); tick++; onJoined(it) }.onFailure { err = it.message }
                    }
                }
                SmallButton(tr("Not now", "अभी नहीं")) {
                    scope.launch { runCatching { Api.post("/api/invites/${i.str("id")}/decline") }; tick++ }
                }
            }
        }
    }
    ErrorBox(err)
}

/** Family tab before there is a circle: create one, or join with a code. */
@Composable
fun NoFamilyPanel(onJoined: (JsonObject) -> Unit) {
    val scope = rememberCoroutineScope()
    var code by remember { mutableStateOf("") }
    var err by remember { mutableStateOf<String?>(null) }
    Section(tr("Start your family circle", "अपना परिवार सर्कल शुरू करें"), Icons.Default.Group, VG.green) {
        Text(tr("Create the circle, then add your family by phone number.", "सर्कल बनाएं, फिर परिवार को फ़ोन नंबर से जोड़ें।"), color = VG.muted, fontSize = 13.sp)
        BigButton(tr("Create family circle", "परिवार सर्कल बनाएं"), Icons.Default.Group, VG.green) {
            scope.launch {
                runCatching { Api.post("/api/family", json("user_id" to Prefs.userId, "name" to "${Prefs.name}'s family")).asObj()!! }
                    .onSuccess { keepFamily(it); onJoined(it) }.onFailure { err = it.message }
            }
        }
        Text(tr("Or join a family that already exists, with its 6-digit code:", "या 6 अंकों के कोड से मौजूदा परिवार में जुड़ें:"), color = VG.muted, fontSize = 13.sp)
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(code, { code = it.filter(Char::isDigit).take(6) }, label = { Text(tr("Family code", "परिवार कोड")) }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.weight(1f))
            Spacer(Modifier.width(8.dp))
            SmallButton(tr("Join", "जुड़ें"), enabled = code.length == 6) {
                scope.launch {
                    runCatching { Api.post("/api/family/join", json("user_id" to Prefs.userId, "code" to code)).asObj()!! }
                        .onSuccess { keepFamily(it); Live.restart(); onJoined(it) }.onFailure { err = it.message }
                }
            }
        }
        ErrorBox(err)
    }
}

/** A member who was added by number and has not joined yet. */
@Composable
fun PendingMemberCard(p: JsonObject, code: String?, onChanged: (JsonObject) -> Unit) {
    val scope = rememberCoroutineScope()
    val name = p.str("name").orEmpty()
    Section {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Avatar(name, 44.dp, VG.amber)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(name, fontWeight = FontWeight.Bold, fontSize = 17.sp)
                Text(Numbers.pretty(p.str("phone")) + " · " + relationLabel(p.str("relation")), color = VG.muted, fontSize = 13.sp)
                Text(tr("Invited – waiting for them to join. SMS / WhatsApp alerts reach them.", "न्योता भेजा – जुड़ने का इंतज़ार। SMS / WhatsApp अलर्ट उन्हें मिलते हैं।"),
                    color = VG.amber, fontSize = 12.sp)
            }
            IconButton({
                scope.launch {
                    runCatching { Api.delete("/api/family/${Prefs.familyId}/invites/${p.str("id")}").asObj()!! }.onSuccess { keepFamily(it); onChanged(it) }
                }
            }) { Icon(Icons.Default.Delete, tr("Remove", "हटाएं"), tint = VG.muted) }
        }
        InviteButtons(name, p.str("phone").orEmpty(), code)
    }
}
