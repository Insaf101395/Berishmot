---
name: Publish health probes
description: Keep Autoscale startup health independent of optional remote storage checks.
---

The root HTTP route used for startup health should verify that the process is serving, without reading App Storage. Storage-dependent operations should still fail explicitly when storage is unavailable.

**Why:** An Autoscale publish built successfully but failed promotion because the root route queried a missing default bucket and returned a non-200 response to the startup probe. Making the probe independent fixes promotion, but does not mean the storage-backed features are usable.

**How to apply:** If adding data to the root route or changing startup logic, avoid calls to external storage in the health path. Verify both the root status and storage-backed endpoints separately before claiming the app is fully functional.