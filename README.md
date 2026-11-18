# status-rss

订阅 Codex / OpenAI 模型 / Claude / DeepSeek / Kimi，以及 Google Play 结算与登录、Apple App Store 与 iCloud 的官方 Status RSS。

| 服务 | Feed | 来源 |
| --- | --- | --- |
| Claude | https://status.claude.com/history.rss | 官方 RSS，直接订阅 |
| DeepSeek | https://status.deepseek.com/history.rss | 官方 RSS，直接订阅 |
| Codex | `<BASE_URL>/codex.xml` | 由 https://status.openai.com/feed.rss 过滤出与 Codex 模型相关的事件 |
| OpenAI 模型 | `<BASE_URL>/openai.xml` | 同一 feed 中与模型相关（ChatGPT 对话、模型 API、生图等）且不含 Codex 的事件 |
| Kimi | `<BASE_URL>/kimi.xml` | Moonshot 关闭了 RSS，由 https://status.moonshot.cn/api/v2/incidents.json 生成 |
| Google | `<BASE_URL>/google.xml` | 由三个 Google 状态面板的 `incidents.json` 按产品过滤生成，见下 |
| Apple | `<BASE_URL>/apple.xml` | Apple 没有 feed，由两个 System Status 页面背后的 JSON 按服务过滤生成，见下 |

Google 和 Apple 只保留支付 / 结算 / 登录相关的服务（在 `build_feeds.py` 的 `GOOGLE_DASHBOARDS`、`APPLE_PAGES` 里调整）：

| 来源 | 保留的产品 / 服务 |
| --- | --- |
| [Google Play](https://status.play.google.com) | Play Billing Purchases、Play Developer Subscriptions and In-App Purchases API、Play Subscriptions Renewal and Management、Play Voided Purchases API |
| [Firebase](https://status.firebase.google.com) | Authentication |
| [Google Workspace](https://www.google.com/appsstatus/dashboard) | Google Sign In |
| [Apple System Status](https://www.apple.com/support/systemstatus/) | App Store、Subscription Purchase、Apple Account、Sign in with Apple、所有 iCloud 服务 |
| [Apple Developer System Status](https://developer.apple.com/system-status/) | 所有 `App Store - *` 服务（In-App Purchases、Receipt Verification、Server APIs、Server Notifications、Sandbox 等） |

## 使用

```sh
python3 build_feeds.py --out public --base-url https://your.host/status/
```

只依赖 Python 3 标准库。输出 `public/codex.xml`、`public/openai.xml`、`public/kimi.xml`、`public/google.xml`、`public/apple.xml` 和包含全部 feed 的 `public/feeds.opml`（导入阅读器即可）。某个上游抓取失败时只跳过对应的 feed，其余照常生成，脚本以非零状态退出。

生成的 feed 需要托管并定时刷新：

- **GitHub Pages**：推送到 GitHub，在 Settings → Pages 里把 Source 设为 “GitHub Actions”。`worker/` 里的 Cloudflare Worker 每 5 分钟触发一次 `.github/workflows/feeds.yml`；workflow 自带的每 15 分钟定时只是兜底。
- **自建服务器**：用 cron 定时运行脚本，输出到 Web 目录，例如
  `*/10 * * * * cd /path/to/status-rss && python3 build_feeds.py --out /var/www/status --base-url https://your.host/status/`

## 飞书机器人通知

`notify.py` 在每次构建时对比各个 feed 与上次运行保存的状态（存在 GitHub Actions cache 里），只挑与模型及模型 API 相关的事件（登录、计费、连接器、搜索、Work mode、Dots 等不推送），以及 `google.xml`、`apple.xml` 里的全部事件，把新增或有更新的事件以卡片形式通过飞书应用机器人发出（已解决为绿色、监控中为蓝色、其余为红色）。默认私聊推送给一个人，也可以发到机器人所在的群。

1. 飞书开放平台的应用需开启「机器人」能力，并开通 `im:message:send_as_bot` 权限；接收人需在应用的可用范围内。
2. 在仓库 Settings → Secrets and variables → Actions 添加：
   - `FEISHU_APP_ID`、`FEISHU_APP_SECRET`：应用凭证
   - `FEISHU_RECEIVE_ID`：接收人邮箱，或 open_id（`ou_...`，私聊），或群 chat_id（`oc_...`）

首次运行只记录现有事件、不推送；只推送 3 天内的事件，单次最多 20 条。发送失败的事件会在下次运行时重试。某个来源暂时读不到时保留它上次的状态，恢复后不会重复推送。

## 每 5 分钟触发（Cloudflare Worker）

GitHub 的定时任务经常延迟很久，所以由 `worker/` 的 Cron Trigger 每 5 分钟调用一次 workflow_dispatch。

1. 创建 fine-grained token：仅授权本仓库，权限只给 **Actions: Read and write**。
2. 部署：
   ```sh
   cd worker
   npx wrangler login
   npx wrangler secret put GH_TOKEN   # 粘贴上一步的 token
   npx wrangler deploy
   ```
