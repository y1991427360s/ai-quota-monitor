# 升级验收报告

日期：2026-10-15。所有样本均为合成脱敏数据，不包含真实账号或凭据。

## 验证结果

- Python：`python -m pytest tests -v`，54 项全部通过。
- 浏览器：`npm run test:browser` 通过，8 张截图，桌面 1440px、手机 390px、浅色与暗色覆盖；无未捕获 JavaScript 异常、无手机横向溢出。
- 本地服务：`python tests/smoke_server.py` 连续两次启动/停止同一测试端口，均通过 `/api/health` 检查（schema_version=2），结束后清理自身测试进程。
- 语法：Python compileall 与浏览器实际脚本执行通过。
- Git：提交前执行 `git diff --check`，未发现空白错误。
- 测试依赖产生 1 条 Starlette/httpx 弃用警告，测试均通过；不影响产品行为。

## 需求覆盖

| 项目 | 验证方式 |
| --- | --- |
| Codex 5H/7D、浮点、0/100 | test_codex_two_windows_and_float_values 与边界参数用例 |
| 专属模型池、功能池及未来模型 | test_additional_new_model_and_feature_pools |
| 缺失 used_percent、非法值、不伪造满额 | test_codex_missing_invalid_zero_full；前端 percentValue 断言 |
| 429 未指定窗口 | test_codex_http_errors_do_not_imply_exhaustion |
| 401/403/500 | 同上参数用例 |
| 未知窗口、周期冲突 | test_unknown_windows_preserved_and_no_duration_guess |
| Credits 数字、无上限、不可用 | test_credits_states |
| Google Argon 未知路由、多个3.x、新Claude/GPT | test_google_all_real_models_and_metadata |
| 不按前缀替代型号 | test_google_no_prefix_model_substitution |
| 仅组额度，不造模型 | test_google_groups_only_does_not_fabricate_models |
| 仅模型额度，不造 Weekly/组额度 | test_google_models_only_does_not_fabricate_weekly_or_groups |
| 未知Bucket、同周期多个池、乱序与缺失数值 | test_google_buckets_order_duplicates_unknown_and_null |
| 多源优先，不将组值复制到模型 | test_google_source_priority_separates_model_and_group |
| 接口失败、旧缓存拒绝与stale | test_google_failure_stale_and_legacy_rejection |
| 超时、缓存过期、刷新失败 | test_codex_expired_cache_stale_and_timeout；Google凭据刷新失败缓存测试 |
| Unix 秒/毫秒、ISO、时区 | test_time_normalization；浏览器时间解析 |
| 模型搜索、Provider过滤、只看有额度、全量/ID展开 | tests/browser.mjs |
| 模型/邮箱/错误恶意字符串 | 浏览器DOM与脚本未执行断言 |
| OAuth 错误/缺失/过期/重放state、双方隔离 | 后端与TestClient集成测试 |
| 账号新增/切换/删除、无Token泄露 | tests/test_integration.py |
| Token轮换与无轮换保留原refreshToken | 后端自动刷新回归 |
| 推荐配置不生成模型 | test_recommendations_order_only_actual_records |
| 告警不推测5H、转义、异常脱敏、重试与恢复 | tests/test_daemon.py |

## 浏览器截图

运行测试后保存在 `output/playwright/`（运行产物不入Git）：

- chatgpt.png
- google.png
- models-expanded.png
- dark-models-expanded.png
- abnormal-stale.png
- mobile-google.png
- mobile-models-expanded.png
- mobile-chatgpt.png
- report.json：浏览器断言结果

全部截图仅有 example.invalid 合成账号和明确 offline-fixture 来源。主智能体已打开桌面 ChatGPT 与手机模型展开截图检查排版。

## 兼容性与行为修正

原API路由和主要字段保留，账号与Token存储位置不变。以下属于有意的语义变更：未知百分比为null；不再生成虚拟模型；旧缓存不再作为可信额度；严格OAuth校验拒绝旧在途链接；状态使用available/exhausted/unavailable/stale/unknown。已有自定义客户端如果依赖旧的假100%或无state回调，需要调整。

新增后端 quota_parsing.py 与可选排序配置，旧目录部署必须同步配套模块；模板服务采用 /opt/ai-quota-monitor，现有生产unit不自动改变。详见 DATA_CONTRACT.md 与 CONFIGURATION.md。

## 未验证项

- 没有用真实账号调用 OpenAI、Google 内部接口，因此不能证明当前生产账号的模型列表、套餐或额度。
- OAuth成功与Token刷新用模拟上游响应验证，未完成真人在线授权。
- 未发送真实企业微信告警、未查询真实sub2api数据库。
- 未在Linux加载systemd模板、未改动Nginx、未重启或部署生产服务。
- 不能可靠取得ChatGPT网页模型独立额度，界面明确不可用，不按套餐生成百分比。

本地测试服务健康检查代表本次源码可启动，不代表生产接口或账号已在线验收。


另补回归：异常巨大 reset_after_seconds 不丢弃有效百分比；模型名称映射不覆盖上游、不生成模型；分源失败时只给历史子项标 stale。
