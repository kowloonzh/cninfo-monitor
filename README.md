# cninfo-monitor

每天查询巨潮资讯新发布的正式财务报告，按“报告年度 + 报告类型 + 证券代码”
去重，并通过企业微信自建应用发送纯文本通知。

默认配置监测半年度报告，报告年度直接从公告标题识别。统计口径参考：
[巨潮资讯 2026 年中报发布公司检索过程](https://kowloonzh.github.io/imgse/2026/08/05/cninfo-2026-interim-report-search-process.md)。

## 工作方式

- 每次以北京时间当天为截止日，滚动查询最近一个自然月，自动遍历巨潮分页，
  减少收录延迟导致的遗漏。
- 标题必须包含指定年度的正式报告名称，兼容沪市常见的公司全称前缀；
  同时排除摘要、英文版、更正、修订和更新稿。
- 同一公司、同一年度、同一报告类型只通知一次。
- 首次运行默认静默建立已有公司基线，避免历史公告刷屏。
- 没有新增报告时发送“今日无新增财报”心跳，确认定时任务正常运行。
- 企业微信发送失败时不确认这些新报告，下一次定时任务会再次推送。
- 状态文件使用原子替换写入；每日脚本使用 `flock` 防止任务重叠。

支持的 `report_types`：

| 配置值 | 报告 |
|---|---|
| `annual` | 年度报告 |
| `first_quarter` | 第一季度报告 |
| `interim` | 半年度报告 |
| `third_quarter` | 第三季度报告 |

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
  report_types:
    - interim
  notify_when_no_updates: true
```

查询日期不需要配置。例如在 2026-08-05 运行，查询区间自动计算为
2026-07-05～2026-08-05；在 2026-03-31 运行，则查询
2026-02-28～2026-03-31。每次查询结果会与本地状态比较，只通知新增公司。

`notify_when_no_updates` 为 `true` 时，没有新增也会发送：

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

状态保存在 `data/state.json`。不要随意删除它，否则程序会把下一次运行视为首次运行。
状态、日志、`.env` 和实际配置均已加入 `.gitignore`，不会上传到远程仓库。

## 每日定时任务

包装脚本每天生成 `logs/monitor-YYYY-MM-DD.log`：

```bash
scripts/run_daily.sh
```

确认企业微信测试成功并完成首次运行后，安装每天 19:00 执行的 cron：

```bash
scripts/install_cron.sh
```

自定义时间，例如每天 08:30：

```bash
CNINFO_CRON_SCHEDULE='30 8 * * *' scripts/install_cron.sh
```

可用 `crontab -l` 检查已安装任务。触发时刻使用 cron 守护进程的本地时区；
脚本内部的查询日期和日志时间固定使用 `Asia/Shanghai`。

## 开发验证

```bash
.venv/bin/pytest -q
bash -n scripts/run_daily.sh scripts/install_cron.sh
```
