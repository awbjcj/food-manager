# 长期运行的 Python 机器人进程

[English](0001-long-running-python-bot-process.md) | [简体中文](0001-long-running-python-bot-process.zh-CN.md)

Food Manager v1 以一个长期运行的 Python 进程运行。该进程负责 Telegram 长轮询、进程内调度器，以及连接挂载持久卷中 SQLite 数据库的连接。这个设计使单用户机器人保持简单，避免把 Webhook 处理、定时任务和持久化拆分到多个无服务器组件中。

v1 明确不支持 Vercel 或其他无服务器部署。若未来需要支持，必须采用不同的架构形态，例如 Webhook 端点配合外部调度器或队列。
