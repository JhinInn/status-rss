# status-rss

订阅 Codex / Claude / DeepSeek / Kimi 四家官方 Status 的 RSS。

| 服务 | Feed | 来源 |
| --- | --- | --- |
| Claude | https://status.claude.com/history.rss | 官方 RSS，直接订阅 |
| DeepSeek | https://status.deepseek.com/history.rss | 官方 RSS，直接订阅 |
| Codex | `<BASE_URL>/codex.xml` | 由 https://status.openai.com/feed.rss 过滤出含 “Codex” 的事件 |
| Kimi | `<BASE_URL>/kimi.xml` | Moonshot 关闭了 RSS，由 https://status.moonshot.cn/api/v2/incidents.json 生成 |

## 使用

```sh
python3 build_feeds.py --out public --base-url https://your.host/status/
```

只依赖 Python 3 标准库。输出 `public/codex.xml`、`public/kimi.xml` 和包含全部四个 feed 的 `public/feeds.opml`（导入阅读器即可）。

生成的两个 feed 需要托管并定时刷新：

- **GitHub Pages**：推送到 GitHub，在 Settings → Pages 里把 Source 设为 “GitHub Actions”，`.github/workflows/feeds.yml` 会每 15 分钟重建一次。
- **自建服务器**：用 cron 定时运行脚本，输出到 Web 目录，例如
  `*/10 * * * * cd /path/to/status-rss && python3 build_feeds.py --out /var/www/status --base-url https://your.host/status/`

## 飞书机器人通知

`notify.py` 在每次构建时对比四个 feed 与上次部署的 `state.json`，把新增或有更新的事件以卡片形式推送到飞书群自定义机器人（已解决为绿色、监控中为蓝色、其余为红色）。

1. 飞书群 → 设置 → 群机器人 → 添加「自定义机器人」，复制 Webhook 地址；如开启「签名校验」，同时复制密钥。
2. 在仓库 Settings → Secrets and variables → Actions 添加 `FEISHU_WEBHOOK`（以及可选的 `FEISHU_SECRET`）。

首次运行只记录现有事件、不推送；只推送 3 天内的事件，单次最多 20 条。发送失败的事件会在下次运行时重试。
