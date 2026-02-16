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

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：
- `OPENAI_API_KEY` 必填
- `OPENAI_BASE_URL` 可选（OpenAI 兼容网关时填写）
- `MODEL_NAME` 建议先用轻量模型测试
- `LIVE2D_MODEL_URL` 默认已提供公用模型
- `LIVE2D_MODEL_PATH` 可选：指向本地 `.model3.json` / `.model.json` 文件

本项目还会自动扫描 `H:\live2dmodel` 下的本地模型；若找到，会自动启动本地静态服务并优先加载本地模型。

## 3. 启动

```powershell
python main.py
```

## 4. 项目结构

- `main.py`: 应用入口
- `app/ui/desktop_pet_window.py`: 桌宠窗口、输入框、信号联动
- `app/ui/live2d_webview.py`: PyQt WebEngine 与 JS 桥接
- `app/workers/llm_worker.py`: 参考你现有风格的 Agent 工作线程
- `app/workers/voice_worker.py`: 参考你现有风格的 TTS/播放工作线程
- `assets/web/index.html`: Live2D 渲染和动作/表情控制逻辑

## 5. LLM 输出协议

`LLMWorker` 要求模型返回 JSON：

```json
{
  "reply": "回复文本",
  "expression": "neutral|happy|sad|angry|surprised|shy",
  "motion": "idle|wave|tap_body|flick_head|jump"
}
```

UI 会自动：
1. 把 `reply` 展示在右侧日志
2. 把 `reply` 发给 `VoiceWorker` 进行 TTS
3. 把 `expression` / `motion` 下发给 Live2D

## 6. 替换你的模型

你有自己的 `.model3.json` 或 `.model.json` 后，只需把 `.env` 中的：

`LIVE2D_MODEL_URL=...`

改为本地 HTTP 地址或可访问 URL。  
如果用本地文件，建议先用轻量静态服务器托管模型目录，避免跨域与资源相对路径问题。
