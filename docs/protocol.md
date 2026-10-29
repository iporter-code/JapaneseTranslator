# 已实现的字幕协议 v1

基于完整方案第 10 节。`POST /v1/sessions` 使用后端配置的应用令牌，返回随机会话 ID、短期流令牌和模式标签；120 秒内连接。`DELETE /v1/sessions/{id}` 可重复调用。

WebSocket `/v1/sessions/{id}/stream` 在 Authorization 请求头使用流令牌。先发送 `{"type":"stream.start","version":1,"epoch":1}`，等待 `stream.ready`。每个连接只使用一个 epoch。

二进制音频头为 `>4sBBHIQI`，24 字节：`JTR1`、版本 1、标志 0、头长 24、32 位序号、64 位首采样位置、32 位采样数。元数据大端，负载为小端 PCM16、单声道、16 kHz。标准帧 100 ms，采样数 1600，PCM 3200 字节。序号和首采样位置从 0 开始连续递增。单帧最多 3200 采样。

支持 `asr.partial`、`asr.final`、`translation.partial`、`translation.final`、`session.error`、`pong`。字幕绑定 epoch、segment_id、source_revision。旧原文版本的译文不能覆盖新版本。

控制消息：`ping` 保持应用层活跃，`stream.stop` 结束上传。每 30 秒必须有音频或应用层消息。暂停、继续、清空和断线恢复通过关闭原会话并创建新会话完成，不在同一连接中复用识别状态。尚未实现句子合并/拆分的 `segment.replace`，当前供应商适配器不产生该事件。

模拟服务只对非静音输入产生固定演示文本，不识别真实内容。服务器限制消息大小、音频超前量、任务队列、活跃会话数与时长，不保存音频和字幕正文。

0.2 增加 `provider=local`，复用上述音频帧与字幕事件，`demo=false`。`/health` 增加 `ready` 与 `max_sessions`；模型在接受会话前加载、预热完成，本地模式只接纳一条会话。`stream.stop` 表示立即取消并丢弃未完成内容，不承诺刷新末尾残句；正常播放时用后续静音触发最终断句。相关错误码还包括 `provider_overloaded`、`provider_empty_result`、`provider_truncated_result`，不会用固定演示文字替代模型失败。
