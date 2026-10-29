package cn.jtranslate.live

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.ComponentName
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
import cn.jtranslate.live.network.testBackendConnection
import cn.jtranslate.live.network.SessionFailure
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
        if (!AppState.live.value.active) {
            val runtime = getSharedPreferences("runtime", MODE_PRIVATE)
            if (runtime.getBoolean("running", false)) AppState.update { it.copy(error =
                "上次字幕未正常结束，可能被系统清理。请检查下方后台运行设置，再重新授权开始。") }
            else runtime.getString("last_stop", "")?.takeIf { it.isNotBlank() }?.let { reason ->
                AppState.update { it.copy(error = "上次停止原因：$reason") }
            }
        }
        setContent {
            val options by settings.options.collectAsState(initial = Options())
            val live by AppState.live.collectAsState()
            var server by remember(options.server) { mutableStateOf(options.server) }
            var token by remember { mutableStateOf("") }
            var notice by remember { mutableStateOf("") }
            var testingConnection by remember { mutableStateOf(false) }
            val colors = lightColorScheme(primary = Color(0xFF165D50), background = Color(0xFFF3F7F3), surface = Color.White)
            MaterialTheme(colorScheme = colors) {
                Surface(Modifier.fillMaxSize()) {
                    Column(Modifier.fillMaxSize().systemBarsPadding().verticalScroll(rememberScrollState()).padding(24.dp),
                        verticalArrangement = Arrangement.spacedBy(16.dp)) {
                        Text("日语实时字幕", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
                        Text("日语 → 简体中文", color = MaterialTheme.colorScheme.primary)
                        Card { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text(if (options.localDemo) "本机演示" else "连接翻译服务", fontWeight = FontWeight.Bold)
                            Text(if (options.localDemo) "演示字幕是固定示例，不代表影片真实对白。"
                                else "连接电脑上的本地模型服务，可识别日语对白并翻译为中文。电脑需保持运行；连接模拟后端时仍显示演示字幕。")
                            Text("开始后请切换到夸克或播放器。不同 App 的采集与覆盖限制以真机结果为准。")
                        } }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            FilterChip(selected = options.localDemo, enabled = !live.active, onClick = {
                                lifecycleScope.launch { settings.save(options.copy(localDemo = true)) }
                            }, label = { Text("本机演示") })
                            FilterChip(selected = !options.localDemo, enabled = !live.active, onClick = {
                                lifecycleScope.launch { settings.save(options.copy(localDemo = false)) }
                            }, label = { Text("连接翻译服务") })
                        }
                        if (!options.localDemo) {
                            OutlinedTextField(server, { server = it }, label = { Text("后端地址") }, singleLine = true,
                                enabled = !live.active && !testingConnection, modifier = Modifier.fillMaxWidth())
                            OutlinedTextField(token, { token = it }, label = { Text("访问令牌（留空保留已保存值）") },
                                visualTransformation = PasswordVisualTransformation(), singleLine = true,
                                enabled = !live.active && !testingConnection, modifier = Modifier.fillMaxWidth())
                            OutlinedButton(enabled = !live.active && !testingConnection, onClick = {
                                lifecycleScope.launch {
                                    runCatching {
                                        val normalized = validateServer(server.trim()).toString().trimEnd('/')
                                        settings.save(options.copy(server = normalized))
                                        if (token.isNotBlank()) settings.saveToken(token.trim())
                                    }.onSuccess { token = ""; notice = "后端配置已保存" }
                                        .onFailure { notice = (it as? SessionFailure)?.reason ?: "保存失败，请检查地址或设备安全存储" }
                                }
                            }) { Text("保存后端配置") }
                            OutlinedButton(enabled = !live.active && !testingConnection, onClick = {
                                lifecycleScope.launch {
                                    testingConnection = true
                                    try {
                                        val normalized = validateServer(server.trim()).toString().trimEnd('/')
                                        settings.save(options.copy(server = normalized))
                                        if (token.isNotBlank()) { settings.saveToken(token.trim()); token = "" }
                                        notice = testBackendConnection(normalized, settings.token()) { notice = it }
                                    } catch (error: Exception) {
                                        notice = (error as? SessionFailure)?.reason ?: "连接测试失败（${error.javaClass.simpleName}）"
                                    } finally { testingConnection = false }
                                }
                            }) { Text(if (testingConnection) "正在测试连接…" else "测试后端连接") }
                            Text("地址只填写 https://域名；不要加 /health。更换地址或令牌后先保存并测试。", style = MaterialTheme.typography.bodySmall)
                        }
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text("显示原文（日语）")
                            Switch(options.bilingual, { lifecycleScope.launch { settings.save(options.copy(bilingual = it)) } })
                        }
                        Text("状态：${live.status}", fontWeight = FontWeight.Bold)
                        LinearProgressIndicator(progress = { (live.rms * 8).coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth())
                        Text("内部音量 ${(live.rms * 100).toInt()}% · 音频帧 ${live.frames}", style = MaterialTheme.typography.bodySmall)
                        if (live.networkRttMs > 0) Text("网络往返 ${live.networkRttMs} ms", style = MaterialTheme.typography.bodySmall)
                        if (live.error.isNotEmpty()) Text(live.error, color = MaterialTheme.colorScheme.error)
                        if (notice.isNotEmpty()) Text(notice)
                        if (!live.active) Button(enabled = !testingConnection, onClick = { requestStart() }, modifier = Modifier.fillMaxWidth()) { Text("授权并开始") }
                        else {
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Button(onClick = { CaptureService.command(this@MainActivity, CaptureService.TOGGLE) }) { Text(if (live.paused) "继续" else "暂停") }
                                OutlinedButton(onClick = { CaptureService.command(this@MainActivity, CaptureService.STOP) }) { Text("停止") }
                            }
                            TextButton(onClick = { CaptureService.command(this@MainActivity, CaptureService.CLEAR) }) { Text("清空并重新跟随") }
                        }
                        HorizontalDivider()
                        Text("字幕样式", style = MaterialTheme.typography.titleMedium)
                        Text("字号 ${options.fontSize.toInt()}")
                        Slider(options.fontSize, { lifecycleScope.launch { settings.save(options.copy(fontSize = it)) } }, valueRange = 14f..36f)
                        Text("背景透明度 ${((1f - options.opacity) * 100).toInt()}%（100% 完全透明）")
                        Slider(1f - options.opacity, { lifecycleScope.launch { settings.save(options.copy(opacity = 1f - it)) } }, valueRange = 0f..1f)
                        Text("整层字幕透明度 ${((1f - options.subtitleOpacity) * 100).toInt()}%（不影响关闭按钮）")
                        Slider(1f - options.subtitleOpacity, { lifecycleScope.launch { settings.save(options.copy(subtitleOpacity = 1f - it)) } }, valueRange = 0f..1f)
                        if (live.translated.isNotEmpty()) Card { Column(Modifier.padding(16.dp)) {
                            if (options.bilingual) Text(live.source)
                            Text(live.translated, fontWeight = FontWeight.Bold)
                        } }
                        Text("仅采集内部播放声音，不使用麦克风。默认不保存音频和字幕。后端联调模式会上传音频。", style = MaterialTheme.typography.bodySmall)
                        HorizontalDivider()
                        Text("后台运行保护", style = MaterialTheme.typography.titleMedium)
                        Text("华为：在应用启动管理中关闭自动管理，允许后台活动；在最近任务中锁定本应用。字幕运行时保留前台通知。", style = MaterialTheme.typography.bodySmall)
                        Text("网络中断会保留当前字幕并重连，不再因短时断网自动退出。系统结束音频捕获后仍需重新授权。", style = MaterialTheme.typography.bodySmall)
                        OutlinedButton(onClick = { openBackgroundSettings() }) { Text("打开华为启动管理 / 应用设置") }
                        OutlinedButton(onClick = {
                            runCatching { startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) }
                                .onFailure { openBackgroundSettings() }
                        }) { Text("打开电池优化设置") }
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

    private fun openBackgroundSettings() {
        val huawei = Intent().setComponent(ComponentName("com.huawei.systemmanager",
            "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity"))
        runCatching { startActivity(huawei) }.onFailure {
            startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, "package:$packageName".toUri()))
        }
    }

    private fun requestCapture() {
        projectionResult.launch(getSystemService(MediaProjectionManager::class.java).createScreenCaptureIntent())
    }
}
