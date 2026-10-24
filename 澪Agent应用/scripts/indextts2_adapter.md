# Mio 本地 IndexTTS2 适配服务

这是 Mio 配套桥接脚本，不是 IndexTeam 官方 REST 服务。根据 2026-10-24 核对的[官方 README](https://github.com/index-tts/index-tts#-python-api)，IndexTTS-2 使用 `indextts.infer_v2.IndexTTS2` 和 `infer(spk_audio_prompt=..., text=..., output_path=...)`。官方 Gradio WebUI 的 7860 地址不能直接填到 Mio；本脚本也不接 IndexTTS-2.5 的 `infer_v2_5`。

先独立准备官方 IndexTTS 源码、它自己的 Python 环境、IndexTTS-2 模型与依赖模型。不要在 Mio 的 Python 环境安装 torch 或 IndexTTS。使用前需在独立环境完成模型准备。

在 PowerShell 中使用 **IndexTTS 自己的 Python** 启动（把示例路径换成实际位置）：

```powershell
& 'D:/AI/index-tts/.venv/Scripts/python.exe' 'D:/Mio-source/澪Agent应用/scripts/indextts2_adapter.py' --repo 'D:/AI/index-tts' --model-dir 'D:/AI/index-tts/checkpoints_2'
```

源码的 `scripts/` 已由现有桌面打包规则整体包含在 `_internal/agent_scripts/`；安装包用户可直接使用安装目录中的脚本和本文档，无需开发机路径。例如 Mio 安装在 `D:/Mio` 时，把上面第二个路径改为 `D:/Mio/_internal/agent_scripts/indextts2_adapter.py`。

`--fp16` 是可选项。脚本固定使用 IndexTTS-2 Python API、禁用 DeepSpeed/CUDA 自定义内核，默认仅监听 `127.0.0.1:11996`。模型构造成功后才开放 `/health`，Ctrl+C 停止。脚本不启动安装器，并设 Hugging Face/Transformers 离线模式；缺权重或缓存时应先按官方方法在独立环境准备，再重启。

Mio 设置 → 角色语音 → IndexTTS2：服务地址填 `http://127.0.0.1:11996`，上传参考音频或填写 **Mio 所在电脑上** 的参考音频完整路径，再点“保存并试听”。与 GPT-SoVITS 不同，不需要参考音频逐字原文或 GPT/SoVITS 权重。参考音频建议干净、单说话人、无配乐，并只使用有权使用的声音。音频上传后保存在 Mio 的个人数据目录；独立服务只在临时目录写入本次参考/输出，完成后清理。

正常聊天、桌宠与 QQ 使用现有语音入口；IndexTTS2 完整合成 WAV 后播放，不宣称流式首音能力。此适配不套用现有 Genie 的中日翻译与短句防泄漏检测；传入清理后的原回复，实际语言能力与声音质量以所用 IndexTTS-2 模型为准。

可连接另一台受信任设备：在该设备上显式加 `--host 0.0.0.0` 并配置防火墙/Mio URL。桥接端口没有登录认证，仅应放在本机或受信任内网；参考音频字节和朗读文本会发到所填服务，远程设备不需要共享相同音频路径。

诊断：

- 连接失败：确认独立服务终端已显示“已就绪”、地址端口正确；Mio 不代管该进程，不会下载模型或把失败切到其他付费服务。
- 协议不符/非音频：通常填了 WebUI、其他项目的 REST 服务或旧版桥接脚本。
- HTTP 500：检查独立环境的模型完整性、参考音频解码、CUDA/显存；桥接返回异常类型，不把文本/音频路径写入 HTTP 错误日志。
- 合成超时：Mio 单次请求上限 300 秒；检查设备速度与输入长度，先用短句试听。

协议是 Mio 自有 `mio-indextts2-v1`：`GET /health` 返回 `ready/protocol`，`POST /synthesize` 接受 `text/reference_name/reference_audio_base64/protocol` 并返回 PCM `audio/wav`。源码测试用真实本地 HTTP 和生成 WAV 夹具验证传输、错误与配置保存；这不等同于真实 IndexTTS2 模型验收。真实验收仍需启动本机模型、试听和正常聊天播放。
