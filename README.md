# 基于 LLM 幻觉的模拟软件公司

基于 Godot 4.x + FastAPI + LLM 的多智能体办公室模拟系统。

## 项目结构

- `backend/` — FastAPI Python 后端（WebSocket + LLM 决策）
- `scenes/` — Godot 场景脚本（UI、状态机、网络通信）
- `characters/` — 员工角色（GDScript）
- `ui/` — 像素风 UI 主题系统
- `fonts/` — 中文字体资源
- `web/` — Web 导出文件

## 后端启动

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# 配置 .env 里的 MIMO_API_KEY
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## WebSocket 地址

```
ws://127.0.0.1:8000/ws/office
```

## 技术栈

- **前端**: Godot 4.x (GDScript)
- **后端**: FastAPI + WebSocket
- **AI**: AutoGen + LLM 函数调用
- **部署**: Nginx + Uvicorn
