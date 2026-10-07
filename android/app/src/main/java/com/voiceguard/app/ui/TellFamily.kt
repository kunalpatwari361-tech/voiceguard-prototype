package com.voiceguard.app.ui

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.telephony.SmsManager
import android.telephony.SubscriptionManager
import android.telephony.TelephonyManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.filled.NotificationsActive
import androidx.compose.material.icons.filled.Sms
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.objs
import com.voiceguard.app.data.str
import com.voiceguard.app.service.Loc
import com.voiceguard.app.telecom.CallManager
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import java.util.Locale
import com.voiceguard.app.data.json
import com.voiceguard.app.data.bool
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.Api
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.LaunchedEffect
import kotlinx.coroutines.delay
import kotlinx.coroutines.Job

/**
 * Tell the family during a call WITHOUT hanging up: VoiceGuard app alert, SMS (sent straight from the phone, works
 * even if they don't have the app or internet) and WhatsApp (opens their chat with the message typed in –
 * WhatsApp only lets the person press Send themselves).
 */
object FamilyMessage {
    val WHATSAPP_GREEN = Color(0xFF25D366)

    /** (name, phone) of everyone else in the family circle. */
    fun recipients(): List<Pair<String, String>> =
        (Sync.others() + Sync.cachedFamily().objs("pending"))     // members added by number get SMS/WhatsApp too
            .mapNotNull { m -> m.str("phone")?.let { p -> (m.str("name") ?: Numbers.pretty(p)) to p } }
            .distinctBy { Numbers.normalize(it.second) }

    @Volatile private var fresh: android.location.Location? = null

    /** Get a fresh GPS fix (max 4 s) so the family gets an up-to-date map link. */
    suspend fun prepareLocation(ctx: Context) {
        fresh = withTimeoutOrNull(4000) { Loc.current(ctx) } ?: fresh
    }

    fun text(ctx: Context, number: String?, claimed: String?, score: Int?): String {
        val me = Prefs.name ?: "Your family member"
        val loc = (fresh?.takeIf { System.currentTimeMillis() - it.time < 15 * 60_000 } ?: Loc.lastKnownQuick(ctx))?.let { "https://maps.google.com/?q=%.5f,%.5f".format(Locale.US, it.latitude, it.longitude) }
        val caller = number?.takeIf { it.isNotBlank() }?.let { Numbers.pretty(it) }
        return if (Prefs.hindi) buildString {
            append("VoiceGuard चेतावनी: $me अभी एक संदिग्ध कॉल पर हैं।")
            caller?.let { append(" कॉलर: $it") }
            claimed?.let { append(", खुद को $it बता रहा है") }
            score?.let { append("। AI जोखिम $it/100") }
            append("। $me को अभी फ़ोन करें (${Prefs.phone.orEmpty()}) और किसी को पैसे न भेजें।")
            loc?.let { append(" लोकेशन: $it") }
        } else buildString {
            append("VoiceGuard ALERT: $me is on a suspicious call right now.")
            caller?.let { append(" Caller: $it") }
            claimed?.let { append(", says they are $it") }
            score?.let { append(". AI risk $it/100") }
            append(". Call $me now (${Prefs.phone.orEmpty()}) and do NOT send money to anyone.")
            loc?.let { append(" Location: $it") }
        }
    }

    /** SIM for the SMS: the one the call is on, else the default SMS SIM, else the first active SIM. */
    @SuppressLint("MissingPermission")
    private fun smsManager(ctx: Context): SmsManager {
        val invalid = SubscriptionManager.INVALID_SUBSCRIPTION_ID
        val sub = runCatching {
            val h = CallManager.call.value?.details?.accountHandle
            if (h != null && Build.VERSION.SDK_INT >= 30) ctx.getSystemService(TelephonyManager::class.java).getSubscriptionId(h) else invalid
        }.getOrDefault(invalid).takeIf { it != invalid }
            ?: SubscriptionManager.getDefaultSmsSubscriptionId().takeIf { it != invalid }
            ?: runCatching { ctx.getSystemService(SubscriptionManager::class.java).activeSubscriptionInfoList?.firstOrNull()?.subscriptionId }.getOrNull()
        @Suppress("DEPRECATION")
        return if (Build.VERSION.SDK_INT >= 31) ctx.getSystemService(SmsManager::class.java).let { if (sub != null) it.createForSubscriptionId(sub) else it }
               else if (sub != null) SmsManager.getSmsManagerForSubscriptionId(sub) else SmsManager.getDefault()
    }

    /** Sends the SMS and waits for the phone network's answer. Returns the names it could NOT be sent to. */
    suspend fun sendSms(ctx: Context, to: List<Pair<String, String>>, text: String): List<String> {
        val action = "${ctx.packageName}.SMS_SENT"
        val results = Channel<Pair<Int, Int>>(Channel.UNLIMITED)    // (recipient index, result code)
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) { results.trySend(i.getIntExtra("k", -1) to resultCode) }
        }
        ContextCompat.registerReceiver(ctx, receiver, IntentFilter(action), ContextCompat.RECEIVER_NOT_EXPORTED)
        val failed = mutableSetOf<Int>()
        try {
            val sm = smsManager(ctx)
            val parts = sm.divideMessage(text)
            var expected = 0
            to.forEachIndexed { k, (_, phone) ->
                val sent = ArrayList(parts.indices.map { j ->
                    PendingIntent.getBroadcast(ctx, 9000 + k * 20 + j, Intent(action).setPackage(ctx.packageName).putExtra("k", k),
                        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
                })
                runCatching { sm.sendMultipartTextMessage(phone, null, parts, sent, null) }
                    .onSuccess { expected += parts.size }.onFailure { failed += k }
            }
            // no answer within 30 s = still queued by the phone; only definite failures are reported
            withTimeoutOrNull(30_000) {
                repeat(expected) { val (k, code) = results.receive(); if (code != Activity.RESULT_OK) failed += k }
            }
        } finally {
            runCatching { ctx.unregisterReceiver(receiver) }
        }
        return to.filterIndexed { k, _ -> k in failed }.map { it.first }
    }

    /** Opens the WhatsApp chat with the message already typed. False if WhatsApp isn't installed. */
    fun whatsApp(ctx: Context, phone: String, text: String): Boolean {
        val digits = phone.filter { it.isDigit() }
        val uris = listOf(Uri.parse("https://wa.me/$digits?text=" + Uri.encode(text)),
            Uri.parse("whatsapp://send?phone=$digits&text=" + Uri.encode(text)))
        for (pkg in listOf("com.whatsapp", "com.whatsapp.w4b")) for (u in uris) {
            val ok = runCatching {
                ctx.startActivity(Intent(Intent.ACTION_VIEW, u).setPackage(pkg).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }.isSuccess
            if (ok) return true
        }
        return false
    }
}

/**
 * One tap, nothing else opens: **Alert everyone** = VoiceGuard app alert + SMS from this phone's SIM + WhatsApp sent
 * by the server (Twilio WhatsApp). Each send waits 3 seconds with a Cancel button so a mistaken tap can be undone.
 * Without server WhatsApp, the WhatsApp button falls back to opening the chat with the message typed in.
 */
@Composable
fun TellFamilyBar(message: () -> String, appAlert: suspend () -> Pair<Boolean, String>) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var lines by remember { mutableStateOf<List<Pair<Boolean, String>>>(emptyList()) }
    var busy by remember { mutableStateOf<String?>(null) }
    var pending by remember { mutableStateOf<String?>(null) }
    var seconds by remember { mutableIntStateOf(0) }
    var job by remember { mutableStateOf<Job?>(null) }
    var waDirect by remember { mutableStateOf(false) }
    var pickWhatsApp by remember { mutableStateOf(false) }
    val people = FamilyMessage.recipients()
    LaunchedEffect(Unit) { waDirect = runCatching { Api.get("/api/alerts/whatsapp/status").asObj().bool("enabled") == true }.getOrDefault(false) }

    fun smsAllowed() = ctx.checkSelfPermission(Manifest.permission.SEND_SMS) == PackageManager.PERMISSION_GRANTED

    suspend fun doSms(text: String): List<Pair<Boolean, String>> {
        if (!smsAllowed()) return listOf(false to tr("SMS: allow SMS permission first (tap SMS once).", "SMS: पहले SMS की अनुमति दें (SMS एक बार दबाएं)।"))
        val failed = FamilyMessage.sendSms(ctx, people, text)
        val ok = people.map { it.first }.filter { it !in failed }
        return listOfNotNull(
            ok.takeIf { it.isNotEmpty() }?.let { true to "SMS ✓ " + it.joinToString() },
            failed.takeIf { it.isNotEmpty() }?.let { false to "SMS ✗ " + it.joinToString() + tr(" (signal / SMS balance?)", " (सिग्नल / बैलेंस?)") })
    }

    /** WhatsApp from the server; null = server WhatsApp not available. */
    suspend fun doWhatsApp(text: String): List<Pair<Boolean, String>>? {
        val r = runCatching { Api.post("/api/alerts/whatsapp", json("text" to text, "person" to Prefs.name)).asObj() }
            .getOrElse { return listOf(false to "WhatsApp ✗ " + (it.message ?: "")) }
        if (r.bool("enabled") != true) return null
        return r.objs("results").map { x ->
            if (x.bool("ok") == true) true to "WhatsApp ✓ " + x.str("name")
            else false to "WhatsApp ✗ " + x.str("name") + " " + (x.str("error") ?: "")
        }
    }

    /** 3-second countdown with Cancel, then [action]. */
    fun later(what: String, action: suspend () -> Unit) {
        job?.cancel()
        job = scope.launch {
            pending = what
            val where = launch { FamilyMessage.prepareLocation(ctx) }     // runs during the countdown
            for (s in 3 downTo 1) { seconds = s; delay(1000) }
            where.join()
            pending = null
            busy = tr("Sending…", "भेज रहे हैं…")
            action()
            busy = null
        }
    }

    val smsPerm = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (ok) later("SMS") { lines = doSms(message()) }
        else lines = listOf(false to tr("SMS permission is needed to text your family.", "परिवार को SMS भेजने के लिए अनुमति चाहिए।"))
    }

    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(VG.surface).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(tr("Tell family – without hanging up", "परिवार को बताएं – कॉल काटे बिना"), fontWeight = FontWeight.SemiBold)
        BigButton(tr("Alert everyone now", "सबको अभी अलर्ट करें"), Icons.Default.NotificationsActive, VG.red,
            enabled = pending == null && busy == null) {
            if (people.isEmpty()) { lines = listOf(false to noFamily()); return@BigButton }
            later(tr("app alert + SMS + WhatsApp", "ऐप अलर्ट + SMS + WhatsApp")) {
                val text = message()
                val out = mutableListOf<Pair<Boolean, String>>()
                appAlert().let { (ok, t) -> out += ok to (tr("App ", "ऐप ") + (if (ok) "✓ " else "✗ ") + t) }
                out += doSms(text)
                out += doWhatsApp(text) ?: listOf(false to tr("WhatsApp: not set up on the server – use the WhatsApp button (opens WhatsApp).",
                    "WhatsApp: सर्वर पर चालू नहीं – WhatsApp बटन इस्तेमाल करें (WhatsApp खुलेगा)।"))
                lines = out
            }
        }
        Row(horizontalArrangement = Arrangement.SpaceEvenly, modifier = Modifier.fillMaxWidth()) {
            RoundAction(Icons.Default.NotificationsActive, tr("App alert", "ऐप अलर्ट"), VG.amber) {
                later(tr("app alert", "ऐप अलर्ट")) { appAlert().let { (ok, t) -> lines = listOf(ok to t) } }
            }
            RoundAction(Icons.Default.Sms, "SMS", VG.blue) {
                when {
                    people.isEmpty() -> lines = listOf(false to noFamily())
                    !smsAllowed() -> smsPerm.launch(Manifest.permission.SEND_SMS)
                    else -> later("SMS") { lines = doSms(message()) }
                }
            }
            RoundAction(Icons.AutoMirrored.Filled.Chat, "WhatsApp", FamilyMessage.WHATSAPP_GREEN) {
                if (people.isEmpty()) lines = listOf(false to noFamily())
                else scope.launch {
                    // ask the server every time: never open WhatsApp when the server can send it
                    if (!waDirect) waDirect = runCatching { Api.get("/api/alerts/whatsapp/status").asObj().bool("enabled") == true }.getOrDefault(false)
                    when {
                        waDirect -> later("WhatsApp") { lines = doWhatsApp(message()) ?: listOf(false to "WhatsApp ✗") }
                        people.size == 1 -> openWhatsApp(ctx, people[0], message()) { lines = listOf(it) }
                        else -> pickWhatsApp = true
                    }
                }
            }
        }
        if (pending != null) Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(VG.amber.copy(alpha = 0.15f))
            .padding(horizontal = 12.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(tr("Sending $pending in $seconds s…", "$seconds सेकंड में $pending भेज रहे हैं…"), color = VG.amber, fontSize = 13.sp,
                modifier = Modifier.weight(1f))
            TextButton({ job?.cancel(); pending = null }) { Text(tr("Cancel", "रद्द करें"), color = VG.amber, fontWeight = FontWeight.Bold) }
        }
        busy?.let { Busy(it) }
        lines.forEach { (ok, t) -> Text(t, color = if (ok) VG.green else VG.amber, fontSize = 13.sp) }
        if (lines.any { !it.first && it.second.startsWith("WhatsApp") } && people.isNotEmpty())
            SmallButton(tr("Open WhatsApp instead", "इसके बजाय WhatsApp खोलें"), Icons.AutoMirrored.Filled.Chat) {
                if (people.size == 1) openWhatsApp(ctx, people[0], message()) { lines = listOf(it) } else pickWhatsApp = true
            }
        if (!waDirect && people.isNotEmpty()) Text(tr("WhatsApp opens the chat (tap Send there). Direct WhatsApp needs the server's Twilio WhatsApp set up.",
            "WhatsApp में चैट खुलेगी (वहाँ Send दबाएं)। सीधे भेजने के लिए सर्वर पर Twilio WhatsApp चालू करें।"), color = VG.muted, fontSize = 11.sp)
    }

    if (pickWhatsApp) AlertDialog(
        onDismissRequest = { pickWhatsApp = false },
        title = { Text(tr("WhatsApp who?", "किसे WhatsApp करें?")) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(tr("WhatsApp opens with the message typed in – tap Send there, then come back to the call.",
                    "WhatsApp में संदेश लिखा मिलेगा – वहाँ Send दबाएं, फिर कॉल पर लौटें।"), color = VG.muted, fontSize = 13.sp)
                people.forEach { p ->
                    SmallButton(p.first, Icons.AutoMirrored.Filled.Chat, Modifier.fillMaxWidth()) {
                        pickWhatsApp = false
                        openWhatsApp(ctx, p, message()) { lines = listOf(it) }
                    }
                }
            }
        },
        confirmButton = { TextButton({ pickWhatsApp = false }) { Text(tr("Close", "बंद करें")) } },
    )
}

private fun openWhatsApp(ctx: Context, p: Pair<String, String>, text: String, done: (Pair<Boolean, String>) -> Unit) {
    done(if (FamilyMessage.whatsApp(ctx, p.second, text)) true to tr("WhatsApp opened for ${p.first} – tap Send.", "${p.first} के लिए WhatsApp खुला – Send दबाएं।")
         else false to tr("WhatsApp is not installed. Use SMS instead.", "WhatsApp इंस्टॉल नहीं है। SMS इस्तेमाल करें।"))
}

private fun noFamily() = tr("No family members yet – open Family and tap \"Add family member\".",
    "अभी परिवार में कोई नहीं – परिवार खोलकर \"परिवार का सदस्य जोड़ें\" दबाएं।")

@Composable
private fun RoundAction(icon: ImageVector, label: String, color: Color, onClick: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.clip(RoundedCornerShape(12.dp)).clickable(onClick = onClick).padding(6.dp)) {
        Box(Modifier.size(54.dp).clip(CircleShape).background(color.copy(alpha = 0.2f)), contentAlignment = Alignment.Center) {
            Icon(icon, label, tint = color, modifier = Modifier.size(28.dp))
        }
        Text(label, fontSize = 12.sp, modifier = Modifier.padding(top = 4.dp))
    }
}

/** "During the call" layer on the home screen: tell family by app alert, SMS or WhatsApp at any time. */
@Composable
fun TellFamilyScreen(back: () -> Unit) {
    val ctx = LocalContext.current
    Screen(tr("Tell family", "परिवार को बताएं"), back) {
        Banner(tr("Getting a suspicious call?", "संदिग्ध कॉल आ रही है?"), VG.amber,
            tr("Tell your family in one tap – without hanging up. During a VoiceGuard call these buttons are also on the call screen.",
                "एक टैप में परिवार को बताएं – कॉल काटे बिना। VoiceGuard कॉल के दौरान ये बटन कॉल स्क्रीन पर भी हैं।"))
        TellFamilyBar(message = { FamilyMessage.text(ctx, CallManager.number.takeIf { CallManager.call.value != null }, null, null) }) {
            notifyFamily("${Prefs.name} may be getting a scam call", "Call ${Prefs.name} now and do not send money to anyone.",
                mapOf("victim_phone" to Prefs.phone, "from_name" to Prefs.name))
        }
        if (FamilyMessage.recipients().isEmpty()) Text(noFamily(), color = VG.muted)
    }
}
