package cn.jtranslate.live

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.content.pm.PackageManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.core.net.toUri
import androidx.lifecycle.lifecycleScope
import cn.jtranslate.live.capture.CaptureService
import cn.jtranslate.live.settings.AppSettings
import cn.jtranslate.live.settings.Options
import cn.jtranslate.live.network.validateServer
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private lateinit var settings: AppSettings
    private val audioPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) requestStart() else AppState.update { it.copy(error = "内部音频采集需要录音权限") }
    }
    private val notifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) { requestCapture() }
    private val projectionResult = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode == Activity.RESULT_OK && result.data != null) {
            ContextCompat.startForegroundService(this, Intent(this, CaptureService::class.java).setAction(CaptureService.START)
                .putExtra("projection_code", result.resultCode).putExtra("projection_data", result.data))
        } else AppState.update { it.copy(error = "已取消系统音频捕获授权") }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        settings = AppSettings(this)
        setContent {
            val options by settings.options.collectAsState(initial = Options())
            val live by AppState.live.collectAsState()
            var server by remember(options.server) { mutableStateOf(options.server) }
            var token by remember { mutableStateOf("") }
            var notice by remember { mutableStateOf("") }
            val colors = lightColorScheme(primary = Color(0xFF165D50), background = Color(0xFFF3F7F3), surface = Color.White)
            MaterialTheme(colorScheme = colors) {
                Surface(Modifier.fillMaxSize()) {
                    Column(Modifier.fillMaxSize().systemBarsPadding().verticalScroll(rememberScrollState()).padding(24.dp),
                        verticalArrangement = Arrangement.spacedBy(16.dp)) {
                        Text("日语实时字幕", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
                        Text("日语 → 简体中文", color = MaterialTheme.colorScheme.primary)
                        Card { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text(if (options.localDemo) "本机演示" else "后端联调", fontWeight = FontWeight.Bold)
                            Text("当前版本用于验证内部音频、悬浮字幕与传输。演示字幕是固定示例，不代表影片真实对白。")
                            Text("开始后请切换到夸克或播放器。不同 App 的采集与覆盖限制以真机结果为准。")
                        } }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            FilterChip(selected = options.localDemo, enabled = !live.active, onClick = {
                                lifecycleScope.launch { settings.save(options.copy(localDemo = true)) }
                            }, label = { Text("本机演示") })
                            FilterChip(selected = !options.localDemo, enabled = !live.active, onClick = {
                                lifecycleScope.launch { settings.save(options.copy(localDemo = false)) }
                            }, label = { Text("后端联调") })
                        }
                        if (!options.localDemo) {
                            OutlinedTextField(server, { server = it }, label = { Text("后端地址") }, singleLine = true,
                                enabled = !live.active, modifier = Modifier.fillMaxWidth())
                            OutlinedTextField(token, { token = it }, label = { Text("访问令牌（留空保留已保存值）") },
                                visualTransformation = PasswordVisualTransformation(), singleLine = true,
                                enabled = !live.active, modifier = Modifier.fillMaxWidth())
                            OutlinedButton(enabled = !live.active, onClick = {
                                lifecycleScope.launch {
                                    runCatching {
                                        validateServer(server)
                                        settings.save(options.copy(server = server))
                                        if (token.isNotBlank()) settings.saveToken(token.trim())
                                    }.onSuccess { token = ""; notice = "后端配置已保存" }
                                        .onFailure { notice = "保存失败，请检查地址或设备安全存储" }
                                }
                            }) { Text("保存后端配置") }
                        }
                        Text("状态：${live.status}", fontWeight = FontWeight.Bold)
                        LinearProgressIndicator(progress = { (live.rms * 8).coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth())
                        Text("内部音量 ${(live.rms * 100).toInt()}% · 音频帧 ${live.frames}", style = MaterialTheme.typography.bodySmall)
                        if (live.error.isNotEmpty()) Text(live.error, color = MaterialTheme.colorScheme.error)
                        if (notice.isNotEmpty()) Text(notice)
                        if (!live.active) Button(onClick = { requestStart() }, modifier = Modifier.fillMaxWidth()) { Text("授权并开始") }
                        else {
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Button(onClick = { CaptureService.command(this@MainActivity, CaptureService.TOGGLE) }) { Text(if (live.paused) "继续" else "暂停") }
                                OutlinedButton(onClick = { CaptureService.command(this@MainActivity, CaptureService.STOP) }) { Text("停止") }
                            }
                            TextButton(onClick = { CaptureService.command(this@MainActivity, CaptureService.CLEAR) }) { Text("清空并重新跟随") }
                        }
                        HorizontalDivider()
                        Text("字幕样式", style = MaterialTheme.typography.titleMedium)
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text("显示日语原文")
                            Switch(options.bilingual, { lifecycleScope.launch { settings.save(options.copy(bilingual = it)) } })
                        }
                        Text("字号 ${options.fontSize.toInt()}")
                        Slider(options.fontSize, { lifecycleScope.launch { settings.save(options.copy(fontSize = it)) } }, valueRange = 14f..36f)
                        Text("字幕背景透明度")
                        Slider(options.opacity, { lifecycleScope.launch { settings.save(options.copy(opacity = it)) } }, valueRange = .15f..1f)
                        if (live.translated.isNotEmpty()) Card { Column(Modifier.padding(16.dp)) {
                            if (options.bilingual) Text(live.source)
                            Text(live.translated, fontWeight = FontWeight.Bold)
                        } }
                        Text("仅采集内部播放声音，不使用麦克风。默认不保存音频和字幕。后端联调模式会上传音频。", style = MaterialTheme.typography.bodySmall)
                        Text("华为：若后台被系统结束，请在应用启动管理中检查后台运行设置。", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
    }

    private fun requestStart() {
        AppState.update { it.copy(error = "") }
        if (!Settings.canDrawOverlays(this)) {
            AppState.update { it.copy(error = "请开启悬浮窗权限，返回后再次点击开始") }
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, "package:$packageName".toUri()))
            return
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            audioPermission.launch(Manifest.permission.RECORD_AUDIO); return
        }
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
            return
        }
        requestCapture()
    }

    private fun requestCapture() {
        projectionResult.launch(getSystemService(MediaProjectionManager::class.java).createScreenCaptureIntent())
    }
}
