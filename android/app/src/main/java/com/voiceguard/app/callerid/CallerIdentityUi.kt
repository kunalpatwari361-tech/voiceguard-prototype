package com.voiceguard.app.callerid

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Badge
import androidx.compose.material.icons.filled.DeleteForever
import androidx.compose.material.icons.filled.Flag
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.asList
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.int
import com.voiceguard.app.data.json
import com.voiceguard.app.data.str
import com.voiceguard.app.ui.Chip
import com.voiceguard.app.ui.Screen
import com.voiceguard.app.ui.Section
import com.voiceguard.app.ui.SmallButton
import com.voiceguard.app.ui.ToggleRow
import com.voiceguard.app.ui.VG
import com.voiceguard.app.ui.toast
import com.voiceguard.app.ui.tr
import kotlinx.coroutines.launch

@Composable
private fun sourceText(s: IdSource): String = when (s) {
    IdSource.FAMILY -> tr("Your family circle", "आपका परिवार सर्कल")
    IdSource.CONTACT -> tr("Your saved contact", "आपका सेव संपर्क")
    IdSource.VERIFIED_BUSINESS -> tr("Verified business (checked by VoiceGuard)", "सत्यापित व्यवसाय (VoiceGuard ने जाँचा)")
    IdSource.TELECOM -> tr("Authorised telecom source", "अधिकृत टेलीकॉम स्रोत")
    IdSource.VOICEGUARD_USER -> tr("VoiceGuard member – number confirmed by OTP, name not verified", "VoiceGuard सदस्य – नंबर OTP से पक्का, नाम सत्यापित नहीं")
    IdSource.COMMUNITY -> tr("Community suggestion – not verified", "समुदाय का सुझाव – सत्यापित नहीं")
    IdSource.UNKNOWN -> tr("No source", "कोई स्रोत नहीं")
}

@Composable
private fun StatusChips(id: CallerId) {
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        when (id.status) {
            "verified" -> Chip(tr("✓ Identity verified", "✓ पहचान सत्यापित"), VG.green)
            "saved" -> Chip(tr("Saved by you", "आपने सेव किया"), VG.blue)
            "number_confirmed" -> Chip(tr("Number confirmed", "नंबर पक्का"), VG.blue)
            "conflicting" -> Chip(tr("Names disagree", "नाम अलग-अलग"), VG.amber)
            "unknown" -> Chip(tr("Identity unknown", "पहचान अज्ञात"), VG.muted)
            else -> Chip(tr("Not verified", "सत्यापित नहीं"), VG.amber)
        }
        when (id.numberCheck) {   // the carrier's check of the number, separate from the name
            NumberCheck.PASSED -> Chip(tr("Carrier check: passed", "ऑपरेटर जाँच: सही"), VG.green)
            NumberCheck.FAILED -> Chip(tr("Carrier check: FAILED", "ऑपरेटर जाँच: विफल"), VG.red)
            NumberCheck.NOT_VERIFIED -> Chip(tr("Carrier check: none", "ऑपरेटर जाँच: नहीं"), VG.muted)
            NumberCheck.UNAVAILABLE -> Chip(tr("Carrier check: not available", "ऑपरेटर जाँच: उपलब्ध नहीं"), VG.muted)
        }
    }
}

/**
 * Caller Identity card: name, its source, identity status, the carrier's number check and - separately - the spam risk.
 * [compact] for the ringing call screen.
 */
@Composable
fun CallerIdentityCard(number: String, check: NumberCheck = NumberCheck.UNAVAILABLE, compact: Boolean = false) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var id by remember(number) { mutableStateOf<CallerId?>(null) }
    var why by remember { mutableStateOf(false) }
    var dialog by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(number, check) { if (number.isNotBlank() && Prefs.callerIdEnabled) id = CallerIdentity.lookup(ctx, number, check) }
    val r = id ?: return
    Section(if (compact) null else tr("Caller identity", "कॉलर की पहचान"), Icons.Default.Badge,
        when (r.status) { "verified" -> VG.green; "saved", "number_confirmed" -> VG.blue; "unknown" -> VG.muted; else -> VG.amber }) {
        Text(r.name ?: tr("Unknown caller", "अज्ञात कॉलर"), fontWeight = FontWeight.Bold, fontSize = 18.sp)
        if (!compact) Text(Numbers.pretty(r.number), color = VG.muted, fontSize = 14.sp)
        Text(tr("Source: ", "स्रोत: ") + sourceText(r.source), color = VG.muted, fontSize = 13.sp)
        StatusChips(r)
        r.spamScore?.let { s ->   // a different question from "who is it": shown on its own
            Chip(tr("Spam risk", "स्पैम जोखिम") + " ${(s * 100).toInt()}%" + (r.spamLabel?.let { " · $it" } ?: ""), VG.score(s))
        }
        if (r.source == IdSource.UNKNOWN) Text(tr("We could not verify who this is. Unknown callers are not blocked for that alone.",
            "हम पक्का नहीं कर सके कि यह कौन है। सिर्फ़ इस वजह से अनजान कॉलर ब्लॉक नहीं होते।"), color = VG.muted, fontSize = 13.sp)
        if (why) {
            Text(r.detail, fontSize = 13.sp)
            r.telecomNote?.let { Text(tr("Telecom identity: ", "टेलीकॉम पहचान: ") + it, color = VG.muted, fontSize = 12.sp) }
            Text(tr("A name is not proof of who is speaking – the AI voice check and family verification answer that.",
                "नाम इस बात का सबूत नहीं कि कौन बोल रहा है – AI आवाज़ जाँच और परिवार से पुष्टि यह बताते हैं।"), color = VG.muted, fontSize = 12.sp)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            TextButton({ why = !why }) { Text(if (why) tr("Hide source", "स्रोत छुपाएं") else tr("Why this name?", "यह नाम क्यों?")) }
            if (r.source != IdSource.FAMILY && r.source != IdSource.CONTACT) {
                TextButton({ dialog = "name" }) { Text(tr("Wrong name?", "गलत नाम?")) }
                TextButton({ dialog = "spam" }) { Text(tr("Report spam", "स्पैम रिपोर्ट")) }
            }
        }
    }
    dialog?.let { kind ->
        var text by remember(kind) { mutableStateOf("") }
        AlertDialog(
            onDismissRequest = { dialog = null },
            title = { Text(if (kind == "name") tr("Suggest the correct name", "सही नाम बताएं") else tr("Report suspected spam", "संदिग्ध स्पैम रिपोर्ट करें")) },
            text = {
                Column {
                    Text(tr("A moderator reviews it. Others see it only as an unverified community suggestion.",
                        "मॉडरेटर इसे जाँचेगा। दूसरों को यह सिर्फ़ असत्यापित सुझाव की तरह दिखेगा।"), color = VG.muted, fontSize = 13.sp)
                    OutlinedTextField(text, { text = it.take(if (kind == "name") 60 else 200) }, singleLine = kind == "name",
                        label = { Text(if (kind == "name") tr("Name", "नाम") else tr("What happened? (optional)", "क्या हुआ? (वैकल्पिक)")) },
                        modifier = Modifier.fillMaxWidth())
                }
            },
            confirmButton = {
                TextButton({
                    scope.launch {
                        val body = if (kind == "name") json("number" to number, "name" to text) else json("number" to number, "reason" to text)
                        runCatching { Api.post(if (kind == "name") "/api/caller-identity/report-name" else "/api/caller-identity/report-spam", body).asObj() }
                            .onSuccess { toast(ctx, tr("Thank you – sent for review.", "धन्यवाद – जाँच के लिए भेजा गया।")) }
                            .onFailure { toast(ctx, it.message ?: "Error") }
                        dialog = null
                    }
                }, enabled = kind == "spam" || text.trim().length >= 2) { Text(tr("Send", "भेजें")) }
            },
            dismissButton = { TextButton({ dialog = null }) { Text(tr("Cancel", "रद्द करें")) } },
        )
    }
}

/** Settings → Caller Identity & Verification: controls, a number check, where names come from, my submissions. */
@Composable
fun CallerIdentityScreen(back: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var enabled by remember { mutableStateOf(Prefs.callerIdEnabled) }
    var contacts by remember { mutableStateOf(Prefs.callerIdContacts) }
    var online by remember { mutableStateOf(Prefs.callerIdOnline) }
    var query by remember { mutableStateOf("") }
    var checking by remember { mutableStateOf<String?>(null) }
    var mine by remember { mutableStateOf<List<kotlinx.serialization.json.JsonObject>?>(null) }
    var confirmDelete by remember { mutableStateOf(false) }
    suspend fun loadMine() { mine = runCatching { Api.get("/api/caller-identity/my-submissions").asList().mapNotNull { it as? kotlinx.serialization.json.JsonObject } }.getOrNull() }
    LaunchedEffect(Unit) { loadMine() }

    Screen(tr("Caller Identity & Verification", "कॉलर पहचान और सत्यापन"), back) {
        Section(tr("Settings", "सेटिंग्स"), Icons.Default.Settings) {
            ToggleRow(tr("Caller identification", "कॉलर पहचान"), enabled) { enabled = it; Prefs.callerIdEnabled = it }
            ToggleRow(tr("Match my contacts (on this phone, offline)", "मेरे संपर्कों से मिलाएं (इसी फ़ोन पर, ऑफ़लाइन)"), contacts) { contacts = it; Prefs.callerIdContacts = it }
            ToggleRow(tr("Online identity lookups for unknown numbers", "अनजान नंबरों के लिए ऑनलाइन पहचान"), online) { online = it; Prefs.callerIdOnline = it }
            Text(tr("Your contact list is never uploaded. Online lookups send only the unknown caller's number.",
                "आपकी संपर्क सूची कभी अपलोड नहीं होती। ऑनलाइन जाँच में सिर्फ़ अनजान कॉलर का नंबर भेजा जाता है।"), color = VG.muted, fontSize = 13.sp)
        }
        Section(tr("Check a number", "नंबर जाँचें"), Icons.Default.Search) {
            OutlinedTextField(query, { query = it }, singleLine = true, label = { Text(tr("Phone number", "फ़ोन नंबर")) }, modifier = Modifier.fillMaxWidth())
            SmallButton(tr("Check", "जाँचें"), Icons.Default.Search, enabled = query.isNotBlank()) { checking = query.trim() }
        }
        checking?.let { CallerIdentityCard(it) }
        Section(tr("Where names come from", "नाम कहाँ से आते हैं"), Icons.Default.Info) {
            listOf(IdSource.FAMILY, IdSource.CONTACT, IdSource.VERIFIED_BUSINESS, IdSource.TELECOM, IdSource.VOICEGUARD_USER, IdSource.COMMUNITY)
                .forEach { Text("• " + sourceText(it), fontSize = 14.sp) }
            Text(tr("Official operator names (CNAP) appear in your phone's own call screen where your operator supports it. " +
                "VoiceGuard has no access to telecom subscriber records; an authorised telecom source will be added only through a licensed provider.",
                "आधिकारिक ऑपरेटर नाम (CNAP) आपके फ़ोन की अपनी कॉल स्क्रीन पर दिखते हैं जहाँ ऑपरेटर इसे देता है। VoiceGuard के पास टेलीकॉम ग्राहक रिकॉर्ड नहीं हैं।"),
                color = VG.muted, fontSize = 13.sp)
        }
        Section(tr("My submissions", "मेरे सुझाव"), Icons.Default.Flag) {
            val list = mine
            when {
                list == null -> Text(tr("Could not load (offline?).", "लोड नहीं हुआ (ऑफ़लाइन?)।"), color = VG.muted)
                list.isEmpty() -> Text(tr("You have not suggested any names or reported spam here.", "आपने यहाँ कोई नाम या स्पैम रिपोर्ट नहीं भेजी।"), color = VG.muted)
                else -> list.forEach { m ->
                    Text("• " + Numbers.pretty(m.str("number")) + " – " + (if (m.str("kind") == "name") tr("name: ", "नाम: ") + m.str("name") else tr("spam report", "स्पैम रिपोर्ट")) +
                        " (" + (m.str("status") ?: "") + ")", fontSize = 14.sp)
                }
            }
            if (!confirmDelete) SmallButton(tr("Delete my submissions", "मेरे सुझाव हटाएं"), Icons.Default.DeleteForever, enabled = !list.isNullOrEmpty()) { confirmDelete = true }
            else Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SmallButton(tr("Yes, delete", "हाँ, हटाएं"), Icons.Default.DeleteForever) {
                    scope.launch {
                        runCatching { Api.post("/api/caller-identity/my-submissions/delete").asObj() }
                            .onSuccess { r -> toast(ctx, tr("Deleted ${r.int("deleted") ?: 0}.", "${r.int("deleted") ?: 0} हटाए गए।")) }
                            .onFailure { toast(ctx, it.message ?: "Error") }
                        confirmDelete = false
                        loadMine()
                    }
                }
                SmallButton(tr("Cancel", "रद्द करें")) { confirmDelete = false }
            }
        }
    }
}
