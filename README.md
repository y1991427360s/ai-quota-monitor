# AI 配额与限额监控看板 (AI & Codex Quota Monitor)

用于实时监控 **OpenAI / Codex (ChatGPT Plus)** 额度、请求频次、Tokens 统计、Prompt 缓存命中率以及 **Google Antigravity** 账号额度的全栈自托管监控看板与异常告警系统。

---

## 🌟 核心特性

- **双模监控看板**：
  - **OpenAI / Codex 额度监控**：
    - 5 小时滑窗请求统计（支持展示限额如 45/45、50/50，以及距离 5h 滚动窗口恢复倒计时）。
    - Tokens 实时统计（总消耗、Prompt 缓存节省比例与节省 Tokens）。
    - 24 小时活跃度与模型调用分布（柱状热度分布图）。
    - 活跃后端节点状态监测（延迟、健康度等）。
    - 支持手动一键发送企业微信测试报警卡片。
  - **Google Antigravity 配额看板**：
    - 多账号切换与额度聚合。
    - Claude / Gemini 官方额度剩余比例与重置倒计时。
    - OAuth 授权一键刷新与登录管理。
- **自动化守护与主动告警**：
  - 常驻后台守护进程，定时自动轮询 sub2api 状态与网关健康度。
  - 遇到 429 Rate Limit、账号认证失效或服务异常时，通过企业微信自建应用实时推送报警卡片。
- **现代化设计风格**：
  - 严格遵循 SEN 工具箱设计系统，暖色调极简无 AI 模板感。
  - 完美适配移动端与暗色/浅色自适应模式。

---

## 🏗️ 项目架构

```
.
├── frontend/             # 前端单页面应用 (HTML + CSS + 原生 JS)
│   └── index.html        # 响应式双 Tab 额度看板
├── backend/              # 后端 API 服务 (FastAPI + Uvicorn)
│   └── server.py         # 数据汇总、OAuth 鉴权、PostgreSQL 统计与企业微信推送
├── daemon/               # 告警守护进程
│   └── codex_monitor.py  # 轮询探测与企业微信主动报警
├── systemd/              # 生产环境 Systemd 服务模板
│   ├── antigravity-quota.service
│   └── codex-monitor.service
├── .env.example          # 环境变量示例
└── README.md
```

---

## 🚀 快速部署指南

### 1. 环境依赖

- Python 3.9+
- FastAPI, Uvicorn, Pydantic
- Docker / PostgreSQL (若需直连 sub2api 统计库)

```bash
pip install fastapi uvicorn pydantic psycopg2-binary
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填写对应配置：

```bash
# Google Antigravity OAuth (可选)
ANTIGRAVITY_OAUTH_CLIENT_ID=your_google_client_id
ANTIGRAVITY_OAUTH_CLIENT_SECRET=your_google_client_secret

# 企业微信告警卡片推送配置
WX_CORP_ID=your_wechat_work_corpid
WX_SECRET=your_wechat_work_secret
WX_AGENT_ID=1000002

# 轮询间隔（秒）
CHECK_INTERVAL_SECONDS=180
```

### 3. 启动后端服务

```bash
# 启动 Quota API 服务 (默认端口 3022)
uvicorn backend.server:app --host 127.0.0.1 --port 3022
```

### 4. 启动告警守护进程

```bash
python3 daemon/codex_monitor.py
```

### 5. 前端部署

将 `frontend/index.html` 放置于 Nginx 静态站点根目录，并配置反向代理：

```nginx
location /quota/api/ {
    proxy_pass http://127.0.0.1:3022/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

---

## 📄 License

MIT License
