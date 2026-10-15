# 2026-10-15 生产部署记录

用户反馈公网仍显示旧的 codex-gpt-5 后，将已验证的前端、后端解析模块与排序配置部署到 sen3。告警 daemon 本次未重新部署，原 systemd unit、.env、账号及 Token 文件保留。

- 后端：/home/ubuntu/antigravity-quota-service/server.py 与 quota_parsing.py。
- 配置：/home/ubuntu/antigravity-quota-service/config/model_preferences.json。
- 页面：/var/www/sentools.sen666.com/quota/index.html。
- 备份：/home/ubuntu/quota-release-backups/20261015T123003Z，含原文件及发布清单；面板清单也已备份。
- antigravity-quota.service 已重启；/api/health 返回 status=ok、schema_version=2。
- 现有扁平目录部署增加优先读取自身 config/ 的兼容处理，55 项Python测试通过。

真实浏览器通过既有站点授权分别验证普通公网URL（没有缓存参数）与带版本参数URL，均为新版：

- Codex接口HTTP 200，真实返回2个共享窗口，stale=false，没有专属模型池，也没有虚拟 codex-gpt-5。
- 4个Google账号均HTTP 200，每个真实返回25个模型与2个额度组，stale=false。
- 桌面与390px手机端均生成截图，无JavaScript执行异常、无横向溢出。
- 本机到Cloudflare外部统计beacon资源连接被关闭，另有资源404控制台记录；配额与账号接口正常。未为统计资源修改Cloudflare全站配置。

生产截图与仅含状态统计的report.json保存在本地 output/playwright/production/，不提交含真实账号信息的截图或原始响应。未执行真人OAuth重新登录或企业微信发送。

服务器信息.md、服务器信息.html 与 sen3 面板projects.json已经同步项目变更记录。
