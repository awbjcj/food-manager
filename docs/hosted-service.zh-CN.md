# Food Manager 托管服务

[English](hosted-service.md) | [简体中文](hosted-service.zh-CN.md)

## 立即开始

在 Telegram 中打开 [@foodie_manager_bot](https://t.me/foodie_manager_bot)，然后点击**开始**。无需安装。

Food Manager 能把购物小票转换成家庭共享的智能食品库，在食物过期前提醒家庭成员，并根据已有食材生成菜谱和多日膳食计划。支持英语、中文、法语和西班牙语。

## 为什么使用托管机器人？

- 拍摄购物小票，无需逐项手动录入。
- 每日接收临期提醒，并可一键标记已食用、已丢弃、延后提醒、冷藏或冷冻。
- 与家庭成员共享食品库、购物清单、饮食偏好和膳食计划。
- 使用自然语言操作，例如“买了牛奶和鸡蛋”“吃掉了酸奶”或“三文鱼能保存多久？”
- 生成优先消耗临期食材的菜谱和 3–7 天晚餐计划。
- 跟踪修正记录、浪费率、预计节省金额和已烹饪餐食。
- 在支持的 AI 任务中选择 Anthropic、OpenAI、Gemini 或 DeepSeek。
- 在 Telegram Mini App 中管理设置、用量、套餐和订阅续费。

## 快速上手

1. 打开 [@foodie_manager_bot](https://t.me/foodie_manager_bot) 并发送 `/start`。
2. 发送一张清晰的购物小票照片，或输入 `买了牛奶和两个牛油果`。
3. 使用 `/pantry` 查看食材，并使用 `/digest_at 8` 设置提醒时间。
4. 使用 `/prefs` 描述饮食需求，然后运行 `/cook` 或 `/plan 5`。
5. 使用 `/invite` 邀请一位家庭成员，或使用 `/invite family` 创建可重复使用的家庭邀请链接。
6. 使用 `/quota` 查看共享额度；如需更多家庭成员席位、收据导入次数或 AI 操作次数，可使用 `/buy` 购买家庭套餐或一次性加量包。

Mini App 托管于 [food-manager-production.up.railway.app](https://food-manager-production.up.railway.app)。请从机器人菜单中的**打开应用**按钮进入，以便 Telegram 安全验证你的账户。

## 可直接分享的推广文案

> 少浪费食物，更轻松地决定吃什么。Food Manager 能把 Telegram 中的购物小票照片转换成家庭共享食品库，发送临期提醒，并利用已有食材生成菜谱和每周晚餐计划。免费试用：https://t.me/foodie_manager_bot

简短版：

> 扫描小票，跟踪保质期，规划晚餐，减少浪费。在 Telegram 中试用 Food Manager：https://t.me/foodie_manager_bot

## 套餐

托管机器人提供免费额度。家庭套餐每 30 天以 500 Telegram Stars 自动续费，每个周期包含 100 次收据导入、300 次 AI 操作，并支持最多 10 位家庭成员。也可以购买一次性的收据或 AI 操作加量包。有关购买和取消流程，请参阅[订阅指南](telegram-subscriptions.zh-CN.md)。

## 服务状态

托管应用运行在 Railway 上。公共健康检查端点为 <https://food-manager-production.up.railway.app/healthz>。
