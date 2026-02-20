# LangChain Live2D Desktop Pet

一个基于 `PyQt5 + Live2D + LangChain` 的桌面宠物项目，支持大模型对话、语音播报、口型同步、记忆存储与主动聊天。

## 功能概览

- Live2D 桌宠透明窗口显示（可拖拽、缩放、点击交互）
- LLM 生成回复并驱动角色表情和动作
- `edge-tts` 语音合成与播放
- 口型同步（支持 viseme / Rhubarb）
- 本地模型与在线模型资源加载
- 对话记忆管理与主动聊天机制

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
- `app/core/memory.py`: 记忆管理逻辑
- `app/core/proactive_chat.py`: 主动聊天策略
- `app/ui/desktop_pet_window.py`: 桌宠窗口与交互逻辑
- `app/ui/chat_window.py`: 聊天窗口
- `app/ui/action_menu.py`: 操作菜单 UI
- `app/ui/speech_bubble.py`: 气泡消息 UI
- `app/workers/llm_worker.py`: LLM 调用与结果处理
- `assets/web/index.html`: Live2D 前端渲染与控制
- `memory/`: 记忆数据目录

## 最近更新（2026-02-20）

- 新增记忆相关模块：`app/core/memory.py`
- 新增主动聊天模块：`app/core/proactive_chat.py`
- 新增 UI 组件：`app/ui/action_menu.py`、`app/ui/speech_bubble.py`
- 更新聊天与主窗口逻辑：`app/ui/chat_window.py`、`app/ui/desktop_pet_window.py`
- 更新 LLM 处理与前端页面：`app/workers/llm_worker.py`、`assets/web/index.html`
- 新增记忆文件：`memory/2026-02-20.md`
- `.env` 持续通过 `.gitignore` 排除，不纳入版本管理

## 注意事项

- 不要提交 `.env`、密钥或本地敏感配置
- 大模型与语音服务需可访问对应 API
- 若模型文件较大，建议使用 Git LFS 管理
