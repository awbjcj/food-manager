# Long-Running Python Bot Process

Food Manager v1 runs as one long-running Python process that owns Telegram
long-polling, the in-process scheduler, and the SQLite connection against a
mounted persistent volume. This keeps the single-user bot simple and avoids
splitting webhook handling, scheduled jobs, and persistence across separate
serverless components. Vercel/serverless deployment is intentionally out of
scope for v1; using it later would require a different shape, such as a
webhook endpoint plus an external scheduler or queue.
