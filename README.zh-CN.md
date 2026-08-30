# food-manager

[English](README.md) | [简体中文](README.zh-CN.md)

[![CI/CD](https://github.com/awbjcj/food-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/awbjcj/food-manager/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

一个 Telegram 机器人和 Mini App，用于跟踪家庭食品库、将购物小票转换为结构化条目、规划膳食，并在食物过期前提醒所有家庭成员。支持 Anthropic、OpenAI、Gemini 和 DeepSeek，共享家庭、Telegram Stars 订阅，以及英语、中文、法语和西班牙语。

## 试用托管机器人

在线服务地址：[@foodie_manager_bot](https://t.me/foodie_manager_bot)。无需运行服务器，即可体验小票扫描、家庭共享食品库、膳食计划、临期提醒和 Telegram Mini App。你可以先使用免费套餐；需要更多收据导入次数、AI 操作次数或家庭成员席位时，再邀请家庭成员或使用 Telegram Stars 升级。

托管 Mini App 运行在 [food-manager-production.up.railway.app](https://food-manager-production.up.railway.app)，但账户访问由 Telegram 验证。请通过机器人中的**打开应用**按钮进入。可参阅[托管服务指南](docs/hosted-service.zh-CN.md)，其中包含快速上手步骤和可直接分享的推广文案。

## 工作原理

1. **小票照片 → 食品库条目**：向机器人发送照片。配置的 LLM 会解析小票、提取食品及其预计保存期，并将其存入本地 SQLite 数据库。
2. **每日摘要**：每天早晨在你设置的时间收到一条消息，列出 7 天内将过期的所有食物，并可一键标记已食用、已丢弃、延后提醒，或移至冷藏/冷冻。
3. **交互式浏览食品库**：在每日定时任务之外，`/pantry` 可以打开同样的摘要、完整有效条目列表，或带有操作按钮的单个条目卡片。
4. **感知存储方式的保存期**：将条目移入冰箱或冷冻室时，会从存入日期重新计算保存期（单向：默认 → 冷藏 → 冷冻）；数据优先使用整理后的 USDA 表，并以网络搜索和缓存作为回退。
5. **保存期学习**：应用 `/correct` 提案后，该修正可以帮助系统处理未来导入的同名食品。
6. **手动添加**：对于没有购物小票的食品，可使用 `/add`。机器人会先展示解析后的提案，再写入数据库。
7. **根据食品库生成菜谱**：`/cook` 会优先使用即将过期的食材，并遵循你的饮食档案（`/prefs`）推荐菜谱；你可以收藏菜谱，并将缺少的食材加入购物清单。
8. **膳食计划与反馈**：`/plan 3` 至 `/plan 7` 可创建多日晚餐计划，`/calendar` 可导出计划；烹饪操作和 `/history` 会让食品库数量及膳食历史保持最新。
9. **自然语言对话**：无需记住所有命令，可以直接告诉机器人“买了牛奶和鸡蛋”“从购物清单中删除牛奶”“把酸奶的过期时间改到周五”或“三文鱼能保存多久？”
10. **共享家庭与群组**：使用 `/invite` 邀请成员共享食品库、购物清单、额度和计划；在托管模式中，`/bind` 可让同一家庭安全地在群聊中操作。
11. **Mini App 与计费**：Telegram Mini App 提供账户设置、额度查看、家庭套餐结账、加量包和 Telegram Stars 订阅管理。
12. **灵活的提供商付费方式**：运营者可以继续使用按量计费的 API 密钥，也可以在不重启机器人的情况下，把单个提供商切换到已有的 Sub2API 订阅。

## 前置要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Node.js 和 npm（仅原生构建 Mini App 时需要；Docker 已包含）
- 或者安装 Docker Desktop / Docker Engine 与 Compose
- Telegram 机器人令牌——通过 `@BotFather` 创建
- 至少一个能读取小票照片的 LLM 提供商 API 密钥：Anthropic、OpenAI 或 Gemini（DeepSeek 无法读取照片，不能作为唯一提供商）
- 你的 Telegram 用户 ID——可从 `@userinfobot` 获取

## 快速开始

```text
# 1. 将 .env.example 复制为 .env，并填写 Telegram 字段及一个
#    支持图像的提供商密钥。

# 2. 构建并启动完整服务栈。
docker compose up --build -d

# 3. 检查就绪状态并查看启动日志。
docker compose ps
docker compose logs -f food-manager
```

打开 <http://localhost:8000/healthz>，预期响应为 `{"ok": true}`。然后使用 `ALLOWED_TELEGRAM_USER_ID` 对应的 Telegram 账户向机器人发送 `/start`。命名 Docker 卷会在重启之间保留 SQLite 数据库。

本地模式有意设计为单用户且不启用计费。Compose 会强制设置 `HOSTED_FEATURES_ENABLED=false`、`OPEN_REGISTRATION=false` 和 `BILLING_ENABLED=false`，因此不会开放家庭邀请、公开注册、额度、套餐、结账或订阅界面。食品库跟踪、收据导入、提醒、菜谱、膳食计划和个人设置仍然可用。

有关 Windows、macOS 和 Linux 的原生安装方法、直接使用 `docker build` / `docker run`、Mini App HTTPS 配置和故障排查，请参阅 [`docs/local-setup.zh-CN.md`](docs/local-setup.zh-CN.md)。

### 行为说明

- `/correct <id> <自由文本>` 和 `/add <自由文本>` 使用配置的文本模型解析输入，并返回差异消息。点击**应用**提交，或点击**取消**放弃。提案在 10 分钟后失效。
- `/llm [anthropic|openai|gemini|deepseek]` 显示或修改每位用户的 LLM 提供商。DeepSeek 仍无法读取小票照片，因此图像请求会自动回退到有此能力的提供商，但 DeepSeek 已支持原生网络搜索。
- 对食品库条目的任何修改（标记为已食用、已丢弃、已移除、延后提醒、已修正，或移至冷藏/冷冻）都会在同一事务中使该条目的待处理修正失效。
- `/stats` 报告过去 30 天的收据准确率、修正次数、浪费情况、预计节省金额、烹饪执行情况和 LLM 成本。
- `/lang [en|zh|fr|es]` 设置语言；每位家庭成员都可以单独选择。底层数据始终以英语保存，并在渲染消息时翻译。

## 测试

```bash
# 运行全部测试
uv run pytest

# 运行与 CI 相同的静态检查
uv run ruff check app tests bin migrations
uv run pyright app bin

# 审计 uv.lock 中锁定的运行时依赖
uv export --no-dev --no-emit-project --no-hashes --output-file requirements-audit.txt
uv run pip-audit --requirement requirements-audit.txt --strict --progress-spinner off
rm requirements-audit.txt

# 运行单个测试文件
uv run pytest tests/test_core_services.py

# 按名称运行单个测试
uv run pytest tests/test_core_services.py::test_shelf_life_defaults
```

## 部署到 Railway

1. 在 Railway 中创建或导入项目，然后把服务源连接到 `awbjcj/food-manager` 仓库的 `master` 分支，并启用自动部署。
2. 添加名为 `food-data` 的持久卷，并挂载到 `/data`。
3. 设置与 `.env.example` 对应的 Railway 服务变量。
4. 推送或合并到 `master`。Railway 会自动构建并部署新版本；GitHub Actions 会独立运行 CI 质量门禁。

容器运行 `bin/run.py`：先备份数据库、执行 Alembic 迁移、注册每位用户的摘要定时任务，然后启动长轮询。

如需回滚，请在 Railway 服务的 **Deployments** 页面恢复到此前成功的部署。此操作有意不降级 SQLite 迁移；如果迁移本身也需要回退，请单独恢复数据库备份。

## 机器人命令

| 命令 | 说明 |
| --- | --- |
| 发送照片 | 解析购物小票并记录所有食品条目 |
| `/add 2 lb chicken, dozen eggs` | 无需小票，提议手动添加条目 |
| `/list` | 显示所有有效食品库条目 |
| `/list dairy` | 按类别筛选 |
| `/list week` | 显示 7 天内将过期的条目 |
| `/list expired` | 显示已经过期的条目 |
| `/pantry [digest\|<id>]` | 交互式食品库视图——摘要、完整列表或带操作按钮的单个条目卡片 |
| `/correct <id> <自由文本>` | 提议进行自然语言修正 |
| `/delete <id>` | 移除错误导入的条目（不会教给未来的导入） |
| `/digest_at 7` | 设置每日摘要时间（所在时区的 0–23 点） |
| `/tz America/New_York` | 设置时区 |
| `/lang [en\|zh\|fr\|es]` | 显示或设置语言 |
| `/stats` | 显示食品库统计信息 |
| `/llm [anthropic\|openai\|gemini\|deepseek]` | 显示或切换 LLM 提供商 |
| `/prefs [句子]` | 显示或更新家庭饮食档案 |
| `/cook` | 根据食品库中的食材生成菜谱 |
| `/history` | 显示家庭已经烹饪的餐食 |
| `/plan [3-7]` | 创建 3–7 天晚餐计划（默认 5 天） |
| `/calendar` | 将当前晚餐计划导出为 `.ics` 日历文件 |
| `/shopping` | 查看待购清单；购买后点击对应条目 |
| `/favorites` | 查看收藏菜谱；点击后根据当前食品库再次烹饪 |
| `/invite [family]` | 邀请一位成员加入家庭（使用 `family` 创建可重复使用的链接） |
| `/join <code>` | 加入邀请你的家庭 |
| `/bind` | 将当前托管群聊绑定到你的家庭 |
| `/household` | 列出家庭成员 |
| `/leave` | 离开家庭 |
| `/remove <id>` | （所有者）从家庭中移除成员 |
| `/quota` | 显示家庭共享的收据和 AI 操作用量 |
| `/buy` | 使用 Telegram Stars 购买家庭套餐或额度加量包 |
| `/billing` | 显示当前订阅状态 |
| `/help` | 显示所有命令 |

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | 是 | — | 从 `@BotFather` 获取的机器人令牌 |
| `ALLOWED_TELEGRAM_USER_ID` | 是 | — | 引导用户和默认运营者身份 |
| `OPEN_REGISTRATION` | 否 | `false` | 允许新的 Telegram 用户创建免费家庭；设为 false 时，现有成员和邀请仍可使用 |
| `HOSTED_FEATURES_ENABLED` | 否 | `false` | 启用仅供部署使用的多租户家庭、注册、额度/套餐界面和 Stars 计费；在本地运行时保持 false |
| `BILLING_ENABLED` | 否 | `false` | 强制执行额度并开放 Stars 结账；设为 false 时仍会记录用量 |
| `WEB_APP_URL` | 否 | — | Telegram Mini App 的公共 HTTPS 根 URL；设置后，启动时会安装机器人的**打开应用**菜单按钮 |
| `PORT` | 否 | `8000` | Mini App、API 和 `/healthz` 的 HTTP 端口 |
| `INGEST_PROVIDER` | 否 | 第一个已配置的提供商 | 固定用于收据导入的图像提供商，不受 `/llm` 影响；自动优先级为 Gemini、OpenAI、Anthropic |
| `OPERATOR_TELEGRAM_IDS` | 否 | `ALLOWED_TELEGRAM_USER_ID` | 以逗号分隔的运营者 Telegram ID |
| `OPERATOR_BOT_TOKEN` | 否 | — | 可选的独立运营机器人令牌 |
| `LLM_PROVIDER` | 否 | `anthropic` | 默认提供商：`anthropic`、`openai`、`gemini` 或 `deepseek`。每位用户可以通过 `/llm` 覆盖。`deepseek` 仅支持文本，因此还必须配置至少一个支持图像的提供商密钥 |
| `ANTHROPIC_API_KEY` | 使用 Anthropic 时 | — | Anthropic API 密钥 |
| `ANTHROPIC_MODEL` | 否 | `claude-sonnet-5` | 用于解析收据的 Claude 模型 |
| `ANTHROPIC_TEXT_MODEL` | 否 | `claude-haiku-4-5-20251001` | 用于 `/correct` 和 `/add` 提案的 Claude 模型 |
| `ANTHROPIC_SEARCH_MODEL` | 否 | `claude-sonnet-5` | 用于保存期网络搜索的 Claude 模型——**需要在 Anthropic 工作区启用网络搜索** |
| `OPENAI_API_KEY` | 使用 OpenAI 时 | — | OpenAI API 密钥 |
| `OPENAI_MODEL` | 否 | `gpt-5.6-terra` | 用于解析收据的 OpenAI 模型 |
| `OPENAI_TEXT_MODEL` | 否 | `gpt-5.6-luna` | 用于 `/correct` 和 `/add` 提案的 OpenAI 模型 |
| `GEMINI_API_KEY` | 使用 Gemini 时 | — | Google Gemini API 密钥（原生 `google-genai` SDK） |
| `GEMINI_MODEL` | 否 | `gemini-3.1-pro-preview` | 用于解析收据的 Gemini 模型 |
| `GEMINI_TEXT_MODEL` | 否 | `gemini-3.5-flash` | 用于 `/correct` 和 `/add` 提案的 Gemini 模型 |
| `DEEPSEEK_API_KEY` | 使用 DeepSeek 时 | — | DeepSeek API 密钥。DeepSeek 仍无法读取小票照片，但现在支持原生网络搜索 |
| `DEEPSEEK_MODEL` | 否 | `deepseek-v4-flash` | 用于文本和搜索任务的 DeepSeek 模型 |
| `DEEPSEEK_BASE_URL` | 否 | `https://api.deepseek.com` | DeepSeek API 根 URL（兼容 OpenAI） |
| `SUB2API_BASE_URL` | 否 | — | 共享 Sub2API 网关根地址；除 `ENV=dev` 中的回环 HTTP 外，必须使用 HTTPS |
| `SUB2API_ANTHROPIC_TOKEN` | 否 | — | Anthropic 订阅路由令牌；设置后 Anthropic 默认使用订阅模式 |
| `SUB2API_OPENAI_TOKEN` | 否 | — | OpenAI 订阅路由令牌 |
| `SUB2API_GEMINI_TOKEN` | 否 | — | Gemini 订阅路由令牌 |
| `SUB2API_DEEPSEEK_TOKEN` | 否 | — | DeepSeek 订阅路由令牌 |
| `SPOONACULAR_API_KEY` | 否 | — | 可选的 Spoonacular 密钥；未设置时，`/cook` 和 `/plan` 使用 TheMealDB 及配置的 LLM 菜谱源 |
| `COOK_COST_CEILING_MICROS` | 否 | `100000` | 每次 `/cook` 的 LLM 支出上限，单位为微美元（0.10 美元）；如果菜谱结果为空可提高 |
| `PLAN_COST_CEILING_MICROS` | 否 | `150000` | 每次 `/plan` 的 LLM 支出上限，单位为微美元（0.15 美元）；如果周计划结果为空可提高 |
| `DATABASE_PATH` | 否 | `./food.db` | SQLite 数据库文件路径 |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 |
| `ENV` | 否 | `dev` | 设置为 `prod` 时使用 JSON 结构化日志 |

Stars 订阅每 30 天续费一次。家庭所有者可以在 Mini App 中取消续费；加量包会在当前额度周期结束时失效。完整的购买、验证、加量和取消流程请参阅 [`docs/telegram-subscriptions.zh-CN.md`](docs/telegram-subscriptions.zh-CN.md)。

## 项目文档

- [`docs/adr/`](docs/adr)——架构决策记录；ADR 0001 提供[简体中文版](docs/adr/0001-long-running-python-bot-process.zh-CN.md)
- [`docs/operations.zh-CN.md`](docs/operations.zh-CN.md)——在生产环境运行和守护机器人
- [`docs/local-setup.zh-CN.md`](docs/local-setup.zh-CN.md)——Windows、macOS 和 Linux 的原生及 Docker 安装指南
- [`docs/hosted-service.zh-CN.md`](docs/hosted-service.zh-CN.md)——在线机器人上手指南和推广文案
- [`docs/telegram-subscriptions.zh-CN.md`](docs/telegram-subscriptions.zh-CN.md)——Stars 订阅与加量包用户指南
- [`docs/superpowers/`](docs/superpowers)——按顺序保存每个已发布版本的英文规格和计划
- [`CONTEXT.zh-CN.md`](CONTEXT.zh-CN.md)——代码库使用的领域术语表（统一语言）
- [`CLAUDE.md`](CLAUDE.md) / [`AGENTS.md`](AGENTS.md)——供本仓库 AI 编码代理使用的英文架构与约定参考

## 参与贡献

这是一个由个人维护的项目，在构建和文档编写过程中大量使用 AI 编码代理。欢迎提交 Issue 和拉取请求；如果修改不只是小修复，请先创建 Issue 讨论。提交 PR 前请运行 `uv run pytest`、`uv run ruff check app tests bin migrations` 和 `uv run pyright app bin`；CI 会运行相同检查。

## 许可证

[MIT](LICENSE)
