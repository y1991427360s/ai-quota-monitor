# AI 配额与限额监控看板 (AI & Codex Quota Monitor)

用于实时监控 **OpenAI / Codex (ChatGPT Plus)** 额度、请求频次、Tokens 统计、Prompt 缓存命中率以及 **Google Antigravity** 账号额度的全栈自托管监控看板与异常告警系统。

---

## 🌟 核心特性

- **双模监控看板**：
  - **OpenAI / Codex 额度监控**：
    - **双窗口限额监控**：精准按 `limit_window_seconds`（18000 秒与 604800 秒）分类 **Five Hour Limit (5小时滚动额度)** 与 **Weekly Limit (7天周额度)** 真实剩余百分比与精确重置倒计时。
    - **Codex 共享额度池**：去除了旧版硬编码过时模型名称，动态展示真实共享配额；支持动态展示 `additional_rate_limits` 专属模型/功能配额与 `credits` 余额。
    - **数据新鲜度与真实容灾**：支持 `fetched_at` 更新时刻标记与历史快照 `stale` 提示，异常时拒绝伪造 100% 假数据。
    - 多账号切换与 OAuth 快捷授权登录。
  - **Google Antigravity 配额看板**：
    - 多账号切换与额度聚合。
    - **双窗口限额监控**：原生支持 **Five Hour Limit (5小时滚动额度)** 与 **Weekly Limit (7天周额度)** 实时消耗百分比与精确重置倒计时。
    - **双模型池独立划分**：分别追踪 Gemini Models (Gemini 3.8 Flash / 3.1 Pro) 及 Claude and GPT models (Claude Sonnet / Opus / GPT-OSS) 的共享配额。
    - **高可用三级智能 Fallback 架构**：
      1. 首选 Antigravity 官方内部 `retrieveUserQuotaSummary` 接口直接获取服务端原生 5H + Weekly 聚合配额；
      2. 若接口不可用或账号未下发周配额，自动平滑回退至 `fetchAvailableModels` 保持 5 小时额度精准刷新；
      3. 双接口异常时自动回退本地最近一次有效快照缓存，确保看板高可用不闪断。
    - 严格识别 `0%` 耗尽合法状态，OAuth 授权一键刷新与登录管理。
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
