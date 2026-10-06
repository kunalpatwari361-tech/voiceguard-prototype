package com.voiceguard.app.ui

import android.Manifest
import android.app.NotificationManager
import android.app.role.RoleManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.Group
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Security
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.navigation.NavHostController
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Live
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.asObj
import com.voiceguard.app.data.json
import com.voiceguard.app.data.obj
import com.voiceguard.app.data.str
import com.voiceguard.app.service.GuardService
import com.voiceguard.app.service.Push
import com.voiceguard.app.data.bool
import androidx.compose.material.icons.filled.Notifications
import kotlinx.coroutines.launch
import com.voiceguard.app.data.int
import kotlinx.coroutines.delay
import androidx.compose.ui.unit.sp
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.material.icons.filled.Sms

@Composable
fun SetupScreen(onDone: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var step by remember { mutableIntStateOf(if (Prefs.registered) 2 else 0) }
    var server by remember { mutableStateOf(Prefs.serverUrl) }
    var name by remember { mutableStateOf(Prefs.name.orEmpty()) }
    var phone by remember { mutableStateOf(Prefs.phone.orEmpty()) }
    var role by remember { mutableStateOf(if (Prefs.registered) Prefs.role ?: "parent" else "parent") }
    var code by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf<String?>(null) }
    var err by remember { mutableStateOf<String?>(null) }
    var health by remember { mutableStateOf<String?>(null) }
    var otpSent by remember { mutableStateOf(false) }
    var otp by remember { mutableStateOf("") }
    var otpMode by remember { mutableStateOf<String?>(null) }
    var sentTo by remember { mutableStateOf("") }
    var resendIn by remember { mutableIntStateOf(0) }
    var smsRound by remember { mutableIntStateOf(0) }
    LaunchedEffect(resendIn) { if (resendIn > 0) { delay(1000); resendIn-- } }

    /** Check the code with the server; on success the phone is signed in. */
    fun verifyOtp() {
        if (otp.length != 6 || busy != null) return
        scope.launch {
            busy = "…"; err = null
            runCatching {
                Api.post("/api/auth/otp/verify", json("phone" to sentTo, "code" to otp, "name" to name.trim(), "role" to role)).asObj()
            }.onSuccess { r ->
                val u = r.obj("user")
                Prefs.token = r.str("token")
                Prefs.userId = u.str("id"); Prefs.name = u.str("name"); Prefs.phone = u.str("phone"); Prefs.role = role
                Prefs.familyId = u.str("family_id")
                runCatching { Sync.family() }
                Live.restart()
                otpSent = false
                step = 2
            }.onFailure { err = it.message }
            busy = null
        }
    }

    // Real SMS: Android offers to read the code from the message; one tap fills it in and verifies.
    SmsCodeListener(if (otpSent && otpMode !in listOf("console", "test")) smsRound else 0) { c -> otp = c; verifyOtp() }

    /** Ask the server to send a 6-digit code to this number (SMS, or the laptop window in demo mode). */
    fun sendOtp() {
        Prefs.serverUrlRaw = server.trim()
        scope.launch {
            busy = "…"; err = null
            runCatching { Api.post("/api/auth/otp/start", json("phone" to phone)).asObj() }
                .onSuccess { r ->
                    otpSent = true; otp = ""
                    otpMode = r.str("provider"); sentTo = r.str("phone") ?: phone
                    smsRound++
                    resendIn = r.int("resend_after") ?: 30
                }.onFailure { err = it.message }
            busy = null
        }
    }

    Screen(tr("Welcome to VoiceGuard", "VoiceGuard में स्वागत है"), onBack = null) {
        Text(tr("Stops AI voice-clone scam calls. Set up takes 2 minutes.", "AI आवाज़-क्लोन ठगी कॉल रोकता है। सेटअप में 2 मिनट लगेंगे।"), color = VG.muted)
        Row { FilterChip(Prefs.hindi, { Prefs.hindi = !Prefs.hindi; step = step }, label = { Text("हिंदी") }) }

        Section(tr("1. Server", "1. सर्वर"), Icons.Default.Dns) {
            OutlinedTextField(server, { server = it }, label = { Text("Server URL") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Text(tr("USB: keep 127.0.0.1:8000 and run 'adb reverse'. Wi-Fi: use the laptop's IP.", "USB: 127.0.0.1:8000 रखें। Wi-Fi: लैपटॉप का IP डालें।"), color = VG.muted)
            SmallButton(tr("Test connection", "कनेक्शन जाँचें")) {
                Prefs.serverUrlRaw = server.trim()
                scope.launch {
                    err = null; health = null
                    runCatching { Api.get("/api/health").asObj() }
                        .onSuccess { health = "✓ " + tr("Connected", "जुड़ गया") + " · AI: " + it.obj("models")?.filterValues { v -> v.str() == "true" }?.keys?.joinToString { k -> k.removePrefix("deepfake:") } }
                        .onFailure { err = it.message }
                }
            }
            health?.let { Text(it, color = VG.green) }
        }

        if (step >= 0) Section(tr("2. About you", "2. आपकी जानकारी"), Icons.Default.Person) {
            OutlinedTextField(name, { name = it }, label = { Text(tr("Your name", "आपका नाम")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(phone, { phone = it }, label = { Text(tr("Your mobile number", "आपका मोबाइल नंबर")) }, singleLine = true,
                enabled = !otpSent && !Prefs.registered,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone), modifier = Modifier.fillMaxWidth())
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("parent" to tr("Parent", "माता-पिता"), "child" to tr("Son/Daughter", "बेटा/बेटी"), "member" to tr("Other", "अन्य")).forEach { (k, l) ->
                    FilterChip(role == k, { role = k }, label = { Text(l) })
                }
            }
            if (Prefs.registered) {
                Text("✓ ${Prefs.name} · ${Prefs.phone} · " + tr("number verified", "नंबर सत्यापित"), color = VG.green)
            } else if (!otpSent) {
                if (Prefs.userId != null) Text(tr("Please verify your mobile number to continue using VoiceGuard.",
                    "VoiceGuard इस्तेमाल करते रहने के लिए अपना मोबाइल नंबर सत्यापित करें।"), color = VG.amber, fontSize = 13.sp)
                BigButton(tr("Send OTP", "OTP भेजें"), Icons.Default.Sms, enabled = name.isNotBlank() && phone.filter { it.isDigit() }.length >= 10 && busy == null) {
                    sendOtp()
                }
            } else {
                Text(tr("Enter the 6-digit code sent to $sentTo", "$sentTo पर भेजा गया 6 अंकों का कोड डालें"), fontWeight = FontWeight.SemiBold)
                if (otpMode == "console") Text(tr("Demo mode: no SMS is sent. The code is shown in the laptop server window (backend\\data\\dev_otp.log).",
                    "डेमो मोड: SMS नहीं भेजा जाता। कोड लैपटॉप सर्वर विंडो में दिखता है।"), color = VG.amber, fontSize = 13.sp)
                if (otpMode !in listOf("console", "test")) Text(tr("When the SMS arrives, tap Allow and the code is filled in for you.",
                    "SMS आने पर 'Allow' दबाएं, कोड अपने आप भर जाएगा।"), color = VG.muted, fontSize = 13.sp)
                OutlinedTextField(otp, { otp = it.filter { c -> c.isDigit() }.take(6) }, label = { Text("OTP") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                    textStyle = androidx.compose.ui.text.TextStyle(fontSize = 24.sp, letterSpacing = 8.sp, fontWeight = FontWeight.Bold),
                    modifier = Modifier.fillMaxWidth())
                BigButton(tr("Verify & continue", "सत्यापित करें"), Icons.Default.CheckCircle, enabled = otp.length == 6 && busy == null) {
                    verifyOtp()
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SmallButton(if (resendIn > 0) tr("Resend in ${resendIn}s", "${resendIn} सेकंड में दोबारा") else tr("Resend code", "कोड दोबारा भेजें"),
                        enabled = resendIn == 0 && busy == null) { sendOtp() }
                    SmallButton(tr("Change number", "नंबर बदलें")) { otpSent = false; otp = "" }
                }
            }
        }

        if (step >= 2) Section(tr("3. Family Circle", "3. परिवार सर्कल"), Icons.Default.Group) {
            if (Prefs.familyId != null) {
                val f = Sync.cachedFamily()
                Text("✓ ${f.str("name") ?: tr("Family", "परिवार")} · " + tr("invite code", "इनवाइट कोड") + " ${f.str("invite_code") ?: ""}", color = VG.green)
            } else {
                InvitesCard { step = 3 }
                Text(tr("One person creates the circle and adds the others by phone number (or they join with the 6-digit code).",
                    "एक व्यक्ति सर्कल बनाए और बाकी को फ़ोन नंबर से जोड़े (या वे 6 अंकों के कोड से जुड़ें)।"), color = VG.muted)
                BigButton(tr("Create family circle", "परिवार सर्कल बनाएं"), enabled = busy == null) {
                    scope.launch {
                        runCatching { Api.post("/api/family", json("user_id" to Prefs.userId, "name" to "${Prefs.name}'s family")).asObj() }
                            .onSuccess { Prefs.familyId = it.str("id"); Prefs.familyJson = it.toString(); step = 3 }
                            .onFailure { err = it.message }
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(code, { code = it }, label = { Text(tr("Invite code", "इनवाइट कोड")) }, singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.weight(1f))
                    Spacer(Modifier.width(8.dp))
                    SmallButton(tr("Join", "जुड़ें"), enabled = code.length == 6) {
                        scope.launch {
                            runCatching { Api.post("/api/family/join", json("user_id" to Prefs.userId, "code" to code)).asObj() }
                                .onSuccess { Prefs.familyId = it.str("id"); Prefs.familyJson = it.toString(); step = 3 }
                                .onFailure { err = it.message }
                        }
                    }
                }
                SmallButton(tr("Skip for now", "अभी छोड़ें")) { step = 3 }
            }
        }

        if (step >= 2) Section(tr("4. Permissions", "4. अनुमतियाँ"), Icons.Default.Security) { PermissionsPanel() }

        ErrorBox(err)
        if (step >= 2) BigButton(tr("Start protection", "सुरक्षा शुरू करें"), Icons.Default.CheckCircle) {
            Prefs.setupDone = true
            GuardService.start(ctx)
            onDone()
        }
    }
}

private val RUNTIME_PERMS = buildList {
    addAll(listOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.READ_PHONE_STATE, Manifest.permission.CALL_PHONE,
        Manifest.permission.ANSWER_PHONE_CALLS, Manifest.permission.READ_CONTACTS, Manifest.permission.READ_CALL_LOG,
        Manifest.permission.SEND_SMS, Manifest.permission.READ_SMS, Manifest.permission.RECEIVE_SMS,
        Manifest.permission.ACCESS_FINE_LOCATION,
        Manifest.permission.ACCESS_COARSE_LOCATION))
    if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
}

private fun has(ctx: Context, p: String) = ctx.checkSelfPermission(p) == android.content.pm.PackageManager.PERMISSION_GRANTED

/** Permissions + Android roles. Shared by setup and settings. */
@Composable
fun PermissionsPanel() {
    val ctx = LocalContext.current
    var tick by remember { mutableIntStateOf(0) }
    val refresh = { tick++ }
    val perms = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { refresh(); GuardService.start(ctx) }
    val roleLauncher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { refresh() }
    val rm = ctx.getSystemService(RoleManager::class.java)
    LaunchedEffect(Unit) { refresh() }
    key(tick) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            PermRow(tr("Microphone, phone, SMS, location, notifications", "माइक, फ़ोन, SMS, लोकेशन, सूचनाएं"), RUNTIME_PERMS.all { has(ctx, it) }) {
                perms.launch(RUNTIME_PERMS.toTypedArray())
            }
            PermRow(tr("Call screening (spam warning on real calls)", "कॉल स्क्रीनिंग (असली कॉल पर स्पैम चेतावनी)"),
                rm.isRoleHeld(RoleManager.ROLE_CALL_SCREENING)) { roleLauncher.launch(rm.createRequestRoleIntent(RoleManager.ROLE_CALL_SCREENING)) }
            PermRow(tr("VoiceGuard Dialer as phone app (optional)", "VoiceGuard डायलर को फ़ोन ऐप बनाएं (वैकल्पिक)"),
                rm.isRoleHeld(RoleManager.ROLE_DIALER)) { roleLauncher.launch(rm.createRequestRoleIntent(RoleManager.ROLE_DIALER)) }
            PermRow(tr("Live check during calls: VoiceGuard call listening (Accessibility)", "कॉल में लाइव जाँच: VoiceGuard call listening (Accessibility)"),
                com.voiceguard.app.service.CallListenService.enabled(ctx)) { com.voiceguard.app.service.CallListenService.openSettings(ctx) }
            PermRow(tr("Panic Pause: usage access", "पैनिक पॉज़: यूसेज एक्सेस"), GuardService.usageAccess(ctx)) {
                roleLauncher.launch(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
            }
            PermRow(tr("Panic Pause: show over other apps", "पैनिक पॉज़: दूसरे ऐप के ऊपर दिखाएं"), Settings.canDrawOverlays(ctx)) {
                roleLauncher.launch(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:${ctx.packageName}")))
            }
            if (Build.VERSION.SDK_INT >= 34) {
                val nm = ctx.getSystemService(NotificationManager::class.java)
                PermRow(tr("Full-screen alerts (verify requests)", "फ़ुल-स्क्रीन अलर्ट"), nm.canUseFullScreenIntent()) {
                    roleLauncher.launch(Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT, Uri.parse("package:${ctx.packageName}")))
                }
            }
        }
    }
}

@Composable
private fun key(k: Any, content: @Composable () -> Unit) = androidx.compose.runtime.key(k) { content() }

@Composable
private fun PermRow(label: String, ok: Boolean, onClick: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Icon(if (ok) Icons.Default.CheckCircle else Icons.Default.Security, null, tint = if (ok) VG.green else VG.amber)
        Spacer(Modifier.width(10.dp))
        Text(label, modifier = Modifier.weight(1f))
        if (!ok) SmallButton(tr("Allow", "अनुमति दें"), onClick = onClick)
    }
}

@Composable
fun SettingsScreen(nav: NavHostController, back: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var server by remember { mutableStateOf(Prefs.serverUrl) }
    var msg by remember { mutableStateOf<String?>(null) }
    var hindi by remember { mutableStateOf(Prefs.hindi) }
    var auto by remember { mutableStateOf(Prefs.autoCheck) }
    var panic by remember { mutableStateOf(Prefs.panicPause) }
    val connected by Live.connected.collectAsState()

    Screen(tr("Settings", "सेटिंग्स"), back) {
        Section(tr("Server", "सर्वर"), Icons.Default.Dns) {
            OutlinedTextField(server, { server = it }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Text(if (connected) tr("● Live link connected", "● लाइव लिंक जुड़ा है") else tr("○ Live link offline", "○ लाइव लिंक बंद है"),
                color = if (connected) VG.green else VG.red)
            SmallButton(tr("Save & reconnect", "सेव करें")) {
                Prefs.serverUrlRaw = server.trim(); Live.restart()
                scope.launch { msg = runCatching { Api.get("/api/health"); tr("Connected", "जुड़ गया") }.getOrElse { it.message } }
            }
            msg?.let { Text(it, color = VG.muted) }
        }
        Section(tr("Preferences", "पसंद"), Icons.Default.Settings) {
            ToggleRow("हिंदी / Hindi", hindi) { hindi = it; Prefs.hindi = it }
            ToggleRow(tr("Auto Check Every Call", "हर कॉल की ऑटो जाँच"), auto) { auto = it; Prefs.autoCheck = it }
            ToggleRow(tr("Panic Pause before payments", "पेमेंट से पहले पैनिक पॉज़"), panic) { panic = it; Prefs.panicPause = it }
        }
        Section(tr("Permissions & roles", "अनुमतियाँ"), Icons.Default.Security) { PermissionsPanel() }
        Section(tr("Alerts when the app is closed", "ऐप बंद होने पर भी अलर्ट"), Icons.Default.Notifications) {
            var push by remember { mutableStateOf(Prefs.pushStatus) }
            var pushMsg by remember { mutableStateOf<String?>(null) }
            val on = push == "on"
            Text(if (on) tr("● Push notifications on (Firebase)", "● पुश सूचनाएं चालू (Firebase)")
                 else tr("○ Push off – alerts need the app running. Server: ", "○ पुश बंद – अलर्ट के लिए ऐप चालू रखें। सर्वर: ") + (push ?: "?"),
                color = if (on) VG.green else VG.amber)
            SmallButton(tr("Check & send test push", "जाँचें और टेस्ट पुश भेजें")) {
                scope.launch {
                    pushMsg = "…"
                    Push.register(ctx)
                    push = Prefs.pushStatus
                    pushMsg = runCatching {
                        val r = Api.post("/api/push/test").asObj()
                        if (r.bool("ok") == true) tr("Sent – it should appear in a few seconds.", "भेज दिया – कुछ सेकंड में दिखेगा।")
                        else r.str("status") ?: tr("Not sent", "नहीं भेजा")
                    }.getOrElse { it.message }
                }
            }
            pushMsg?.let { Text(it, color = VG.muted) }
        }
        Section(tr("Account", "खाता"), Icons.Default.Person) {
            Kv("Name", Prefs.name); Kv("Phone", Prefs.phone); Kv("User ID", Prefs.userId); Kv("Family ID", Prefs.familyId)
            SmallButton(tr("Sign out of this phone", "इस फ़ोन से साइन आउट")) {
                kotlinx.coroutines.MainScope().launch { runCatching { Api.post("/api/auth/logout") }; Prefs.token = null }
                Live.stop()
                ctx.stopService(Intent(ctx, GuardService::class.java))
                Prefs.userId = null; Prefs.familyId = null; Prefs.familyJson = null; Prefs.setupDone = false
                Prefs.pushStatus = null
                nav.navigate("setup") { popUpTo(0) }
            }
        }
    }
}

@Composable
fun ToggleRow(label: String, value: Boolean, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Text(label, modifier = Modifier.weight(1f))
        Switch(value, onChange)
    }
}
