# AI 配额与限额监控看板

自托管的 Codex 共享额度、独立功能额度、Credits 与 Google Antigravity 配额看板。保留多账号、OAuth、自动刷新、缓存和企业微信告警。额度只来自实际授权接口响应，模型资料与套餐介绍不代表当前账号的权限。

## 数据规则

- Codex 来源为 `https://chatgpt.com/backend-api/wham/usage`。共享额度不绑定任何具体模型；`additional_rate_limits` 动态显示真实独立池。
- 缺失百分比返回 `null`；0% 代表耗尽，100% 必须有数据支撑。未知窗口、重复周期的独立池、原始周期秒数和未知模型均保留。
- 只凭明确窗口标识或可信秒数识别 5 小时和 7 天；不按窗口顺序、套餐或当前时间补造额度与重置时间。
- Google summary 组额度优先，models 补充真实模型列表与各自额度。组额度不复制成单模型额度，失败不生成虚拟模型。
- 历史缓存记录实际获取时间、来源与 stale 状态。401、403、429、超时和服务错误独立呈现；429 本身不能证明 5 小时额度耗尽。
- Credits 区分数字余额、明确无上限和不可用。ChatGPT 网页独立额度缺少可靠授权接口时显示“目前无法获取”。

详见 [官方来源与接口边界](docs/SOURCES.md)、[数据协议](docs/DATA_CONTRACT.md) 和 [测试报告](docs/TEST_REPORT.md)。

## 目录

- `backend/server.py`：FastAPI、OAuth、查询和缓存。
- `frontend/index.html`：单文件看板、模型搜索、Provider 筛选与原始 ID。
- `daemon/codex_monitor.py`：sub2api 限流记录与企业微信告警。
- `systemd/`：需要按部署用户调整的服务模板。
- `tests/`：脱敏离线回归和浏览器自动测试。

## 本地启动

Python 3.10+，Node.js 仅用于浏览器测试。不要把生产 Token 复制到仓库。

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
cp .env.example .env
python -m uvicorn backend.server:app --host 127.0.0.1 --port 3022
```

进程环境变量优先于 .env。Google OAuth 需要可用客户端配置；OpenAI 继续使用既有 Codex OAuth 流程。账号文件仍在运行用户的 `~/.config/antigravity-usage/` 和 `~/.config/chatgpt-usage/`，不会迁移或删除现有数据。

前端托管到 `/quota/`，Nginx 必须保留后端 `/api/` 路径：

```nginx
location /quota/api/ {
    proxy_pass http://127.0.0.1:3022/api/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 60s;
}
```

继续使用现有登录门禁，API 仅监听本机。模板不含生产 Cookie、口令或证书。

## systemd 与旧目录兼容

仓库模板使用 `/opt/ai-quota-monitor`。安装时按实际用户调整 User、WorkingDirectory、ExecStart 和 EnvironmentFile，先安装 .venv 与配置 .env。

旧服务器后端目录为 `/home/ubuntu/antigravity-quota-service`，告警目录为 `/home/ubuntu/codex-monitor`。保留原部署目录时继续使用原服务配置，只同步对应源码；不要覆盖 .env 和家目录账号文件。仓库 backend 下的配套模块也必须一并复制。

```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart antigravity-quota codex-monitor
curl --fail http://127.0.0.1:3022/api/status
```

告警守护进程检查 sub2api PostgreSQL 需要本机 Docker、sudo 权限及对应容器。数据库冷却记录不代表真实配额耗尽，不会在缺少窗口证据时标为 5 小时耗尽。

## 自动测试

```bash
python -m pytest tests -v
python tests/smoke_server.py
npm ci
npx playwright install chromium
npm run test:browser
```

浏览器测试使用合成脱敏响应，截图在 `output/playwright/`。测试不能证明真实账号具有某个模型或额度。

## 兼容性

保留原 API 路径与主要字段，新增窗口、状态与溯源字段。语义修正：未知百分比为 null，不再生成虚拟模型，不再根据 Low 路由推断降级，OAuth 回调必须携带当前会话的有效 state。依赖旧版虚拟 100% 或无 state 回调的自定义客户端需要同步调整。

首次代码升级未自动部署；用户随后反馈旧页面后，已部署前后端，见 [生产部署记录](docs/DEPLOYMENT_20261015.md)。未调用真实企业微信推送。

