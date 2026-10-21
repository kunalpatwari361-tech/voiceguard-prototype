package com.voiceguard.app.telecom

import android.content.Context
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Truecaller-style floating caller card (needs "Display over other apps"): a coloured badge says whether the caller
 * is family, a saved contact, unknown, suspicious or spam (CallStatus). Shown over any screen while a call rings;
 * hidden when the call is answered or ends, or when VoiceGuard's own call screen opens.
 */
object CallerIdOverlay {
    private val main = Handler(Looper.getMainLooper())
    private var view: View? = null
    private var badge: TextView? = null
    private var title: TextView? = null
    private var sub: TextView? = null
    private var card: LinearLayout? = null

    fun show(ctx: Context, s: CallStatus.Status) = show(ctx, s.title, s.detail, s.color, s.badge)

    fun show(ctx: Context, heading: String, detail: String, color: Int, status: String? = null) = main.post {
        if (!Settings.canDrawOverlays(ctx)) return@post
        if (view != null) { update(heading, detail, color, status); return@post }
        val app = ctx.applicationContext
        val d = app.resources.displayMetrics.density
        val brand = TextView(app).apply { text = "🛡 VoiceGuard"; setTextColor(0xFF22C55E.toInt()); textSize = 12f }
        badge = TextView(app).apply { textSize = 14f; setTypeface(typeface, Typeface.BOLD); setPadding(0, (4 * d).toInt(), 0, (2 * d).toInt()) }
        title = TextView(app).apply { setTextColor(0xFFFFFFFF.toInt()); textSize = 20f; setTypeface(typeface, Typeface.BOLD) }
        sub = TextView(app).apply { setTextColor(0xFFCBD5E1.toInt()); textSize = 14f }
        card = LinearLayout(app).apply {
            orientation = LinearLayout.VERTICAL
            setPadding((18 * d).toInt(), (14 * d).toInt(), (18 * d).toInt(), (14 * d).toInt())
            addView(brand); addView(badge); addView(title); addView(sub)
            setOnClickListener { hide() }
        }
        update(heading, detail, color, status)
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT,
        ).apply { gravity = Gravity.TOP; y = (90 * d).toInt(); horizontalMargin = 0.04f }
        runCatching { app.getSystemService(WindowManager::class.java).addView(card, lp); view = card }
    }

    private fun update(heading: String, detail: String, color: Int, status: String?) {
        badge?.apply { text = status.orEmpty(); visibility = if (status.isNullOrBlank()) View.GONE else View.VISIBLE; setTextColor(color) }
        title?.text = heading
        sub?.text = detail
        card?.background = GradientDrawable().apply {
            cornerRadius = 40f; setColor(0xF2131C2F.toInt()); setStroke(6, color)
        }
    }

    fun hide() = main.post {
        val v = view ?: return@post
        runCatching { v.context.getSystemService(WindowManager::class.java).removeView(v) }
        view = null; card = null; badge = null; title = null; sub = null
    }

    const val GREEN = 0xFF22C55E.toInt()
    const val AMBER = 0xFFF59E0B.toInt()
    const val RED = 0xFFEF4444.toInt()
    const val GREY = 0xFF94A3B8.toInt()
}
