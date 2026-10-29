package cn.jtranslate.live

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update

data class LiveState(
    val active: Boolean = false,
    val paused: Boolean = false,
    val status: String = "准备就绪",
    val rms: Float = 0f,
    val source: String = "",
    val translated: String = "",
    val demo: Boolean = true,
    val frames: Long = 0,
    val epoch: Int = 0,
    val lastSubtitleAt: Long = 0,
    val networkRttMs: Long = 0,
    val error: String = ""
)

object AppState {
    val live = MutableStateFlow(LiveState())
    fun update(block: (LiveState) -> LiveState) = live.update(block)
}
