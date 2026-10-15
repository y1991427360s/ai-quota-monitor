# 模型排序配置

`config/model_preferences.json` 只控制展示顺序，不创建模型、不限制模型准入。

- `recommended_model_ids`：按优先级填写真实路由 ID，必须完全匹配。未配置的真实模型仍会全部展示。
- `provider_order`：Provider 排序，未识别的新 Provider 仍保留。
- `display_names`：原始 ID 到展示名的可维护映射，仅在实际返回该模型且未给展示名时使用；上游展示名优先，未知 ID 原样显示。映射不会授予权限或注入模型。

名称和元数据从接口读取；未知后缀保留在原始 ID 中，不与已有型号合并。不要根据公开 Gemini API 模型清单推造 Antigravity 路由。运行旧后端目录布局时，将 config 放到后端源码目录内（优先）或上级 config/；缺失配置会使用安全默认排序，额度不受影响。

`.env.example` 提供 Google OAuth 和企业微信参数。OpenAI OAuth 的官方客户端配置沿用现有实现；生产 Token 仅保存于运行用户家目录。CHECK_INTERVAL_SECONDS 为告警轮询秒数，WX_AGENT_ID 是推送应用 ID，后台与守护进程共用相同配置。

