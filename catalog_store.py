"""
catalog_store.py — надёжное хранилище каталога САЙТА (отдельно от VK/Pinterest).

Заложено под массовое удаление/редактуру без потери данных:
- мягкое удаление (status=archived), с восстановлением;
- журнал изменений (audit log, jsonl) с откатом последнего действия;
- атомарная запись + валидация: битые данные не перезапишут рабочие;
- автобэкапы каждой версии (последние BACKUPS_KEEP);
- потокобезопасность (единый Lock на запись).

Данные лежат в STORAGE_DIR/site/ :
  products.json      — весь каталог (активные + архив), список объектов
  backups/*.json     — снимки перед каждым изменением
  audit.jsonl        — журнал действий
"""

from __future__ import annotations

import json
import os
import re
import time
import threading
from datetime import datetime, timezone
from uuid import uuid4

STORAGE_DIR = os.getenv("STORAGE_DIR", "storage_data")
_BASE = os.path.join(STORAGE_DIR, "site")
_PRODUCTS = os.path.join(_BASE, "products.json")
_BACKUPS = os.path.join(_BASE, "backups")
_AUDIT = os.path.join(_BASE, "audit.jsonl")
BACKUPS_KEEP = 50

_lock = threading.RLock()

ALLOWED_STATUS = {"active", "hidden", "archived"}
# поля, которые можно менять массово/по одному
EDITABLE = {"title", "category", "price", "oldPrice", "sizes",
            "images", "badge", "stock", "description", "status"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_dirs() -> None:
    os.makedirs(_BASE, exist_ok=True)
    os.makedirs(_BACKUPS, exist_ok=True)


def _atomic_write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _read_all() -> list[dict]:
    if not os.path.isfile(_PRODUCTS):
        return []
    try:
        with open(_PRODUCTS, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # рабочий файл битый — не падаем, отдаём последний бэкап
        return _read_latest_backup()
    if isinstance(data, dict):
        data = data.get("products", [])
    return data if isinstance(data, list) else []


def _read_latest_backup() -> list[dict]:
    if not os.path.isdir(_BACKUPS):
        return []
    files = sorted(os.listdir(_BACKUPS), reverse=True)
    for name in files:
        try:
            with open(os.path.join(_BACKUPS, name), encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else data.get("products", [])
        except (json.JSONDecodeError, OSError):
            continue
    return []


def _backup(products: list[dict]) -> None:
    _ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:6]
    _atomic_write(os.path.join(_BACKUPS, f"{stamp}.json"),
                  json.dumps(products, ensure_ascii=False))
    files = sorted(os.listdir(_BACKUPS), reverse=True)
    for old in files[BACKUPS_KEEP:]:
        try:
            os.remove(os.path.join(_BACKUPS, old))
        except OSError:
            pass


def _validate(products: list[dict]) -> None:
    if not isinstance(products, list):
        raise ValueError("catalog must be a list")
    ids = set()
    for p in products:
        if not isinstance(p, dict) or not p.get("id"):
            raise ValueError("each product needs an id")
        if p["id"] in ids:
            raise ValueError(f"duplicate id: {p['id']}")
        ids.add(p["id"])
        if p.get("status") and p["status"] not in ALLOWED_STATUS:
            raise ValueError(f"bad status: {p['status']}")


def _save(products: list[dict], action: str, detail: dict | None = None) -> None:
    """Валидирует, делает бэкап предыдущего состояния, атомарно пишет, логирует."""
    _validate(products)
    _ensure_dirs()
    prev = _read_all()
    _backup(prev)  # снимок ДО изменения — для отката
    _atomic_write(_PRODUCTS, json.dumps(products, ensure_ascii=False, indent=2))
    _audit(action, detail or {})


def _audit(action: str, detail: dict) -> None:
    _ensure_dirs()
    rec = {"at": _now(), "action": action, **detail}
    with open(_AUDIT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _slugify(title: str) -> str:
    s = (title or "").lower()
    s = re.sub(r"[^a-z0-9а-я]+", "-", s).strip("-")
    return s or uuid4().hex[:8]


# ======================= ПУБЛИЧНОЕ API =======================

def list_products(status: str | None = "active", q: str = "",
                  category: str = "") -> list[dict]:
    """Список товаров с фильтрами. status=None → все."""
    with _lock:
        items = _read_all()
    q = (q or "").lower().strip()
    out = []
    for p in items:
        st = p.get("status", "active")
        if status and st != status:
            continue
        if category and p.get("category") != category:
            continue
        if q:
            hay = f"{p.get('title','')} {p.get('category','')} {p.get('id','')}".lower()
            if q not in hay:
                continue
        out.append(p)
    return out


def public_products() -> list[dict]:
    """Только активные — то, что показывает сайт."""
    return list_products(status="active")


def counts() -> dict:
    with _lock:
        items = _read_all()
    c = {"active": 0, "hidden": 0, "archived": 0, "total": len(items)}
    for p in items:
        c[p.get("status", "active")] = c.get(p.get("status", "active"), 0) + 1
    return c


def upsert(product: dict) -> dict:
    """Добавить или обновить один товар."""
    with _lock:
        items = _read_all()
        pid = str(product.get("id") or "").strip()
        now = _now()
        if not pid:
            pid = f"P{datetime.now():%Y%m%d%H%M%S}{uuid4().hex[:4]}"
            product["id"] = pid
        product.setdefault("slug", _slugify(product.get("title", "")))
        product.setdefault("status", "active")
        product.setdefault("created_at", now)
        product["updated_at"] = now
        found = False
        for i, p in enumerate(items):
            if p.get("id") == pid:
                merged = {**p, **{k: v for k, v in product.items() if k in EDITABLE
                                  or k in ("id", "slug", "created_at", "updated_at")}}
                items[i] = merged
                found = True
                break
        if not found:
            items.append(product)
        _save(items, "upsert", {"id": pid, "new": not found})
        return product


def bulk(action: str, ids: list[str], value=None) -> dict:
    """
    Массовые действия над списком id:
      archive  — мягкое удаление (в архив)
      restore  — вернуть из архива в active
      hide/show
      set_category (value=str)
      set_price_pct (value=число %, напр. 10 или -15 к текущей цене)
      purge    — удалить НАВСЕГДА (только из архива), чистит фото-сироты отдельно
    Возвращает {"changed": n}.
    """
    ids = [str(x) for x in (ids or [])]
    if not ids:
        return {"changed": 0}
    with _lock:
        items = _read_all()
        idset = set(ids)
        changed = 0
        remaining = []
        for p in items:
            if p.get("id") not in idset:
                remaining.append(p)
                continue
            if action == "purge":
                if p.get("status") == "archived":
                    changed += 1          # выбрасываем из списка = удалено навсегда
                    continue
                remaining.append(p)       # purge разрешён только из архива
                continue
            if action == "archive":
                p["status"] = "archived"
            elif action == "restore":
                p["status"] = "active"
            elif action == "hide":
                p["status"] = "hidden"
            elif action == "show":
                p["status"] = "active"
            elif action == "set_category" and value:
                p["category"] = str(value)
            elif action == "set_price_pct" and value is not None:
                try:
                    p["price"] = max(0, round(int(p.get("price", 0)) * (1 + float(value) / 100)))
                except (TypeError, ValueError):
                    pass
            else:
                remaining.append(p)
                continue
            p["updated_at"] = _now()
            changed += 1
            remaining.append(p)
        _save(remaining, f"bulk:{action}", {"ids": ids, "value": value, "changed": changed})
        return {"changed": changed}


def rollback_last() -> bool:
    """Откатывает каталог к последнему бэкапу (перед последним изменением)."""
    with _lock:
        if not os.path.isdir(_BACKUPS):
            return False
        files = sorted(os.listdir(_BACKUPS), reverse=True)
        if not files:
            return False
        latest = os.path.join(_BACKUPS, files[0])
        try:
            with open(latest, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return False
        products = data if isinstance(data, list) else data.get("products", [])
        # бэкапим текущее (чтобы можно было «отменить откат») и пишем прошлое
        _backup(_read_all())
        _atomic_write(_PRODUCTS, json.dumps(products, ensure_ascii=False, indent=2))
        _audit("rollback", {"restored_from": files[0]})
        return True


def audit_tail(limit: int = 50) -> list[dict]:
    if not os.path.isfile(_AUDIT):
        return []
    with open(_AUDIT, encoding="utf-8") as f:
        lines = f.readlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))
