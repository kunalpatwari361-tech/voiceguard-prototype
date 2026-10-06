package com.voiceguard.app.ui

import android.app.role.RoleManager
import android.net.Uri
import android.os.Bundle
import android.telecom.PhoneAccountHandle
import android.telecom.TelecomManager
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.SimCard
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.voiceguard.app.data.Numbers
import com.voiceguard.app.telecom.Sims

/**
 * Real calls from anywhere in the app. As the default Phone app VoiceGuard places the call itself (so it opens the
 * VoiceGuard call screen); dual-SIM phones without a default SIM get a SIM choice first.
 */
@Composable
fun rememberPlacer(): (String) -> Unit {
    val ctx = LocalContext.current
    var simFor by remember { mutableStateOf<String?>(null) }

    fun placeWith(n: String, sim: PhoneAccountHandle?) {
        val extras = Bundle().apply { if (sim != null) putParcelable(TelecomManager.EXTRA_PHONE_ACCOUNT_HANDLE, sim) }
        runCatching { ctx.getSystemService(TelecomManager::class.java).placeCall(Uri.fromParts("tel", n, null), extras) }
            .onFailure { dial(ctx, n) }
    }

    simFor?.let { n ->
        AlertDialog(
            onDismissRequest = { simFor = null },
            title = { Text(tr("Call with which SIM?", "किस SIM से कॉल करें?")) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(Numbers.pretty(n), color = VG.muted)
                    Sims.list(ctx).forEach { (h, label) ->
                        BigButton(label, Icons.Default.SimCard, VG.green) { simFor = null; placeWith(n, h) }
                    }
                }
            },
            confirmButton = {},
            dismissButton = { SmallButton(tr("Cancel", "रद्द करें")) { simFor = null } },
            containerColor = VG.surface,
        )
    }
    return { n ->
        if (n.isNotBlank()) {
            if (!ctx.getSystemService(RoleManager::class.java).isRoleHeld(RoleManager.ROLE_DIALER)) dial(ctx, n)
            else {
                val sims = Sims.list(ctx)
                val def = Sims.default(ctx)
                if (def == null && sims.size > 1) simFor = n else placeWith(n, def ?: sims.firstOrNull()?.first)
            }
        }
    }
}
