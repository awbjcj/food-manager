# Subscribe through Telegram

Food Manager uses Telegram Stars for the Family plan and one-time quota
top-ups. Billing is pooled by household, so a purchase changes the limits for
everyone in that household.

Open the live bot: [@foodie_manager_bot](https://t.me/foodie_manager_bot).
Subscriptions are offered by the hosted service; localhost installations are
single-user and do not display billing or subscription features.

## Subscribe in the chat

1. Open [@foodie_manager_bot](https://t.me/foodie_manager_bot) and send `/start`.
2. Send `/quota` to see the household's current receipt and AI-action usage.
3. Send `/buy`.
4. Tap **Family plan - 500 Stars**.
5. Review Telegram's invoice and confirm the recurring Stars payment.
6. Wait for the bot's confirmation, then send `/billing` to verify that the
   Family plan is active.

The Family plan renews every 30 days and includes 100 receipt imports, 300 AI
actions, and up to 10 household members per period. A household can have only
one active Family subscription.

## Subscribe in the Mini App

If the bot has an **Open app** menu button:

1. Open it from the bot chat.
2. Select **Plans**.
3. Choose **Upgrade to Family**.
4. Confirm the Telegram Stars invoice.

The account home screen shows the current plan and remaining quota after the
payment completes.

## Buy a top-up

Run `/buy` or open **Plans**, then choose either:

- **+50 receipts** for 250 Stars.
- **+150 AI actions** for 250 Stars.

Top-ups are one-time purchases, not subscriptions. Their unused quota expires
at the end of the household's current 30-day quota period.

## Manage or cancel renewal

Only the household owner can cancel renewal in Food Manager:

1. Open the Mini App.
2. Select **Plans**, then **Manage plan**.
3. Select **Cancel renewal**.

Cancellation stops the next recurring charge. The household keeps Family
benefits until the displayed period end. Use `/billing` to check status.

## If checkout is unavailable

- The deployment owner must set `BILLING_ENABLED=true`; otherwise `/buy` and
  Mini App checkout deliberately remain disabled.
- Complete checkout inside an official Telegram client with enough Stars.
- You must still be an active member of the household when Telegram validates
  the invoice.
- If another member already activated the Family plan, use `/billing` instead
  of purchasing it again.
- Ask the bot operator for help if Telegram accepts payment but the bot does
  not confirm it. Provide the time and Telegram transaction details, but never
  send bot tokens or provider API keys.
