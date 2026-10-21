package com.voiceguard.app.ui

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat

/**
 * VoiceGuard colours. Two palettes with the same design: the original dark one and a light one. Only the neutral
 * colours change; on light, the accents are a shade deeper so text and icons stay readable on white.
 * Every screen reads these through VG.x, so switching the theme recolours the whole app without a restart.
 */
object VG {
    /** "system" (follow the phone), "light" or "dark" - set from Prefs.theme. */
    var choice by mutableStateOf("system")
    var systemDark by mutableStateOf(true)
    /** The call screen always stays dark, like the phone's own call screen. */
    var forceDark by mutableStateOf(false)
    val dark: Boolean get() = forceDark || when (choice) { "light" -> false; "dark" -> true; else -> systemDark }

    val bg get() = if (dark) Color(0xFF0B1220) else Color(0xFFF3F6FA)
    val surface get() = if (dark) Color(0xFF131C2F) else Color(0xFFFFFFFF)
    val surface2 get() = if (dark) Color(0xFF1B2741) else Color(0xFFE5EBF3)
    val green get() = if (dark) Color(0xFF22C55E) else Color(0xFF15803D)
    val red get() = if (dark) Color(0xFFEF4444) else Color(0xFFDC2626)
    val amber get() = if (dark) Color(0xFFF59E0B) else Color(0xFFB45309)
    val blue get() = if (dark) Color(0xFF38BDF8) else Color(0xFF0369A1)
    val violet get() = if (dark) Color(0xFFA78BFA) else Color(0xFF6D28D9)
    val text get() = if (dark) Color(0xFFE5E7EB) else Color(0xFF0F172A)
    val muted get() = if (dark) Color(0xFF94A3B8) else Color(0xFF526173)
    /** Text / icon colour on a filled accent button: black on the bright dark-theme green and amber, else white. */
    fun onAccent(c: Color) = if (dark && (c == green || c == amber)) Color.Black else Color.White

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
    val sysDark = isSystemInDarkTheme()
    LaunchedEffect(sysDark) { VG.systemDark = sysDark }
    val dark = VG.dark
    val view = LocalView.current
    if (!view.isInEditMode) SideEffect {
        val window = (view.context as? Activity)?.window ?: return@SideEffect
        @Suppress("DEPRECATION")
        window.statusBarColor = VG.bg.toArgb()
        @Suppress("DEPRECATION")
        window.navigationBarColor = VG.bg.toArgb()
        WindowCompat.getInsetsController(window, view).apply {
            isAppearanceLightStatusBars = !dark
            isAppearanceLightNavigationBars = !dark
        }
    }
    MaterialTheme(
        colorScheme = if (dark) darkColorScheme(
            primary = VG.green, onPrimary = Color.Black, secondary = VG.blue, background = VG.bg,
            surface = VG.surface, surfaceVariant = VG.surface2, onSurface = VG.text, onBackground = VG.text,
            error = VG.red, surfaceContainer = VG.surface, surfaceContainerHigh = VG.surface2,
        ) else lightColorScheme(
            primary = VG.green, onPrimary = Color.White, secondary = VG.blue, background = VG.bg,
            surface = VG.surface, surfaceVariant = VG.surface2, onSurface = VG.text, onBackground = VG.text,
            onSurfaceVariant = VG.muted, error = VG.red, surfaceContainer = VG.surface, surfaceContainerHigh = VG.surface2,
            surfaceContainerLow = VG.surface, surfaceContainerHighest = VG.surface2, outline = Color(0xFFB8C4D3),
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
