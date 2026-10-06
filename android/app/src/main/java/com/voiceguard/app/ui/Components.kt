package com.voiceguard.app.ui

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.voiceguard.app.data.Prefs

/** Bilingual UI text: English by default, Hindi when the user switches language. */
fun tr(en: String, hi: String) = if (Prefs.hindi) hi else en

fun toast(ctx: Context, msg: String) = Toast.makeText(ctx, msg, Toast.LENGTH_LONG).show()

fun dial(ctx: Context, number: String, direct: Boolean = true) {
    val action = if (direct) Intent.ACTION_CALL else Intent.ACTION_DIAL
    runCatching { ctx.startActivity(Intent(action, Uri.parse("tel:$number")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
        .onFailure { ctx.startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:$number")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
}

fun openUrl(ctx: Context, url: String) =
    ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))

fun shareText(ctx: Context, text: String) =
    ctx.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text), "Share")
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun Screen(title: String, onBack: (() -> Unit)?, actions: @Composable () -> Unit = {}, scroll: Boolean = true,
           content: @Composable ColumnScope.() -> Unit) {
    Scaffold(
        containerColor = VG.bg,
        topBar = {
            TopAppBar(
                title = { Text(title, maxLines = 1) },
                navigationIcon = { if (onBack != null) IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } },
                actions = { actions() },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = VG.bg),
            )
        },
    ) { pad ->
        val m = Modifier.padding(pad).fillMaxSize().padding(horizontal = 16.dp)
        Column(if (scroll) m.verticalScroll(rememberScrollState()) else m, verticalArrangement = Arrangement.spacedBy(12.dp)) {
            content()
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
fun Section(title: String? = null, icon: ImageVector? = null, accent: Color = VG.blue, content: @Composable ColumnScope.() -> Unit) {
    Card(colors = CardDefaults.cardColors(containerColor = VG.surface), shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (title != null) Row(verticalAlignment = Alignment.CenterVertically) {
                if (icon != null) { Icon(icon, null, tint = accent, modifier = Modifier.size(20.dp)); Spacer(Modifier.width(8.dp)) }
                Text(title, style = androidx.compose.material3.MaterialTheme.typography.titleMedium)
            }
            content()
        }
    }
}

@Composable
fun Tile(icon: ImageVector, title: String, subtitle: String? = null, color: Color = VG.blue, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Card(colors = CardDefaults.cardColors(containerColor = VG.surface), shape = RoundedCornerShape(16.dp),
        modifier = modifier.clickable(onClick = onClick)) {
        Column(Modifier.padding(14.dp).fillMaxWidth()) {
            Box(Modifier.size(40.dp).clip(CircleShape).background(color.copy(alpha = 0.18f)), contentAlignment = Alignment.Center) {
                Icon(icon, null, tint = color)
            }
            Spacer(Modifier.height(8.dp))
            Text(title, fontWeight = FontWeight.SemiBold, fontSize = 15.sp, maxLines = 2)
            if (subtitle != null) Text(subtitle, color = VG.muted, fontSize = 12.sp, maxLines = 2)
        }
    }
}

@Composable
fun BigButton(text: String, icon: ImageVector? = null, color: Color = VG.green, enabled: Boolean = true,
              modifier: Modifier = Modifier.fillMaxWidth(), onClick: () -> Unit) {
    Button(onClick = onClick, enabled = enabled, modifier = modifier.height(52.dp), shape = RoundedCornerShape(14.dp),
        colors = ButtonDefaults.buttonColors(containerColor = color, contentColor = if (color == VG.amber || color == VG.green) Color.Black else Color.White)) {
        if (icon != null) { Icon(icon, null); Spacer(Modifier.width(8.dp)) }
        Text(text, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
fun SmallButton(text: String, icon: ImageVector? = null, modifier: Modifier = Modifier, enabled: Boolean = true, onClick: () -> Unit) {
    OutlinedButton(onClick = onClick, modifier = modifier, enabled = enabled, shape = RoundedCornerShape(12.dp)) {
        if (icon != null) { Icon(icon, null, modifier = Modifier.size(18.dp)); Spacer(Modifier.width(6.dp)) }
        Text(text, maxLines = 1)
    }
}

@Composable
fun Busy(text: String) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 8.dp)) {
        CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 3.dp)
        Spacer(Modifier.width(12.dp))
        Text(text, color = VG.muted)
    }
}

@Composable
fun ErrorBox(msg: String?) {
    if (msg == null) return
    Box(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(VG.red.copy(alpha = 0.15f)).padding(12.dp)) {
        Text(msg, color = VG.red)
    }
}

@Composable
fun Banner(text: String, color: Color, sub: String? = null) {
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(color.copy(alpha = 0.18f)).padding(14.dp)) {
        Text(text, color = color, fontWeight = FontWeight.Bold, fontSize = 17.sp)
        if (sub != null) Text(sub, color = VG.text, fontSize = 14.sp)
    }
}

@Composable
fun Chip(text: String, color: Color) {
    Text(text, color = color, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
        modifier = Modifier.clip(RoundedCornerShape(50)).background(color.copy(alpha = 0.16f)).padding(horizontal = 10.dp, vertical = 4.dp))
}

/** 0..100 semicircle gauge for the Final Risk Score. */
@Composable
fun RiskGauge(score: Int, level: String?, size: Dp = 180.dp) {
    val color = VG.level(level)
    Box(contentAlignment = Alignment.Center, modifier = Modifier.fillMaxWidth()) {
        Canvas(Modifier.size(size, size * 0.6f)) {
            val stroke = 18.dp.toPx()
            val d = this.size.width - stroke
            val tl = Offset(stroke / 2, stroke / 2)
            drawArc(VG.surface2, 180f, 180f, false, tl, Size(d, d), style = Stroke(stroke, cap = StrokeCap.Round))
            drawArc(color, 180f, 180f * score.coerceIn(0, 100) / 100f, false, tl, Size(d, d), style = Stroke(stroke, cap = StrokeCap.Round))
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(top = size * 0.18f)) {
            Text("$score", fontSize = 44.sp, fontWeight = FontWeight.Bold, color = color)
            Text(when (level) { "danger" -> tr("DANGER", "ख़तरा"); "caution" -> tr("CAUTION", "सावधान"); else -> tr("SAFE", "सुरक्षित") },
                color = color, fontWeight = FontWeight.Bold, letterSpacing = 2.sp)
        }
    }
}

@Composable
fun ScoreRow(label: String, score: Double?, detail: String?, note: String? = null) {
    Column(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(label, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            Text(if (score == null) "–" else "${(score * 100).toInt()}%", color = VG.score(score), fontWeight = FontWeight.Bold)
        }
        LinearProgressIndicator(progress = { (score ?: 0.0).toFloat() }, color = VG.score(score), trackColor = VG.surface2,
            modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp).height(6.dp).clip(RoundedCornerShape(3.dp)))
        if (!detail.isNullOrBlank()) Text(detail, color = VG.muted, fontSize = 13.sp)
        if (!note.isNullOrBlank()) Text(note, color = VG.amber, fontSize = 12.sp)
    }
}

@Composable
fun LevelMeter(db: Double) {
    val v = ((db + 60) / 60).coerceIn(0.0, 1.0).toFloat()
    LinearProgressIndicator(progress = { v }, color = if (v > 0.5f) VG.green else VG.blue, trackColor = VG.surface2,
        modifier = Modifier.fillMaxWidth().height(10.dp).clip(RoundedCornerShape(5.dp)))
}

@Composable
fun Kv(k: String, v: String?) {
    Row(Modifier.fillMaxWidth()) {
        Text(k, color = VG.muted, modifier = Modifier.weight(0.45f), fontSize = 14.sp)
        Text(v ?: "–", modifier = Modifier.weight(0.55f), fontSize = 14.sp, textAlign = TextAlign.End)
    }
}
