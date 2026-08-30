# 机器人运维

[English](operations.md) | [简体中文](operations.zh-CN.md)

系统使用一个长期运行的进程（ADR 0001）。在进程内，`app/resilience.py` 会以退避策略重启崩溃的轮询循环；启动补偿逻辑（`catch_up_missed_digests`）会补发停机期间错过的摘要。以下机制用于应对进程彻底终止（内存不足、未处理退出、主机重启）。必须只运行一个实例；同一个令牌上的两个轮询器会发生冲突。

## GitHub Actions 与 Railway

`.github/workflows/ci.yml` 会在拉取请求、推送到 `master` 以及每周定时任务中运行。它会检查 Ruff、Pyright、空数据库上的 Alembic 升级、完整测试套件、Python 和 Docker 构建，以及锁定依赖的安全审计。

该工作流仅负责 CI：Railway 直接连接 `master` 并负责每次部署。

保护 `master` 分支，并要求合并前通过 `Quality gates`、`Build artifacts` 和 `Dependency audit` 检查。这样可避免 Railway 自动部署尚未通过 CI 的合并版本。不要把 Railway、AI 提供商或 Telegram 的生产凭据放入 GitHub Actions；这些凭据应保存在 Railway 服务变量中。

如需回滚，请在 Railway 的 **Deployments** 页面恢复到此前成功的部署。这只回滚代码，Alembic 不会自动降级。`bin/run.py` 会在迁移前创建 SQLite 备份；需要回滚数据库结构时，应有意识地恢复对应备份。

生产服务必须设置 `HOSTED_FEATURES_ENABLED=true`。这是公开注册、共享家庭、额度/套餐界面和 Stars 订阅的部署边界。`OPEN_REGISTRATION` 和 `BILLING_ENABLED` 只有在该边界启用后才会生效。本地环境和 Compose 环境必须保持为 `false`。

## systemd（Linux）

`/etc/systemd/system/food-manager.service`：

    [Unit]
    Description=Food Manager Telegram bot
    After=network-online.target
    Wants=network-online.target

    [Service]
    WorkingDirectory=/opt/food-manager
    EnvironmentFile=/opt/food-manager/.env
    ExecStart=/usr/local/bin/uv run python bin/run.py
    Restart=always
    RestartSec=5

    [Install]
    WantedBy=multi-user.target

然后运行：`sudo systemctl enable --now food-manager`。

## Docker Compose

仓库中的 `compose.yaml` 是标准的本地容器配置。它会构建 Mini App 和机器人镜像、公开 8000 端口、在名为 `food-data` 的卷中持久化 `/data/food.db`，并使用镜像健康检查。

    docker compose up --build -d
    docker compose ps
    docker compose logs -f food-manager

使用 `docker compose down` 停止服务。除非明确要删除本地数据库，否则不要添加 `--volumes`。初始 `.env` 配置和直接运行镜像的命令请参阅 [`local-setup.zh-CN.md`](local-setup.zh-CN.md)。

## Windows（开发机）

最简单的进程守护方式是 PowerShell 循环：

    while ($true) { uv run python bin/run.py; Start-Sleep -Seconds 5 }

若需无人值守运行，请将指向该循环 `.ps1` 文件的命令注册为计划任务，并设置“启动时”触发和“失败时重新启动”。

## 发生故障时的预期行为

- 处理器出现未处理错误 → 所有者收到 `⚠️ handler_error: ...` 私信（同一事件每 5 分钟最多一次）。
- 摘要连续发送失败两次 → 所有者收到 `⚠️ digest_failed: ...`。
- 轮询崩溃 → 记录 `polling_crashed` 日志并发送 `⚠️ polling_crashed: ...` 私信，然后以 1 秒到 300 秒的退避间隔自动重启。
- 进程在夜间终止 → 重启后，若某个摘要的发送时间已经过去且 `User.last_digest_date` 尚未记录，系统会补发该摘要。

## 运营机器人

设置 `OPERATOR_BOT_TOKEN` 可在同一进程中启用第二个私有机器人。只有 `OPERATOR_TELEGRAM_IDS` 中的 ID 可以使用 `/whois`、`/grant`、`/refund`、`/ban`、`/unban`、`/revenue` 和 `/reconcile`；其他发送者不会收到回复。

两个机器人共用 SQLite 卷，因此保持在同一进程中。若要拆分为独立进程，必须先迁移到 PostgreSQL。

## v6.0 上线检查清单

1. 使用 `BILLING_ENABLED=false` 和 `OPEN_REGISTRATION=false` 部署。
2. 在收款前至少观察一个完整的 30 天用量周期，并根据 `quotausage` 数据重新校准 `app/billing/plans.py`。
3. 设置 `OPERATOR_BOT_TOKEN`；验证 `/whois`、`/grant`、`/ban` 和 `/reconcile`。
4. 设置 `BILLING_ENABLED=true`；验证 `/quota` 和一次真实加量包购买。确认账本记录、提升后的额度，以及 `/reconcile` 返回无异常。
5. 设置 `OPEN_REGISTRATION=true`；监控 `household_registered` 日志和警报。

如需关闭注册，将 `OPEN_REGISTRATION` 恢复为 `false`。现有家庭仍可继续使用，只有首次联系时的账户创建会停止。
