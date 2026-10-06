package com.voiceguard.app.ui

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.LocalPolice
import androidx.compose.material.icons.filled.PanTool
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.data.Sync
import com.voiceguard.app.data.str
import kotlinx.coroutines.delay

/** Panic Pause (feature 19): a forced cool-down before paying right after a risky call. */
class PanicPauseActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val app = intent.getStringExtra("app") ?: "a payment app"
        setContent {
            VgTheme {
                var left by remember { mutableIntStateOf(60) }
                LaunchedEffect(Unit) { while (left > 0) { delay(1000); left-- } }
                val helper = Sync.others().firstOrNull()
                Column(Modifier.fillMaxSize().background(Color(0xFF3B0A0A)).padding(24.dp),
                    verticalArrangement = Arrangement.spacedBy(16.dp, Alignment.CenterVertically), horizontalAlignment = Alignment.CenterHorizontally) {
                    Icon(Icons.Default.PanTool, null, tint = VG.red, modifier = Modifier.size(84.dp))
                    Text(tr("PAUSE", "रुकिए"), fontSize = 44.sp, fontWeight = FontWeight.Black, color = VG.red)
                    val score = Prefs.lastRiskyScore ?: "high"
                    Text(if (app == "panic button") tr("You pressed PANIC during a risky call (score $score).", "आपने ख़तरनाक कॉल (स्कोर $score) के दौरान पैनिक दबाया।")
                         else tr("You opened $app right after a risky call (score $score).", "ख़तरनाक कॉल (स्कोर $score) के तुरंत बाद आपने $app खोला।"),
                        textAlign = TextAlign.Center, fontSize = 18.sp, color = VG.text)
                    Text(tr("Scammers rush you on purpose. Real family can wait 1 minute. Talk to someone you trust first.",
                        "ठग जानबूझकर जल्दबाज़ी करवाते हैं। असली परिवार 1 मिनट रुक सकता है। पहले किसी भरोसेमंद से बात करें।"),
                        textAlign = TextAlign.Center, color = VG.muted)
                    if (helper != null) BigButton(tr("Call ${helper.str("name")}", "${helper.str("name")} को कॉल करें"), Icons.Default.Call, VG.green) {
                        dial(this@PanicPauseActivity, helper.str("phone") ?: return@BigButton)
                    }
                    BigButton(tr("Call 1930 helpline", "1930 हेल्पलाइन"), Icons.Default.LocalPolice, VG.blue) { dial(this@PanicPauseActivity, "1930") }
                    BigButton(if (left > 0) tr("Continue in $left s", "$left सेकंड में आगे") else tr("I understand, continue", "मैं समझता हूँ, आगे बढ़ें"),
                        color = VG.surface2, enabled = left == 0) { finish() }
                }
            }
        }
    }
}
