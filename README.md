# LangChain Live2D Desktop Pet

一个基于 `PyQt5 + Live2D + LangChain` 的桌面宠物项目，支持大模型对话、语音播报、口型同步与表情/动作联动。

## 功能概览

- Live2D 桌宠透明窗口显示（可拖拽、缩放、点击交互）
- LLM 生成回复并驱动角色表情和动作
- `edge-tts` 语音合成与播放
- 口型同步（支持 viseme / Rhubarb）
- 本地模型与在线模型资源加载

## 快速开始

```powershell
cd h:\langchain_live2d
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

创建并配置 `.env`：

```env
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://openrouter.ai/api/v1
MODEL_NAME=arcee-ai/trinity-large-preview:free
TTS_VOICE=zh-CN-XiaoxiaoNeural
LIVE2D_MODEL_PATH=H:\live2dmodel\mao_pro_en\runtime\mao_pro.model3.json
```

启动：

```powershell
python main.py
```

## 目录结构

- `main.py`: 应用入口
- `app/ui/desktop_pet_window.py`: 桌宠窗口与交互逻辑
- `app/ui/live2d_webview.py`: WebView 与 JS 桥接
- `app/workers/llm_worker.py`: LLM 调用与结果处理
- `app/workers/voice_worker.py`: TTS 与播放逻辑
- `assets/web/index.html`: Live2D 前端渲染与控制

## 最近更新（2026-02-19）

- 更新桌宠窗口交互与 WebView 联动逻辑
- 调整 LLM worker 与语音 worker 的处理流程
- 更新前端页面（`assets/web/index.html`）
- 保持 `.env` 不纳入版本管理（通过 `.gitignore` 排除）

## 注意事项

- 不要提交 `.env`、密钥或本地敏感配置
- 大模型与语音服务需可访问对应 API
- 若模型文件较大，建议使用 Git LFS 管理
