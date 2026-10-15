# 官方资料与接口边界

核对日期：2026-10-15。以下资料确认产品与结构边界，不用于制造账号模型列表或额度。

## OpenAI / Codex

- [官方使用说明](https://learn.chatgpt.com/docs/pricing)：实际限额应查账号用量；并非所有账号都有 5 小时窗口。
- [GPT-6 指南](https://developers.openai.com/api/docs/guides/latest-model)、[GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra)、[ChatGPT 模型说明](https://learn.chatgpt.com/docs/models)：核对名称，不能证明当前账号拥有专属模型配额。GPT-6、GPT-6 Sol、GPT-6 Astra、GPT-6.1 Sol、GPT-5.6 及未来 ID 仅在实际响应中出现时展示。
- [Sign in with ChatGPT](https://learn.chatgpt.com/docs/sign-in-with-chatgpt)：登录权限与使用套餐的权限不同，Codex Token 不被假定能访问全部网页接口。
- [官方 Codex usage 请求](https://github.com/openai/codex/blob/main/codex-rs/backend-client/src/client/rate_limit_resets.rs)：WHAM usage 是 Codex 客户端所用后端接口，不是各 ChatGPT 对话模型剩余次数的通用公开 API。
- [AdditionalRateLimitDetails](https://github.com/openai/codex/blob/main/codex-rs/codex-backend-openapi-models/src/models/additional_rate_limit_details.rs)：limit_name、metered_feature、rate_limit。
- [RateLimitWindowSnapshot](https://github.com/openai/codex/blob/main/codex-rs/codex-backend-openapi-models/src/models/rate_limit_window_snapshot.rs)：used_percent、limit_window_seconds、reset_after_seconds、reset_at。
- [RateLimitStatusPayload](https://github.com/openai/codex/blob/main/codex-rs/codex-backend-openapi-models/src/models/rate_limit_status_payload.rs)：共享池、独立池、Credits、工作区限制相互独立；工作区 Credits 耗尽不能推断为 5 小时耗尽。

### ChatGPT 网页额度调查

本次核对的公开官方资料未建立可由现有 Codex OAuth 可靠读取 ChatGPT Thinking、GPT-6 Pro 或各网页模型每日/每周剩余额度的接口协议。这是本次调查范围的结论，不宣称不存在内部接口。没有猜测或探测网页私有端点，也没有绕过授权、抓取浏览器 Cookie 或采用第三方额度服务。

因此界面显示“目前无法获取”，不生成百分比。未来需有合法授权、稳定协议和可靠响应才能增加适配器，不得把 Codex usage 改名当作网页额度。

## Google Antigravity

- [官方模型说明](https://www.antigravity.google/docs/models/)：列有 Gemini 4 Argon、Gemini 3.8 Flash、Gemini 3.6 Flash、Gemini 3.1 Pro、Claude 和 GPT-OSS 等；产品入口与套餐限制不同。
- [官方 /usage 命令](https://www.antigravity.google/docs/cli/commands/usage)：客户端可以查看模型资源用量。
- [官方计划说明](https://www.antigravity.google/docs/plans)：窗口和模型权限受套餐影响，不能据此推造账号额度。

retrieveUserQuotaSummary 和 fetchAvailableModels 是既有授权所用 Google 内部接口；本次未找到保证长期稳定且覆盖全部账号的公开 Schema，失败须明确返回不可用。

真实模型只来自 fetchAvailableModels.models。Gemini API ID 不一定等于 Antigravity 路由 ID，不能因为官方资料列出 Argon 就注入同名模型。配置排序仅影响顺序，不影响准入。

summary 组额度与 models 单模型额度各自保留来源，不互相复制。Low/extra-low 是路由属性，没有充分降级元数据时显示未知。

## 验证范围

本次使用合成脱敏响应进行离线验证，没有读取生产账号 Token、没有将含凭据真实响应提交仓库，也不声称真实账号在线配额查询已通过。
