# 本地安装

[English](local-setup.md) | [简体中文](local-setup.zh-CN.md)

机器人、调度器、运营机器人、Mini App API 和已构建的 Mini App 前端都在同一个进程中运行。一个 Telegram 机器人令牌只能启动一个进程，因为 Telegram 长轮询不支持同一令牌同时被两个消费者使用。

本地模式是私有的单用户版本。请保持 `HOSTED_FEATURES_ENABLED=false`（默认值）。该设置会禁用公开注册、家庭邀请和成员管理、额度、套餐销售、Telegram Stars 结账及订阅界面，避免在个人电脑上运行仅适用于托管部署的商业和多租户系统。

## 1. 创建 Telegram 机器人

1. 在 Telegram 中打开 `@BotFather` 并运行 `/newbot`。
2. 将机器人令牌复制到 `.env` 中的 `TELEGRAM_BOT_TOKEN`。
3. 从 `@userinfobot` 获取你的 Telegram 数字 ID，并填入 `ALLOWED_TELEGRAM_USER_ID`。
4. 配置一个支持图像的 AI 提供商：Anthropic、OpenAI 或 Gemini。DeepSeek 可以处理文本和搜索，但不能作为唯一提供商，因为收据导入需要图像输入。

编辑前，先将 `.env.example` 复制为 `.env`：

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```bash
# macOS 或 Linux
cp .env.example .env
```

切勿提交 `.env`，其中包含机器人和 AI 提供商凭据。

## 2. 以原生方式启动全部组件

要求：Python 3.12 或更高版本、`uv`，以及用于构建 Mini App 的 Node.js/npm。

```powershell
# Windows PowerShell，在仓库根目录运行
uv sync
Push-Location web
npm ci
npm run build
Pop-Location
uv run python bin/run.py
```

```bash
# macOS 或 Linux，在仓库根目录运行
uv sync
(cd web && npm ci && npm run build)
uv run python bin/run.py
```

启动时会备份现有 SQLite 数据库、应用 Alembic 迁移、在 `PORT`（默认 8000）启动 HTTP 服务器、注册摘要任务，并启动 Telegram 轮询。访问 <http://localhost:8000/healthz> 确认 HTTP 服务已就绪；响应应为 `{"ok": true}`。

如果只修改了 Python 代码，重启进程即可。修改 Mini App 后，请运行 `npm run build` 重新构建 `web/dist`，然后重启。Vite 的独立开发服务器适合界面开发（`cd web && npm run dev`），但在 Telegram 之外会使用模拟数据；集成构建由 Python 进程提供。

## 3. 使用 Docker Compose 启动全部组件

要求：Windows/macOS 使用 Docker Desktop；Linux 使用 Docker Engine 和 Compose 插件。

```text
docker compose up --build -d
docker compose ps
docker compose logs -f food-manager
```

Compose 服务会构建 React Mini App 和 Python 运行环境，公开 <http://localhost:8000>，并把 SQLite 数据保存在名为 `food-data` 的卷中。停止服务但保留数据：

```text
docker compose down
```

仅当你明确要删除本地数据库时，才使用 `docker compose down --volumes`。

## 4. 直接构建并运行镜像

```text
docker build -t food-manager:local .
docker volume create food-manager-data
docker run --name food-manager --env-file .env \
  -e DATABASE_PATH=/data/food.db -e PORT=8000 \
  -e HOSTED_FEATURES_ENABLED=false -e BILLING_ENABLED=false \
  -e OPEN_REGISTRATION=false \
  -p 8000:8000 -v food-manager-data:/data food-manager:local
```

在 PowerShell 中，可将 `docker run` 命令写在一行，或将每行末尾的 `\` 替换为 PowerShell 的反引号续行符。

## 5. 在本地启用 Telegram Mini App

健康检查端点和 API 可以在 localhost 上使用，但 Telegram 要求 Mini App 使用 HTTPS URL。若要测试真实的 Telegram 界面：

1. 在本地 8000 端口启动应用。
2. 通过 HTTPS 开发隧道公开该端口。
3. 将 `.env` 中的 `WEB_APP_URL` 设置为隧道的公共 HTTPS 根 URL。
4. 重启 food-manager。启动时，它会向 Telegram 注册**打开应用**菜单按钮。

应将隧道 URL 视为公开地址：除非确有需要，否则保持注册关闭，且不要分享该地址。Mini App API 请求会使用 Telegram 初始化数据进行身份验证；直接在普通浏览器中打开 URL 无法访问用户账户。

## 可选系统

- 设置 `OPERATOR_BOT_TOKEN`，可在同一进程中运行私有运营机器人。只有 `OPERATOR_TELEGRAM_IDS` 中的用户可以访问；默认情况下为引导用户。
- 当 `HOSTED_FEATURES_ENABLED=false` 时，`BILLING_ENABLED` 和 `OPEN_REGISTRATION` 会被忽略。在公共机器上启用托管功能前，请遵循 [`operations.zh-CN.md`](operations.zh-CN.md) 并配置运营控制。
- 设置 `SUB2API_BASE_URL` 和提供商对应的 `SUB2API_*_TOKEN`，可通过 Sub2API 使用已有的 AI 提供商订阅。仅在开发环境中允许回环 HTTP；其他网关 URL 必须使用 HTTPS。

## 故障排查

- **设置验证失败：**检查默认提供商是否配置了 API 密钥或 Sub2API 令牌，并确认至少有一个已配置的提供商能够处理图像。
- **Mini App 没有菜单按钮：**`WEB_APP_URL` 必须是 Telegram 可访问的公共 HTTPS 地址；修改后请重启。
- **端口 8000 被占用：**原生启动时可修改 `PORT`。使用 Compose 时，还需修改 `compose.yaml` 中 `8000:8000` 的主机端口。
- **Telegram 报告轮询冲突：**停止另一个使用相同机器人令牌的本地或已部署进程。
- **Docker 数据消失：**确认 `DATABASE_PATH` 为 `/data/food.db`，且 `/data` 由命名卷持久化。
