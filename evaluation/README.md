# 本地开源模型实测

测试对象：Whisper large-v3-turbo / faster-whisper + Hy-MT2-1.8B Q4_K_M / llama.cpp。结果与完整结论见 `docs/开源模型实测报告.md`。本目录的真实模型测试独立于 App，应用和现有后端仍默认模拟模式。

## 复现

在项目根目录使用 PowerShell：

```powershell
python -m venv evaluation\.venv-models
python -m pip --python evaluation\.venv-models install -r evaluation\requirements-models-lock.txt
.\evaluation\.venv-models\Scripts\python.exe evaluation\fetch_ranged_assets.py
$env:PYTHONIOENCODING = 'utf-8'
.\evaluation\.venv-models\Scripts\python.exe evaluation\model_benchmark.py --stage all --device cuda --translation-gpu-layers 99 --beam-size 5 --label gpu
.\evaluation\.venv-models\Scripts\python.exe evaluation\prompt_comparison.py
```

锁文件对应本机 Windows x64、Python 3.13 环境；其他系统需要重新核对运行库和二进制程序。需 NVIDIA CUDA 兼容显卡和驱动；运行库通过隔离环境安装，不修改系统驱动。

首次需要下载约 2.75 GB 模型权重、0.28 GB 推理程序、0.15 GB 测试集及推理依赖。下载器支持分段续传并核对 SHA-256，路径和固定版本在 `model-manifest.json`。模型、音频、环境和分片缓存被 Git 忽略。国内权重镜像与原文件使用相同 SHA-256 校验。

注意固定 `av==16.1.0`：本次实测时 PyAV 19 的接口与 faster-whisper 1.2.1 不兼容，不能任意升级。

CPU 对照：

```powershell
.\evaluation\.venv-models\Scripts\python.exe evaluation\model_benchmark.py --stage all --device cpu --translation-gpu-layers 0 --label cpu
```

指标函数检查：

```powershell
.\evaluation\.venv-models\Scripts\python.exe -m unittest discover -s evaluation -p test_metrics.py
```

## 数据与结果

- `samples/`：公开日语测试集和提取的音频，不提交版本库。
- `models/`：本地权重，不提交版本库。
- `results/asr-gpu.json`：beam=5 的 GPU 识别结果。
- `results/asr-gpu-beam1.json`：beam=1 的 GPU 识别对照。
- `results/translation-gpu.json`：确认 CUDA 后的短句翻译。
- `results/pipeline-gpu.json`：ASR 和翻译都使用 GPU、两个模型同时驻留的整段链路。
- `results/*-cpu.json`：CPU 基线。
- `results/*-hybrid.json`：ASR 使用 GPU、翻译使用 CPU 的对照，不作为全 GPU 数字。
- `results/prompt-comparison.json`：默认与忠实翻译指令的已知错误对照。
- `results/translation_startup-gpu.json`：运行配置与真实 CUDA0 设备信息。

模型测试服务仅监听 `127.0.0.1:8788`，脚本结束后自动关闭，并移除自己创建的临时 ASCII 盘符映射。运行前确保该端口空闲。全部推理在电脑本地执行，没有付费 API 调用。

## 0.2 真实流式链路

`backend/app/local_runtime.py` 和 `streaming.py` 已把模型接入应用协议。启动、USB 手机联调与配置见 [本地模型联调](../docs/本地模型联调.md)。

```powershell
# 需要本地模型环境和公开 WAV 已准备好，8765/8788 端口空闲。
.\evaluation\.venv-models\Scripts\python.exe evaluation/run_streaming_check.py --repeat 2
```

该检查自行启动常驻模型服务，以真实播放速度重复上传 10 段公开音频，再创建新 epoch 上传 6 秒静音，最后关闭服务并检查端口释放。`results/streaming-local-stable.json` 是采用两次识别共同前缀、400 ms 前导音频的当前实现报告；`streaming-local.json` 和 `streaming-local-stable-preroll200.json` 保留调试前后对照。字幕事件的延迟从 `end_audio_ms` 计起，包含的音频已带 VAD 静音，不等于实际说话结束或手机延迟。报告中的文本用于复核，不自动判为翻译正确。
