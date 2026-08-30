# Food Manager hosted service

[English](hosted-service.md) | [简体中文](hosted-service.zh-CN.md)

## Start now

Open [@foodie_manager_bot](https://t.me/foodie_manager_bot) in Telegram and tap
**Start**. No installation is required.

Food Manager turns grocery receipts into a shared smart pantry, reminds your
household before food expires, and helps turn what you already own into recipes
and multi-day meal plans. It works in English, Chinese, French, and Spanish.

## Why use the hosted bot?

- Photograph a grocery receipt instead of entering every item manually.
- Get daily expiry reminders with one-tap eaten, tossed, snooze, fridge, and
  freezer actions.
- Share one pantry, shopping list, preferences, and meal plan with household
  members.
- Ask naturally: “bought milk and eggs,” “ate the yogurt,” or “how long does
  salmon keep?”
- Generate recipes and 3–7 day dinner plans that prioritize food before it
  expires.
- Track corrections, waste rate, estimated savings, and cooked meals.
- Choose Anthropic, OpenAI, Gemini, or DeepSeek for supported AI tasks.
- Manage settings, usage, plans, and subscription renewal in the Telegram Mini
  App.

## Quick onboarding

1. Open [@foodie_manager_bot](https://t.me/foodie_manager_bot) and send
   `/start`.
2. Send a clear photo of a grocery receipt, or type `bought milk and two
   avocados`.
3. Use `/pantry` to review items and `/digest_at 8` to choose a reminder hour.
4. Use `/prefs` to describe dietary needs, then `/cook` or `/plan 5`.
5. Use `/invite` to add one household member, or `/invite family` for a reusable
   household link.
6. Use `/quota` to see the shared allowance and `/buy` if the household needs
   the Family plan or a one-time top-up.

The Mini App is hosted at
[food-manager-production.up.railway.app](https://food-manager-production.up.railway.app).
Open it from the bot's **Open app** menu button so Telegram can authenticate
your account securely.

## Share-ready promotional copy

> Waste less food and decide what to cook with less effort. Food Manager turns
> Telegram receipt photos into a shared household pantry, sends expiry
> reminders, and creates recipes and weekly dinner plans from groceries you
> already have. Try it free: https://t.me/foodie_manager_bot

Short version:

> Scan receipts. Track expiry dates. Plan dinner. Waste less. Try Food Manager
> in Telegram: https://t.me/foodie_manager_bot

## Plans

The hosted bot includes a free allowance. The Family plan renews every 30 days
for 500 Telegram Stars and includes 100 receipt imports, 300 AI actions, and up
to 10 members per household. One-time receipt and AI-action top-ups are also
available. See [the subscription guide](telegram-subscriptions.md) for the exact
purchase and cancellation flow.

## Service status

The hosted application runs on Railway. Its public health endpoint is
<https://food-manager-production.up.railway.app/healthz>.
