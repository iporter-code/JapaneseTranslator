# 日语实时字幕

当前是 **0.2 本地模型联调版**：Android 采集内部音频并显示悬浮字幕，电脑运行 Whisper 日语识别和 Hy-MT2 中文翻译。真实模型已接入后端并通过本机实时音频回放测试；手机采音、上传及夸克兼容性仍待连接真机验证。默认保留无需电脑的本机演示模式，该模式只显示固定字幕。

最低 Android 10 / API 29，不依赖 Google Play Services。优先验证设备为华为 Mate 40 Pro / 鸿蒙 4.2，目标 App 为夸克；目前尚未连接真机验证。系统或目标 App 禁止音频采集、禁止悬浮覆盖时，不能保证可用。

## 手机直接联网，不接 USB

电脑执行 `.\scripts\start-internet-backend.ps1`，等待显示 `Phone server address`，在手机现有 APK 的“连接翻译服务”中保存该 HTTPS 地址和自己的应用访问令牌。手机不需要 Tailscale、VPN 或其他额外软件，电脑需保持开机。该免账号测试通道的地址重启后可能变化；完整步骤见 [电脑作为无线翻译服务器](docs/电脑作为无线翻译服务器.md)。

## 先在手机测试

1. 安装 `artifacts/日语实时字幕-0.2.0-local.apk`。这是使用测试签名的调试 APK。
2. 选择“本机演示”，无需服务账号、无需购买云服务、无需配置后端。
3. 点击“授权并开始”，按引导开启悬浮窗和录音权限，再通过系统捕获授权。
4. 切换到夸克或播放器，播放有声音的内容。默认不会使用麦克风。
5. 输入音量大于阈值并持续约 2 秒后，显示带“演示”标签的固定字幕。
6. 测试耳机、横竖屏、拖动、暂停、继续、停止。没有采到声音时查看主界面诊断。

本机演示不联网、不保存音频和字幕。演示能验证采集和显示，不代表已经具备真实翻译效果或成人题材识别能力。

## 在电脑运行模拟后端

从项目根目录使用 PowerShell：

```powershell
.\scripts\setup-backend.ps1
.\scripts\start-backend.ps1
```

配置文件是被 Git 忽略的 `backend/.env`，安装脚本自动创建随机访问令牌。不要把该文件、令牌或云服务密钥公开。服务默认只监听本机。

连接已开启 USB 调试并在手机上授权的设备，然后在另一个终端执行：

```powershell
.\scripts\connect-phone.ps1
```

在调试 APK 中选择“连接翻译服务”，填写 `http://127.0.0.1:8765`，将自己的后端访问令牌填入输入框并保存。应用使用 Android Keystore 加密保存令牌。这里不是填写 Azure 的 API 密钥。

后端联调会上传真实内部音频，默认模拟服务不会识别它，只按非静音输入生成带标签的固定演示字幕。暂停和继续使用新会话，清空旧字幕和上下文。远程地址必须使用 HTTPS；调试版仅对本机调试地址允许明文。

## 运行测试

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest -q
```

后端启动后，可从项目根目录执行合成音频的端到端测试：

```powershell
.\backend\.venv\Scripts\python.exe .\evaluation\smoke_client.py
```

本机安卓构建、单元测试与 Lint（脚本会为中文目录建立临时 ASCII 盘符映射，结束后移除）：

```powershell
.\scripts\build-android.ps1
```

首次构建需要 JDK 17 或兼容版本、Android SDK 36、Build Tools 35.0.0 和网络下载依赖。脚本可用 `-SdkRoot`、`-JavaHome` 指定本机位置。工程固定 AGP 8.11.1、Gradle 8.13、Kotlin 2.1.20，采用已知兼容组合进行首次构建。其他 ASCII 路径下也可通过 `android/gradlew.bat` 构建。不要把当前机器的 `local.properties` 提交到版本库。

## 现在使用本地真实翻译

本机已准备好模型与运行环境。启动电脑模型服务，然后连接手机：

```powershell
.\scripts\start-local-backend.ps1
# 在另一个 PowerShell 窗口执行，需手机已开启 USB 调试并授权：
.\scripts\connect-phone.ps1 -Install
```

手机选择“连接翻译服务”，保存本机地址和自己的后端访问令牌。此时音频通过 USB 送到电脑，本地模型生成字幕并返回手机；电脑必须保持运行，无需收费服务账号。默认只允许一条本地模型会话。

启动说明、延迟口径、真实限制和复现命令见 [本地模型联调](docs/本地模型联调.md)。模型权重位于电脑上，当前版本不在手机内运行模型。无线联网使用上面的 HTTPS 通道；本机 HTTP 地址仅用于 USB 调试。

## 可选云服务适配

后端已提供 Azure Speech 连续识别与 Azure Translator 适配器。取得账号后，在 `backend/.env` 中设置 `PROVIDER=azure` 和相应区域、凭证，再重启服务；长期供应商密钥只放在后端。

在正式使用前需验证日语质量、网络、费用、内容使用规则和成人题材保真。Raw / NoAction 选项不保证任意内容都受支持。目前未调用真实云服务、未花费云服务费用，也没有完成对应质量验收。

模型初步质量和错误样例见 [开源模型实测报告](docs/开源模型实测报告.md)，独立模型评测步骤见 [模型测试说明](evaluation/README.md)。0.2 已接入本地提供器，最终译文仍可能误识别或误译，不保证所有题材准确。

阶段进度见 [开发进度](docs/开发进度.md)，操作故障排查见 [真机测试步骤](docs/真机测试步骤.md)。
