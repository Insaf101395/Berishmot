---
name: Autoscale local files
description: Published Autoscale instances do not retain runtime-written files across restarts or republishing.
---

Files written to an Autoscale deployment's local filesystem are ephemeral. Do not promise that runtime-generated catalog media or feeds will remain accessible after scale-to-zero, restart, or republishing.

**Why:** Replit's deployment documentation states that Autoscale files reset across these events; external catalog consumers need stable URLs over time.

**How to apply:** When implementing file-backed media for external imports, confirm the deployment target and use persistent storage for a durable production solution. A requested local-file implementation can be tested locally, but disclose the production limitation before publishing.