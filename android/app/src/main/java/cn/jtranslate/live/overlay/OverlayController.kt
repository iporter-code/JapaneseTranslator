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
import kotlin.math.abs
import kotlin.math.roundToInt

/** Text-only overlay: all controls stay in the app except one close target. */
class OverlayController(private val context: Context, private val stop: () -> Unit,
    private val savePosition: (Boolean, Int, Int) -> Unit) {
    private val manager = context.getSystemService(WindowManager::class.java)
    private fun dp(value: Int) = (value * context.resources.displayMetrics.density).roundToInt()
    private val container = FrameLayout(context)
    private val body = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(12), dp(8), dp(42), dp(10))
    }
    private fun label(color: Int) = TextView(context).apply {
        setTextColor(color)
        setShadowLayer(dp(2).toFloat(), 0f, dp(1).toFloat(), Color.BLACK)
        includeFontPadding = true
        setLineSpacing(dp(2).toFloat(), 1f)
    }
    private val source = label(0xFFE0E8E5.toInt())
    private val translation = label(Color.WHITE).apply { setTypeface(null, Typeface.BOLD) }
    private val close = ImageButton(context).apply {
        setImageResource(cn.jtranslate.live.R.drawable.ic_close)
        setPadding(dp(10), dp(10), dp(10), dp(10))
        background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(0x66000000) }
        contentDescription = "关闭字幕并停止翻译"
        setOnClickListener { stop() }
    }
    private var attached = false
    private var landscape = false
    private var options = Options()
    private var state = LiveState()
    private val params = WindowManager.LayoutParams(dp(40), WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY, WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
        PixelFormat.TRANSLUCENT).apply { gravity = Gravity.TOP or Gravity.START }

    init {
        body.addView(source, LinearLayout.LayoutParams(-1, -2))
        body.addView(translation, LinearLayout.LayoutParams(-1, -2))
        container.addView(body, FrameLayout.LayoutParams(-1, -2))
        container.addView(close, FrameLayout.LayoutParams(dp(40), dp(40), Gravity.TOP or Gravity.END))
        var downX = 0f; var downY = 0f; var originX = 0; var originY = 0
        val drag = View.OnTouchListener { view, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.rawX; downY = event.rawY; originX = params.x; originY = params.y
                }
                MotionEvent.ACTION_MOVE -> {
                    params.x = originX + (event.rawX - downX).roundToInt()
                    params.y = originY + (event.rawY - downY).roundToInt()
                    clamp(); update()
                }
                MotionEvent.ACTION_UP -> {
                    savePosition(landscape, params.x, params.y)
                    if (abs(event.rawX - downX) < dp(4) && abs(event.rawY - downY) < dp(4)) view.performClick()
                }
            }
            true
        }
        listOf(container, body, source, translation).forEach { it.setOnTouchListener(drag) }
        container.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ ->
            val oldY = params.y
            clamp()
            if (oldY != params.y) update()
        }
    }

    private fun clamp() {
        val metrics = context.resources.displayMetrics
        params.x = params.x.coerceIn(0, maxOf(0, metrics.widthPixels - params.width))
        params.y = params.y.coerceIn(dp(24), maxOf(dp(24), metrics.heightPixels - maxOf(container.height, dp(40))))
    }
    private fun update() { if (attached) runCatching { manager.updateViewLayout(container, params) } }

    fun style(value: Options) {
        val rotated = landscape != (context.resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE)
        val positionChanged = if (landscape) options.landscapeX != value.landscapeX || options.landscapeY != value.landscapeY
            else options.portraitX != value.portraitX || options.portraitY != value.portraitY
        options = value
        landscape = context.resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
        if (!attached || rotated || positionChanged) {
            params.x = if (landscape) value.landscapeX else value.portraitX
            params.y = if (landscape) value.landscapeY else value.portraitY
        }
        source.textSize = value.fontSize * .75f
        translation.textSize = value.fontSize
        render(state, true)
    }

    fun show() { if (!attached) { manager.addView(container, params); attached = true } }
    fun render(value: LiveState, force: Boolean = false) {
        val changed = state.source != value.source || state.translated != value.translated
        state = value
        if (!force && !changed) return
        source.text = value.source
        translation.text = value.translated  // No placeholder or status text in subtitles.
        val hasText = value.translated.isNotBlank() || (options.bilingual && value.source.isNotBlank())
        source.visibility = if (options.bilingual && value.source.isNotBlank()) View.VISIBLE else View.GONE
        translation.visibility = if (value.translated.isNotBlank()) View.VISIBLE else View.GONE
        body.visibility = if (hasText) View.VISIBLE else View.GONE
        body.alpha = options.subtitleOpacity.coerceIn(0f, 1f)
        body.background = GradientDrawable().apply {
            cornerRadius = dp(8).toFloat()
            setColor(Color.argb((options.opacity.coerceIn(0f, 1f) * 255).roundToInt(), 10, 24, 22))
        }
        val width = if (hasText) minOf(context.resources.displayMetrics.widthPixels - dp(16), dp(720)) else dp(40)
        if (params.width != width) params.width = width
        clamp(); update()
    }
    fun close() { if (attached) { runCatching { manager.removeView(container) }; attached = false } }
}
