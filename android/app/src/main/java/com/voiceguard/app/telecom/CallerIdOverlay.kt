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
 * Truecaller-style caller ID card shown over the system incoming-call screen (needs "Display over other apps").
 * Used when VoiceGuard is not the default Phone app; as the Phone app, InCallActivity shows this itself.
 */
object CallerIdOverlay {
    private val main = Handler(Looper.getMainLooper())
    private var view: View? = null
    private var title: TextView? = null
    private var sub: TextView? = null
    private var card: LinearLayout? = null

    fun show(ctx: Context, heading: String, detail: String, color: Int) = main.post {
        if (!Settings.canDrawOverlays(ctx)) return@post
        if (view != null) { update(heading, detail, color); return@post }
        val app = ctx.applicationContext
        val d = app.resources.displayMetrics.density
        val brand = TextView(app).apply { text = "🛡 VoiceGuard"; setTextColor(0xFF22C55E.toInt()); textSize = 12f }
        title = TextView(app).apply { setTextColor(0xFFFFFFFF.toInt()); textSize = 20f; setTypeface(typeface, Typeface.BOLD) }
        sub = TextView(app).apply { setTextColor(0xFFCBD5E1.toInt()); textSize = 14f }
        card = LinearLayout(app).apply {
            orientation = LinearLayout.VERTICAL
            setPadding((18 * d).toInt(), (14 * d).toInt(), (18 * d).toInt(), (14 * d).toInt())
            addView(brand); addView(title); addView(sub)
            setOnClickListener { hide() }
        }
        update(heading, detail, color)
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT,
        ).apply { gravity = Gravity.TOP; y = (90 * d).toInt(); horizontalMargin = 0.04f }
        runCatching { app.getSystemService(WindowManager::class.java).addView(card, lp); view = card }
    }

    private fun update(heading: String, detail: String, color: Int) {
        title?.text = heading
        sub?.text = detail
        card?.background = GradientDrawable().apply {
            cornerRadius = 40f; setColor(0xF2131C2F.toInt()); setStroke(6, color)
        }
    }

    fun hide() = main.post {
        val v = view ?: return@post
        runCatching { v.context.getSystemService(WindowManager::class.java).removeView(v) }
        view = null; card = null; title = null; sub = null
    }

    const val GREEN = 0xFF22C55E.toInt()
    const val AMBER = 0xFFF59E0B.toInt()
    const val RED = 0xFFEF4444.toInt()
    const val GREY = 0xFF94A3B8.toInt()
}
