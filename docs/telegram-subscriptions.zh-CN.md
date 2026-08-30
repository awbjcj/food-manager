# 通过 Telegram 订阅

[English](telegram-subscriptions.md) | [简体中文](telegram-subscriptions.zh-CN.md)

Food Manager 使用 Telegram Stars 支付家庭套餐和一次性额度加量包。账单额度由家庭共享，因此购买后会更新该家庭所有成员的使用上限。

打开在线机器人：[@foodie_manager_bot](https://t.me/foodie_manager_bot)。订阅仅由托管服务提供；本地安装为单用户模式，不显示计费或订阅功能。

## 在聊天中订阅

1. 打开 [@foodie_manager_bot](https://t.me/foodie_manager_bot) 并发送 `/start`。
2. 发送 `/quota` 查看家庭当前的收据和 AI 操作用量。
3. 发送 `/buy`。
4. 点击**家庭套餐 - 500 Stars**。
5. 检查 Telegram 账单并确认 Stars 周期性付款。
6. 等待机器人确认，然后发送 `/billing` 验证家庭套餐是否已生效。

家庭套餐每 30 天续费一次，每个周期包含 100 次收据导入、300 次 AI 操作，并支持最多 10 位家庭成员。一个家庭只能有一个生效中的家庭订阅。

## 在 Mini App 中订阅

如果机器人菜单中有**打开应用**按钮：

1. 从机器人聊天中打开 Mini App。
2. 选择**套餐**。
3. 选择**升级到家庭套餐**。
4. 确认 Telegram Stars 账单。

付款完成后，账户首页会显示当前套餐和剩余额度。

## 购买加量包

运行 `/buy` 或打开**套餐**，然后选择：

- **+50 次收据导入**，价格为 250 Stars。
- **+150 次 AI 操作**，价格为 250 Stars。

加量包是一次性购买，不会自动续订。未使用的额度会在家庭当前 30 天额度周期结束时失效。

## 管理或取消续费

只有家庭所有者可以在 Food Manager 中取消续费：

1. 打开 Mini App。
2. 选择**套餐**，然后选择**管理套餐**。
3. 选择**取消续费**。

取消后不会再产生下一笔周期性扣款。家庭仍可在页面显示的周期结束时间前继续使用家庭套餐权益。可使用 `/billing` 查看状态。

## 无法结账时

- 部署所有者必须设置 `BILLING_ENABLED=true`；否则 `/buy` 和 Mini App 结账功能会按设计保持禁用。
- 请在拥有足够 Stars 的官方 Telegram 客户端内完成结账。
- Telegram 验证账单时，你仍须是该家庭的有效成员。
- 如果其他成员已经启用家庭套餐，请使用 `/billing` 查看，而不要重复购买。
- 如果 Telegram 已接受付款但机器人未确认，请联系机器人运营者，并提供付款时间和 Telegram 交易详情；切勿发送机器人令牌或 AI 提供商 API 密钥。
