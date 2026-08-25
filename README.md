# cninfo-monitor

每小时查询巨潮资讯新发布的沪深京及港股正式财务报告，按“市场 + 报告年度 +
报告类型 + 证券代码”去重，并通过企业微信自建应用发送纯文本通知。

默认配置监测所有正式财务报告，报告年度直接从公告标题识别。中报统计口径参考：
[巨潮资讯 2026 年中报发布公司检索过程](https://kowloonzh.github.io/imgse/2026/08/05/cninfo-2026-interim-report-search-process.md)。

## 工作方式

- SQLite 保存每个市场的精确成功游标，程序每小时查询游标涉及的自然日至当前
  时间。巨潮部分公告的时间戳只有日期精度（统一为 `00:00:00`），因此不能仅按
  小时边界过滤；程序会重扫相关日期并按公告 ID 去重，避免晚间入库公告漏报。
- 候选公告先写入本地 SQLite，再在本地按标题识别财报；公告 ID 唯一去重，
  已完整扫描的日期再次查询时，遇到一整页已缓存 ID 就停止翻页。修改识别规则
  不需要重新按关键词请求巨潮。
- A 股按深市、沪市、北交所及已配置的年报、一季报、中报、三季报官方分类分别
  分页，避免同期非财报公告挤占巨潮最多 100 页的结果窗口；港股仍抓取全部公告
  后本地识别。若接口返回重复页或分类分区仍超过上限，立即停止并告警。
- 标题必须包含指定年度的正式报告名称，兼容沪市常见的公司全称前缀；
  同时排除摘要、英文版、更正、修订和更新稿。
- 港股兼容“中期报告”“中期业绩”“截至……止六个月业绩公布”以及
  “6月份季度业绩”等标题，
  排除业绩预告、快报、盈利预警、会议通知、演示材料和单独股息公告。
- 推送前通过腾讯行情接口批量补充总市值；A 股显示亿元人民币，港股及港币
  计价 B 股显示亿港元。默认过滤 A 股低于 100 亿元、港股低于 500 亿港元的
  公司；行情暂不可用时显示“暂无数据”并保留该公司，避免行情故障造成漏报。
- 新发现但低于市值门槛的财报进入 SQLite 待观察队列，未来 7 天每小时重新检查；
  期间达到门槛后正常推送，通知成功才移出队列。启用此逻辑之前已经过滤的历史
  报告不会自动补发。
- 推送前从本地快照匹配指数归属，支持沪深300、中证500、中证1000、
  中证2000、恒生科技、科创50、科创创业50和机器人产业；一家公司可同时
  属于多个指数，没有命中时不显示“指数”一行。
- 同一公司、同一年度、同一报告类型只通知一次。
- 首次运行默认静默建立已有公司基线，避免历史公告刷屏。
- 新启用一个市场时会为该市场静默建立基线，不会把最近一个月的历史报告刷屏。
- 每小时仅在发现新增财报时推送；当天没有其他成功通知时，21 点后发送一次
  “今日无新增财报”健康心跳。
- 企业微信发送失败时不确认这些新报告，下一次定时任务会再次推送。
- 某个市场或日期分页抓取失败时不越过失败位置；两个市场独立保存进度，并发送
  故障告警，下一小时自动续跑。
- 通知状态使用原子替换写入，公告与扫描游标保存在 SQLite；运行脚本使用
  `flock` 防止任务重叠。

支持的 `report_types`：

| 配置值 | 报告 |
|---|---|
| `annual` | 年度报告 |
| `first_quarter` | 第一季度报告 |
| `interim` | 半年度报告 |
| `third_quarter` | 第三季度报告 |
| `quarterly` | 港股通用季度业绩（例如“6月份季度业绩”） |

## 环境要求

- Python 3.10+
- Linux（定时脚本使用 `cron` 和 `flock`）

## 安装

```bash
git clone git@github.com:kowloonzh/cninfo-monitor.git
cd cninfo-monitor
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp config/config.example.yaml config/config.yaml
```

实际配置文件 `config/config.yaml` 不会提交到 Git。复制模板后，选择需要监测的报告类型：

```yaml
monitor:
  markets:
    - mainland
    - hong_kong
  report_types:
    - annual
    - first_quarter
    - interim
    - third_quarter
    - quarterly
  minimum_market_cap_yi:
    mainland: 100
    hong_kong: 500
  cache_path: "../data/announcements.db"
  initial_lookback_hours: 48
  overlap_seconds: 300
  heartbeat_hour: 21
  notify_when_no_updates: true
```

`markets` 支持：

| 配置值 | 市场 |
|---|---|
| `mainland` | 沪深京市场 |
| `hong_kong` | 港股市场（巨潮港股频道） |

需要同时监控 A 股和港股时保留上述两个配置值；也可以只配置其中一个市场。

`minimum_market_cap_yi` 是各市场推送公司的最低总市值，单位为“亿”。默认 A 股
为 100 亿元、港股为 500 亿港元；恰好达到门槛的公司仍会推送。将对应市场设置为
`0` 可关闭该市场的市值过滤。港币计价 B 股仍属于 A 股市场，使用 A 股门槛；行情
接口没有返回市值时不会过滤该公司。

首次运行默认回看最近 48 小时，之后从 SQLite 保存的市场游标继续抓取。
`overlap_seconds` 默认是 300 秒，用于确定需要重扫的日期，结果通过公告 ID 自动
去重。程序按自然日保存完整扫描快照；当天后续运行通常只需检查顶部少量分页。
SQLite 另存公告的首次发现时间，日期精度公告也能作为本小时新增数据处理。只有
财报通知成功后才推进处理游标。

`notify_when_no_updates` 为 `true` 时，当天没有其他通知则在 `heartbeat_hour`
指定小时之后发送一次：

```text
今日无新增财报
```

如不需要心跳，可将它设置为 `false`。

## 配置企业微信

在企业微信管理后台创建自建应用，取得企业 ID、应用 Secret 和 AgentId。复制环境变量模板：

```bash
cp .env.example .env
chmod 600 .env
```

填写 `.env`：

```dotenv
WORKWECHAT_CORP_ID=ww_your_corp_id
WORKWECHAT_CORP_SECRET=your_app_secret
WORKWECHAT_AGENT_ID=1000002
```

然后将 `config/config.yaml` 中的通知开关改为：

```yaml
notifications:
  workwechat:
    enabled: true
```

接收人配置是可选的：

- `user_ids`：企业微信用户 ID；
- `department_ids`：部门 ID；
- `tag_ids`：标签 ID。

三项都为空时发送给应用可见范围内的 `@all`。

先验证企业微信配置：

```bash
.venv/bin/cninfo-monitor test-notification
```

## 手动运行

只查询和预览，不写状态、不推送：

```bash
.venv/bin/cninfo-monitor run --dry-run
```

首次正式运行默认建立静默基线：

```bash
.venv/bin/cninfo-monitor run
```

如果希望第一次就推送查询区间内所有已发布公司：

```bash
.venv/bin/cninfo-monitor run --notify-existing
```

通知状态保存在 `data/state.json`，原始公告和时间游标保存在
`data/announcements.db`。不要随意删除它们，否则程序会重新建立基线或回看抓取。
状态、日志、`.env` 和实际配置均已加入 `.gitignore`，不会上传到远程仓库。

## 指数归属缓存

程序运行时只读取本地的“证券代码 → 指数名称”快照，不会在每次财报查询时
请求指数网站。仓库内附带一份已核验的初始快照；如果存在
`data/index_memberships.json`，则优先使用该运行时快照，文件不存在或损坏时
自动回退到仓库内快照，不影响财报推送。

刷新缓存：

```bash
.venv/bin/cninfo-monitor refresh-indexes
```

数据均来自指数公司官方来源：6 个中证指数使用中证指数公司成份券文件，
机器人产业使用国证指数官网 980022 样本接口，恒生科技使用恒生指数公司
成份股文件，并与官方实时前十成份交叉检查。刷新过程中会严格校验每个指数的
预期样本数；任一来源不完整或口径不一致时不会替换旧缓存。

这些指数通常按季度或半年调整，建议在指数调整生效后手动刷新，或另设每周一次
的低频任务；每小时财报任务无需刷新指数缓存。

## 每小时定时任务

包装脚本每天生成 `logs/monitor-YYYY-MM-DD.log`：

```bash
scripts/run_daily.sh
```

确认企业微信测试成功并完成首次运行后，安装每个整点执行一次的 cron：

```bash
scripts/install_cron.sh
```

如需自定义频率，例如每半小时执行一次：

```bash
CNINFO_CRON_SCHEDULE='*/30 * * * *' scripts/install_cron.sh
```

可用 `crontab -l` 检查已安装任务。触发时刻使用 cron 守护进程的本地时区；
脚本内部的查询时间、游标和日志时间固定使用 `Asia/Shanghai`。

## 开发验证

```bash
.venv/bin/pytest -q
bash -n scripts/run_daily.sh scripts/install_cron.sh
```
