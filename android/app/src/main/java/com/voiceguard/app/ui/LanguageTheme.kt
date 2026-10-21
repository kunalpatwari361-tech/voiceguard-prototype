package com.voiceguard.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Translate
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.voiceguard.app.data.I18n
import com.voiceguard.app.data.Prefs

/** Button showing the current language; opens the list of all Indian languages. */
@Composable
fun LanguageButton(onChanged: () -> Unit = {}) {
    var open by remember { mutableStateOf(false) }
    OutlinedButton({ open = true }) {
        Icon(Icons.Default.Translate, null)
        Spacer(Modifier.width(8.dp))
        Text(I18n.current().native)
    }
    if (open) LanguageDialog({ open = false }, onChanged)
}

@Composable
fun LanguageDialog(onDismiss: () -> Unit, onChanged: () -> Unit = {}) {
    AlertDialog(
        onDismissRequest = onDismiss,
        confirmButton = { TextButton(onDismiss) { Text(tr("Close", "बंद करें")) } },
        title = { Text(tr("Language", "भाषा")) },
        text = {
            Column(Modifier.heightIn(max = 440.dp).verticalScroll(rememberScrollState())) {
                I18n.available.forEach { l ->
                    Row(Modifier.fillMaxWidth().clickable { I18n.select(l.code); onChanged(); onDismiss() }.padding(vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(l.code == Prefs.lang, onClick = null)
                        Spacer(Modifier.width(10.dp))
                        Column {
                            Text(l.native, fontWeight = FontWeight.SemiBold)
                            if (l.native != l.english) Text(l.english, color = VG.muted, fontSize = 12.sp)
                        }
                    }
                }
            }
        },
    )
}

/** System / Light / Dark. Only the colours change; every screen keeps its design. */
@Composable
fun ThemeChips() {
    Row(verticalAlignment = Alignment.CenterVertically) {
        listOf("system" to tr("System", "सिस्टम"), "light" to tr("Light", "लाइट"), "dark" to tr("Dark", "डार्क")).forEach { (k, label) ->
            FilterChip(VG.choice == k, { Prefs.theme = k; VG.choice = k }, label = { Text(label) })
            Spacer(Modifier.width(8.dp))
        }
    }
}
