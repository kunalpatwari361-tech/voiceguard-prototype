package com.voiceguard.app.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

object VG {
    val bg = Color(0xFF0B1220)
    val surface = Color(0xFF131C2F)
    val surface2 = Color(0xFF1B2741)
    val green = Color(0xFF22C55E)
    val red = Color(0xFFEF4444)
    val amber = Color(0xFFF59E0B)
    val blue = Color(0xFF38BDF8)
    val violet = Color(0xFFA78BFA)
    val text = Color(0xFFE5E7EB)
    val muted = Color(0xFF94A3B8)

    fun level(level: String?) = when (level) {
        "danger" -> red
        "caution" -> amber
        "safe" -> green
        else -> muted
    }

    /** 0..1 AI-likeness / risk -> colour. */
    fun score(s: Double?) = when {
        s == null -> muted
        s >= 0.6 -> red
        s >= 0.35 -> amber
        else -> green
    }
}

@Composable
fun VgTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = VG.green, onPrimary = Color.Black, secondary = VG.blue, background = VG.bg,
            surface = VG.surface, surfaceVariant = VG.surface2, onSurface = VG.text, onBackground = VG.text,
            error = VG.red, surfaceContainer = VG.surface, surfaceContainerHigh = VG.surface2,
        ),
        typography = Typography(
            titleLarge = TextStyle(fontSize = 22.sp, fontWeight = FontWeight.Bold),
            titleMedium = TextStyle(fontSize = 17.sp, fontWeight = FontWeight.SemiBold),
            bodyLarge = TextStyle(fontSize = 16.sp),
            bodyMedium = TextStyle(fontSize = 14.sp),
            labelLarge = TextStyle(fontSize = 15.sp, fontWeight = FontWeight.SemiBold),
        ),
        content = content,
    )
}
