package cn.jtranslate.live.overlay

import android.content.Context
import android.content.res.Configuration
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.*
import android.widget.*
import cn.jtranslate.live.LiveState
import cn.jtranslate.live.settings.Options
import kotlin.math.roundToInt

class OverlayController(private val context: Context, private val pause: () -> Unit,
    private val stop: () -> Unit, private val savePosition: (Boolean, Int, Int) -> Unit) {
    private val manager = context.getSystemService(WindowManager::class.java)
    private val container = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 8, 24, 16) }
    private val title = TextView(context).apply { setTextColor(0xFFB8E4DC.toInt()); textSize = 12f }
    private val source = TextView(context).apply { setTextColor(0xFFD4DDD9.toInt()) }
    private val translation = TextView(context).apply { setTextColor(Color.WHITE); setTypeface(null, Typeface.BOLD) }
    private val toggle = Button(context).apply { text = "暂停"; setOnClickListener { pause() }; minHeight = 0; textSize = 11f }
    private var attached = false
    private var landscape = false
    private var options = Options()
    private val params = WindowManager.LayoutParams(300, WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY, WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
        PixelFormat.TRANSLUCENT).apply { gravity = Gravity.TOP or Gravity.START; alpha = .8f }

    init {
        val header = LinearLayout(context)
        header.addView(title, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        header.addView(toggle, LinearLayout.LayoutParams(130, 90))
        header.addView(Button(context).apply { text = "停止"; textSize = 11f; setOnClickListener { stop() } },
            LinearLayout.LayoutParams(130, 90))
        container.addView(header); container.addView(source); container.addView(translation)
        var downX = 0f; var downY = 0f; var originX = 0; var originY = 0
        title.setOnTouchListener { view, event ->
            when (event.action) {
                MotionEvent.ACTION_DOWN -> { downX = event.rawX; downY = event.rawY; originX = params.x; originY = params.y }
                MotionEvent.ACTION_MOVE -> { params.x = originX + (event.rawX - downX).toInt(); params.y = originY + (event.rawY - downY).toInt(); clamp(); update() }
                MotionEvent.ACTION_UP -> {
                    savePosition(landscape, params.x, params.y)
                    if (kotlin.math.abs(event.rawX - downX) < 8 && kotlin.math.abs(event.rawY - downY) < 8) view.performClick()
                }
            }
            true
        }
    }

    private fun clamp() {
        val metrics = context.resources.displayMetrics
        params.x = params.x.coerceIn(0, maxOf(0, metrics.widthPixels - params.width))
        params.y = params.y.coerceIn((24 * metrics.density).toInt(), maxOf((24 * metrics.density).toInt(), metrics.heightPixels - maxOf(container.height, 200)))
    }
    private fun update() { if (attached) runCatching { manager.updateViewLayout(container, params) } }

    fun style(value: Options) {
        options = value
        landscape = context.resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
        val metrics = context.resources.displayMetrics
        params.width = minOf(metrics.widthPixels - (24 * metrics.density).toInt(), (720 * metrics.density).toInt())
        params.x = if (landscape) value.landscapeX else value.portraitX
        params.y = if (landscape) value.landscapeY else value.portraitY
        source.textSize = value.fontSize * .75f
        translation.textSize = value.fontSize
        source.visibility = if (value.bilingual) View.VISIBLE else View.GONE
        container.background = GradientDrawable().apply {
            cornerRadius = 22f; setColor(Color.argb((value.opacity * 255).roundToInt(), 10, 24, 22))
        }
        clamp(); update()
    }

    fun show() { if (!attached) { manager.addView(container, params); attached = true } }
    fun render(state: LiveState) {
        title.text = context.getString(cn.jtranslate.live.R.string.overlay_title, if (state.demo) "演示字幕" else "实时翻译", state.status)
        source.text = state.source
        translation.text = state.translated.ifEmpty { if (state.demo) "等待内部音频 · 仅演示，不识别对白" else "等待日语对白…" }
        toggle.text = if (state.paused) "继续" else "暂停"
    }
    fun close() { if (attached) { runCatching { manager.removeView(container) }; attached = false } }
}
