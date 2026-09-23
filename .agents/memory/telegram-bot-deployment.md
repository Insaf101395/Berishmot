---
name: Telegram bot deployment
description: Production deployment mode required for continuous Telegram polling.
---

Telegram polling must run on a continuously running VM deployment, not an autoscaling request-driven deployment. Keep development Preview polling disabled to avoid a second polling consumer.

**Why:** Request-driven deployments can scale to zero when there are no web requests, which stops `getUpdates` polling and makes the bot appear offline.

**How to apply:** After changing the deployment target to VM, the user must Republish before the live deployment uses the new mode.