package com.voiceguard.app.ui

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.service.GuardService
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.serialization.json.JsonObject

/** Hand-off data between screens that does not fit in a route string. */
object Shared {
    var sharedAudio: Uri? = null
    var verify: Triple<String, String, String?>? = null      // request id, asker name, caller number
    var report: JsonObject? = null
    var reportCtx: ReportCtx? = null
}

class MainActivity : ComponentActivity() {
    private val pending = MutableStateFlow<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handle(intent)
        GuardService.start(this)
        setContent {
            VgTheme {
                val nav = rememberNavController()
                val route by pending.collectAsState()
                LaunchedEffect(route) {
                    route?.let { if (Prefs.setupDone) nav.navigate(it) { launchSingleTop = true }; pending.value = null }
                }
                AppNav(nav, if (Prefs.setupDone) "home" else "setup")
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    private fun handle(i: Intent?) {
        i ?: return
        // Only urgent family requests may wake the phone and show over the lock screen.
        val urgent = i.getStringExtra("nav") in setOf("verify", "hd")
        setShowWhenLocked(urgent)
        setTurnScreenOn(urgent)
        when {
            i.action == Intent.ACTION_SEND -> {
                @Suppress("DEPRECATION")
                Shared.sharedAudio = i.getParcelableExtra(Intent.EXTRA_STREAM)
                pending.value = "check?mode=shared"
            }
            i.action == Intent.ACTION_DIAL || (i.action == Intent.ACTION_VIEW && i.data?.scheme == "tel") ->
                pending.value = "dialer?number=" + Uri.encode(i.data?.schemeSpecificPart.orEmpty())
            i.getStringExtra("nav") == "verify" -> {
                Shared.verify = Triple(i.getStringExtra("rid")!!, i.getStringExtra("from") ?: "Family", i.getStringExtra("number"))
                pending.value = "verify"
            }
            i.getStringExtra("nav") == "hd" ->
                pending.value = "hd/${i.getStringExtra("call_id")}?incoming=true&peer=${Uri.encode(i.getStringExtra("from"))}"
            i.getStringExtra("nav") == "number" -> pending.value = "number/" + Uri.encode(i.getStringExtra("number"))
            i.getStringExtra("nav") == "alerts" -> pending.value = "alerts"
        }
    }
}

@androidx.compose.runtime.Composable
fun AppNav(nav: NavHostController, start: String) {
    val back: () -> Unit = { nav.popBackStack() }
    NavHost(nav, startDestination = start) {
        composable("setup") { SetupScreen(onDone = { nav.navigate("home") { popUpTo("setup") { inclusive = true } } }) }
        composable("home") { HomeScreen(nav) }
        composable("dialer?number={number}", listOf(navArgument("number") { defaultValue = "" })) {
            DialerScreen(it.arguments?.getString("number").orEmpty(), nav, back)
        }
        composable("number/{number}") { NumberScreen(it.arguments?.getString("number").orEmpty(), back) }
        composable("family") { FamilyScreen(nav, back) }
        composable("voiceprint") { VoicePrintScreen(back) }
        composable("check?mode={mode}", listOf(navArgument("mode") { defaultValue = "file" })) {
            CheckScreen(it.arguments?.getString("mode") ?: "file", nav, back)
        }
        composable("report") { ReportScreen(nav, back) }
        composable("demo") { DemoListScreen(nav, back) }
        composable("democall/{id}?claimed={claimed}&phone={phone}", listOf(
            navArgument("claimed") { defaultValue = "" }, navArgument("phone") { type = NavType.BoolType; defaultValue = true })) {
            DemoCallScreen(it.arguments?.getString("id")!!, it.arguments?.getString("claimed").orEmpty(),
                it.arguments?.getBoolean("phone") ?: true, nav, back)
        }
        composable("verify") { VerifyRequestScreen(back) }
        composable("hdcall/{peerId}") { HdCallScreen(callIdIn = null, peerId = it.arguments?.getString("peerId"), peerName = null, incoming = false, back = back) }
        composable("hd/{callId}?incoming={incoming}&peer={peer}", listOf(
            navArgument("incoming") { type = NavType.BoolType; defaultValue = true }, navArgument("peer") { defaultValue = "" })) {
            HdCallScreen(callIdIn = it.arguments?.getString("callId"), peerId = null, peerName = it.arguments?.getString("peer"),
                incoming = it.arguments?.getBoolean("incoming") ?: true, back = back)
        }
        composable("alerts") { AlertsScreen(back) }
        composable("scamlist") { ScamListScreen(nav, back) }
        composable("evidence") { EvidenceListScreen(nav, back) }
        composable("evidence/{id}") { EvidenceDetailScreen(it.arguments?.getString("id")!!, back) }
        composable("future") { FutureLabScreen(back) }
        composable("settings") { SettingsScreen(nav, back) }
    }
}
