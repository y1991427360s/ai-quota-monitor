# 数据协议与升级说明

## 兼容策略

保留 `/api/chatgpt/*`、`/api/quota`、Google OAuth 和账号操作路由。旧 `five_hour_remaining`、`seven_day_remaining`、`groups[].fiveHour` / `weekly` 等主要字段保留；未知数值由虚拟默认值改为 JSON null。使用完整 `windows` 读取全部窗口，旧兼容字段仅指向第一个匹配窗口，不包含所有相同周期池。

新缓存采用 schema_version=2。旧缓存可能包含生成的模型与假满额，不作为可信快照展示；原 Token 和账号文件不改动，首次查询会重新取值。新版缓存刷新失败后保留原 fetched_at 并递归标记 stale。此为有意修正旧数据语义。

## Codex

- `rate_limits`：`five_hour`、`weekly` 和全量 `windows`。
- `additional_rate_limits`：真实独立池数组；保留池名称与标识。模型标识缺失时展示功能池，不推造模型。
- 每窗口：`kind`（five_hour / weekly / unknown）、`used_percent`、`remaining_percent`、`reset_at`、`window_seconds`、`source`、`fetched_at`、`status`、`stale`。
- `credits`：明确返回时提供 balance、has_credits、unlimited 和状态/来源字段；未返回时为 null。
- `chatgpt_web_quota`：当前 unavailable 与原因；不能将 WHAM Codex 配额等同于网页对话额度。
- 只凭明确标识或秒数归类；primary/secondary 名称本身不表示5H/Weekly。窗口秒数冲突时以实际秒数为准。缺少绝对重置时间时，仅允许用上游明确返回的 reset_after_seconds 与获取时刻换算，不以周期长度推算。

## Google

- `groups`：仅真实 summary 组；每组保留原始 ID、名称、windows、来源和获取时间。模型返回独立存在，不从组记录生成模型。
- 每组窗口：`percentage`（允许 null）、`resetTime`、`kind`、`window_seconds`、`id`、`source`、`fetched_at`、`status`、`stale`。
- `models`：遍历真实 models 字典，不以推荐清单限制；每条保留 id/rawModelId、displayName、provider、family、version、tier、thinkingLevel、remainingPercent、resetTime、windows 与溯源字段。
- Tier 和模型系列分开；未知属性与 ID 仍显示。命名推断仅用于标签，不推断账号权限或是否降级。
- `degradation.status`：缺少可靠路由元数据时 unknown。
- `sources`：分别标记 summary 与 models 数据源可用性。summary 决定组额度，models 决定模型及模型额度；二者不会互相覆盖或继承，避免将组额度冒充模型专属额度。
- 双接口失败时展示可信历史缓存并标记 stale；没有可信缓存则 unavailable。单个接口失败时，只有该来源的历史项标记 stale，其余保留最新来源。

## 状态

| 状态 | 含义 |
| --- | --- |
| available | 接口提供有效额度 |
| exhausted | 接口明确提供剩余0% |
| unavailable | 当前无法获取 |
| stale | 新查询失败或部分来源失败后的历史数据 |
| unknown | 响应存在但缺少可信额度数值 |

重置时间统一规范为 UTC ISO，展示按浏览器时区；无时区 ISO 明确按 UTC 解析。历史额度不代表当前仍有效。错误信息经过脱敏，不包含上游响应体、Token 或授权码。

## OAuth 与部署兼容

OpenAI 和 Google 各有独立的一次性 state/PKCE 缓存，严格校验匹配与有效期，不能回退到另一会话 verifier。升级前已打开的授权页面需要重新发起登录，不影响已经保存的账号和自动刷新。

OAuth state 存于进程内，因此继续按单 worker 运行；多 worker 或多副本部署需另做共享一次性会话存储。不要直接增加 uvicorn worker 数量。

后端新增 quota_parsing.py，旧目录部署必须连同 server.py 一起复制。systemd 模板已改为仓库布局 `/opt/ai-quota-monitor`；现有部署可保留原 unit，无需迁移账号家目录。前端仍是单文件，无构建步骤。
