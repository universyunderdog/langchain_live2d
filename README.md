# Live2D LLM Desktop Pet (Windows)

从零搭建的可运行骨架：
- Live2D 桌宠直接显示在桌面前景（透明无边框窗口）
- LLM 控制回复、表情和动作
- edge-tts + pygame 语音播报
- 默认使用公用测试模型（CDN）

## 1. 安装

```powershell
cd h:\langchain_live2d
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. 配置环境变量

创建 `.env` 文件：

```powershell
New-Item .env
```

编辑 `.env`，添加以下内容：
```
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://openrouter.ai/api/v1  # 可选：OpenAI 兼容网关时填写
MODEL_NAME=arcee-ai/trinity-large-preview:free  # 建议先用轻量模型测试
TTS_VOICE=zh-CN-XiaoxiaoNeural
LIVE2D_MODEL_URL=  # 可选：默认已提供公用模型
LIVE2D_MODEL_PATH=H:\live2dmodel\mao_pro_en\runtime\mao_pro.model3.json  # 可选：指向本地 .model3.json / .model.json 文件
```

**注意**：不要提交 `.env` 文件到 GitHub！

本项目还会自动扫描 `H:\live2dmodel` 下的本地模型；若找到，会自动启动本地静态服务并优先加载本地模型。

## 3. 启动

```powershell
python main.py
```

## 4. 项目结构

- `main.py`: 应用入口
- `app/ui/desktop_pet_window.py`: 桌宠窗口、输入框、信号联动
- `app/ui/live2d_webview.py`: PyQt WebEngine 与 JS 桥接
- `app/workers/llm_worker.py`: Agent 工作线程，处理 LLM 交互
- `app/workers/voice_worker.py`: TTS/播放工作线程，处理语音合成和播放
- `app/core/pet_command.py`: 指令解析器，解析 LLM 返回的 JSON
- `assets/web/index.html`: Live2D 渲染和动作/表情控制逻辑

## 5. 功能特性

### 5.1 语音合成
- 使用 edge-tts 进行语音合成
- 支持 Rhubarb Lip Sync 口型同步
- 自动提取 viseme 信息驱动 Live2D 口型

### 5.2 表情和动作
- 支持 6 种基础表情：neutral, happy, sad, angry, surprised, shy
- 支持多种动作：idle, wave, tap_body, flick_head, jump
- 点击桌宠不同部位触发不同反应

### 5.3 交互功能
- 右键点击桌宠打开环形菜单（Chat/Exit）
- 鼠标滚轮缩放桌宠
- 拖拽移动桌宠位置
- 双击点击触发特殊动作

## 6. LLM 输出协议

`LLMWorker` 要求模型返回 JSON：

```json
{
  "reply": "回复文本",
  "expression": "neutral|happy|sad|angry|surprised|shy",
  "motion": "idle|wave|tap_body|flick_head|jump",
  "emotion_timeline": [
    {"text": "分句1", "emotion": "happy"},
    {"text": "分句2", "emotion": "sad"}
  ]
}
```

UI 会自动：
1. 把 `reply` 展示在右侧日志
2. 把 `reply` 发给 `VoiceWorker` 进行 TTS
3. 把 `expression` / `motion` 下发给 Live2D
4. 根据 `emotion_timeline` 在说话过程中动态切换表情

## 7. 可选配置

### 7.1 TTS 配置
在 `.env` 中添加：
```
TTS_USE_SSML=false  # 是否使用 SSML
TTS_RATE=+5%  # 语速
TTS_PITCH=+0Hz  # 音调
TTS_VOLUME=+0%  # 音量
TTS_OUTPUT_FORMAT=riff-24khz-16bit-mono-pcm  # 输出格式

# 口型同步调优
TTS_LIPSYNC_ADVANCE_MS=120  # 口型提前量
TTS_ZERO_VISEME_HOLD_MS=240  # 零 viseme 保持时间
TTS_OPEN_CARRY_MS=480  # 开口保持时间
TTS_MIN_ACTIVE_MOUTH=0.18  # 最小活跃口型值
TTS_DEBUG_LIPSYNC=true  # 调试模式
```

### 7.2 Rhubarb Lip Sync
在 `.env` 中添加：
```
TTS_USE_RHUBARB=true  # 启用 Rhubarb 口型同步
RHUBARB_EXE=H:\Rhubarb-Lip-Sync-1.14.0-Windows\Rhubarb-Lip-Sync-1.14.0-Windows\rhubarb.exe  # Rhubarb 可执行文件路径
RHUBARB_RECOGNIZER=phonetic  # 识别器：phonetic 或 pocketsphinx
```

## 8. 替换你的模型

你有自己的 `.model3.json` 或 `.model.json` 后，只需把 `.env` 中的：

`LIVE2D_MODEL_URL=...`

改为本地 HTTP 地址或可访问 URL。  
如果用本地文件，建议先用轻量静态服务器托管模型目录，避免跨域与资源相对路径问题。

也可以将模型放入 `models/` 目录，项目会自动加载。
