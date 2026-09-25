"""
admin.py — веб-админка каталога и публичная отдача /products.json.

Подключается к веб-серверу бота (aiohttp) одной строкой:  admin.setup(app)

Маршруты:
  GET  /products.json         — публичный: активные товары для сайта
  GET  /admin                 — страница админки (HTML)
  POST /admin/api/login       — вход по паролю (ADMIN_PASSWORD), ставит cookie
  POST /admin/api/logout
  GET  /admin/api/state       — каталог + счётчики + категории + журнал
  POST /admin/api/product     — добавить/изменить один товар
  POST /admin/api/bulk        — массовые действия (archive/restore/hide/show/price/category/purge)
  POST /admin/api/rollback    — откат последнего изменения

Авторизация: подписанная cookie (HMAC на SESSION_SECRET). Один администратор.
Если ADMIN_PASSWORD не задан — админка отключена (503), сайт всё равно работает.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os

from aiohttp import web

import catalog_store as store

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me")
COOKIE = "brsh_admin"
_HERE = os.path.dirname(os.path.abspath(__file__))


def _token() -> str:
    return hmac.new(SESSION_SECRET.encode(), b"berishmot-admin", hashlib.sha256).hexdigest()


def _authed(request: web.Request) -> bool:
    return request.cookies.get(COOKIE) == _token()


def _require(request: web.Request):
    if not ADMIN_PASSWORD:
        raise web.HTTPServiceUnavailable(text="admin disabled: set ADMIN_PASSWORD")
    if not _authed(request):
        raise web.HTTPUnauthorized(text="unauthorized")


# ---------------- публичный фид для сайта ----------------
async def _products_json(request: web.Request):
    data = {"products": store.public_products()}
    return web.json_response(
        data,
        headers={"Access-Control-Allow-Origin": "*",
                 "Cache-Control": "no-store"},
    )


# ---------------- страница админки ----------------
async def _admin_page(request: web.Request):
    path = os.path.join(_HERE, "admin.html")
    if os.path.isfile(path):
        return web.FileResponse(path)
    return web.Response(text="admin.html not found", status=404)


# ---------------- API ----------------
async def _login(request: web.Request):
    if not ADMIN_PASSWORD:
        return web.json_response({"ok": False, "error": "admin disabled"}, status=503)
    body = await request.json()
    if str(body.get("password", "")) != ADMIN_PASSWORD:
        return web.json_response({"ok": False, "error": "bad password"}, status=401)
    resp = web.json_response({"ok": True})
    resp.set_cookie(COOKIE, _token(), httponly=True, samesite="Lax", max_age=30 * 24 * 3600)
    return resp


async def _logout(request: web.Request):
    resp = web.json_response({"ok": True})
    resp.del_cookie(COOKIE)
    return resp


async def _state(request: web.Request):
    _require(request)
    status = request.query.get("status") or None
    if status == "all":
        status = None
    q = request.query.get("q", "")
    category = request.query.get("category", "")
    products = store.list_products(status=status, q=q, category=category)
    cats = sorted({p.get("category", "") for p in store.list_products(status=None) if p.get("category")})
    return web.json_response({
        "ok": True,
        "products": products,
        "counts": store.counts(),
        "categories": cats,
        "audit": store.audit_tail(30),
    })


async def _product(request: web.Request):
    _require(request)
    body = await request.json()
    try:
        saved = store.upsert(body)
    except ValueError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    return web.json_response({"ok": True, "product": saved})


async def _bulk(request: web.Request):
    _require(request)
    body = await request.json()
    action = body.get("action", "")
    ids = body.get("ids", [])
    value = body.get("value")
    allowed = {"archive", "restore", "hide", "show",
               "set_category", "set_price_pct", "purge"}
    if action not in allowed:
        return web.json_response({"ok": False, "error": "bad action"}, status=400)
    try:
        res = store.bulk(action, ids, value)
    except ValueError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    return web.json_response({"ok": True, **res})


async def _rollback(request: web.Request):
    _require(request)
    ok = store.rollback_last()
    return web.json_response({"ok": ok})


def setup(app: web.Application) -> None:
    app.router.add_get("/products.json", _products_json)
    app.router.add_get("/admin", _admin_page)
    app.router.add_post("/admin/api/login", _login)
    app.router.add_post("/admin/api/logout", _logout)
    app.router.add_get("/admin/api/state", _state)
    app.router.add_post("/admin/api/product", _product)
    app.router.add_post("/admin/api/bulk", _bulk)
    app.router.add_post("/admin/api/rollback", _rollback)
