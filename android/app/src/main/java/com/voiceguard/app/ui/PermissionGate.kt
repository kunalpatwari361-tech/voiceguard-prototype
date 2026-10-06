package com.voiceguard.app.ui

import android.Manifest
import android.app.Activity
import android.app.role.RoleManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Contacts
import androidx.compose.material.icons.filled.Dialpad
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import com.voiceguard.app.data.Contacts
import com.voiceguard.app.data.Prefs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Truecaller-style first-run gate: ask to become the default phone app (system role dialog),
 * then for contacts + phone access, before the rest of the app.
 */
object PermissionGate {
    val CONTACT_PERMS = arrayOf(Manifest.permission.READ_CONTACTS, Manifest.permission.READ_PHONE_STATE, Manifest.permission.CALL_PHONE)

    fun isDialer(ctx: Context) = ctx.getSystemService(RoleManager::class.java).isRoleHeld(RoleManager.ROLE_DIALER)

    fun contactsOk(ctx: Context) = CONTACT_PERMS.all { ctx.checkSelfPermission(it) == android.content.pm.PackageManager.PERMISSION_GRANTED }

    fun done(ctx: Context) = isDialer(ctx) && contactsOk(ctx)

    /** Show the gate on launch until both are granted, unless the user chose "Not now". */
    fun shouldShow(ctx: Context) = !Prefs.permGateSkipped && !done(ctx)
}

@Composable
fun PermissionGateScreen(onDone: () -> Unit) {
    val ctx = LocalContext.current
    val activity = ctx as? Activity
    var tick by remember { mutableIntStateOf(0) }
    var roleAsks by remember { mutableIntStateOf(0) }
    var permAsks by remember { mutableIntStateOf(0) }

    // Re-check when coming back from the system dialog or the Settings app.
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { tick++ }

    val roleLauncher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { roleAsks++; tick++ }
    val settingsLauncher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { tick++ }
    val permLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { permAsks++; tick++ }

    val dialerOk = remember(tick) { PermissionGate.isDialer(ctx) }
    val contactsOk = remember(tick) { PermissionGate.contactsOk(ctx) }
    LaunchedEffect(dialerOk, contactsOk) {
        if (dialerOk && contactsOk) {
            withContext(Dispatchers.IO) { Contacts.all(ctx, refresh = true) }
            onDone()
        }
    }

    fun askDialer() {
        // Android stops showing the role dialog after it is declined twice; send the user to Default apps instead.
        if (roleAsks >= 2) settingsLauncher.launch(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS))
        else roleLauncher.launch(ctx.getSystemService(RoleManager::class.java).createRequestRoleIntent(RoleManager.ROLE_DIALER))
    }

    fun askContacts() {
        val blocked = permAsks > 0 && activity != null && PermissionGate.CONTACT_PERMS.any {
            ctx.checkSelfPermission(it) != android.content.pm.PackageManager.PERMISSION_GRANTED && !activity.shouldShowRequestPermissionRationale(it)
        }
        // "Don't ask again" (or two denials) means only the app's Settings page can grant it now.
        if (blocked) settingsLauncher.launch(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${ctx.packageName}")))
        else permLauncher.launch(PermissionGate.CONTACT_PERMS)
    }

    Box(Modifier.fillMaxSize().background(VG.bg).systemBarsPadding()) {
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 32.dp),
            horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Box(Modifier.size(96.dp).clip(CircleShape).background(VG.green.copy(alpha = 0.15f)), contentAlignment = Alignment.Center) {
                Icon(Icons.Default.Shield, null, tint = VG.green, modifier = Modifier.size(56.dp))
            }
            Text(tr("Get scam protection on every call", "हर कॉल पर ठगी से सुरक्षा पाएं"),
                fontSize = 24.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center)
            Text(tr("To identify callers and warn you about voice-clone scams, VoiceGuard needs two things.",
                "कॉलर पहचानने और आवाज़-क्लोन ठगी की चेतावनी देने के लिए VoiceGuard को दो चीज़ें चाहिए।"),
                color = VG.muted, textAlign = TextAlign.Center)
            Spacer(Modifier.height(4.dp))

            GateStep(Icons.Default.Dialpad, tr("Set as default phone app", "डिफ़ॉल्ट फ़ोन ऐप बनाएं"),
                tr("See who is calling, get the scam check and family verify inside every call, and block spam automatically.",
                    "देखें कौन कॉल कर रहा है, हर कॉल में स्कैम जाँच और परिवार वेरिफ़ाई पाएं, स्पैम अपने-आप ब्लॉक करें।"), dialerOk)
            GateStep(Icons.Default.Contacts, tr("Allow contacts & phone access", "कॉन्टैक्ट्स और फ़ोन की अनुमति दें"),
                tr("Show your saved names on calls and in the dialer, and never flag people you know as spam. Your contacts stay on this phone.",
                    "कॉल और डायलर में आपके सेव नाम दिखें, जान-पहचान वालों को स्पैम न माना जाए। आपके कॉन्टैक्ट्स इसी फ़ोन पर रहते हैं।"), contactsOk)

            Spacer(Modifier.height(8.dp))
            when {
                !dialerOk -> BigButton(if (roleAsks >= 2) tr("Open Default apps settings", "डिफ़ॉल्ट ऐप सेटिंग्स खोलें")
                    else tr("Set as default phone app", "डिफ़ॉल्ट फ़ोन ऐप बनाएं"), Icons.Default.Dialpad) { askDialer() }
                else -> BigButton(tr("Allow contacts access", "कॉन्टैक्ट्स की अनुमति दें"), Icons.Default.Contacts) { askContacts() }
            }
            if (!dialerOk && roleAsks >= 2) Text(tr("Choose Phone app → VoiceGuard", "फ़ोन ऐप → VoiceGuard चुनें"), color = VG.muted, fontSize = 13.sp)
            TextButton({ Prefs.permGateSkipped = true; onDone() }) {
                Text(tr("Not now", "अभी नहीं"), color = VG.muted)
            }
            Text(tr("You can change this any time in Settings → Permissions.", "आप इसे कभी भी सेटिंग्स → अनुमतियाँ में बदल सकते हैं।"),
                color = VG.muted, fontSize = 12.sp, textAlign = TextAlign.Center)
        }
    }
}

@Composable
private fun GateStep(icon: ImageVector, title: String, body: String, ok: Boolean) {
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(VG.surface).padding(16.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Icon(icon, null, tint = VG.blue, modifier = Modifier.size(28.dp))
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            Text(body, color = VG.muted, fontSize = 13.sp)
        }
        Spacer(Modifier.width(8.dp))
        Icon(Icons.Default.CheckCircle, null, tint = if (ok) VG.green else VG.muted.copy(alpha = 0.3f))
    }
}
