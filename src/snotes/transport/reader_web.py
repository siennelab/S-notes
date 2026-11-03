"""Small server-rendered, read-only Reader over S's already-filtered mirror."""
from __future__ import annotations

from html import escape
from pathlib import Path
import secrets
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from src.snotes.application.reader_service import ReaderService
from src.snotes.transport.reader_auth import (
    Auth0OIDC, LoginError, ORIGIN, ReaderLogin, ReaderSettings,
    SESSION_COOKIE, SESSION_TTL, TRANSACTION_COOKIE, TRANSACTION_TTL,
)
from src.snotes.transport.reader_render import render_markdown, render_redacted


SECURITY_HEADERS = {
    "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff", "X-Robots-Tag": "noindex",
    "Content-Security-Policy": "default-src 'self'; script-src 'none'; img-src 'self'; "
        "style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}


class ReaderBoundary:
    """One security-header and error boundary, including 404/405 and failures."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def safe_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message["headers"] = [(key, value) for key, value in message["headers"]
                                      if key.decode("latin-1").lower() not in {name.lower() for name in SECURITY_HEADERS}]
                message["headers"].extend((key.lower().encode(), value.encode()) for key, value in SECURITY_HEADERS.items())
            await send(message)

        try:
            await self.app(scope, receive, safe_send)
        except Exception:
            if not started:
                await PlainTextResponse("暂时无法读取，请稍后再试。", status_code=503)(scope, receive, safe_send)


def _page(content: str, *, csrf: str = "", title: str = "S-notes") -> HTMLResponse:
    logout = ('<form method="post" action="/logout"><input type="hidden" name="csrf" value="'
              + escape(csrf, quote=True) + '"><button type="submit">退出</button></form>') if csrf else ""
    return HTMLResponse('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{escape(title)}</title><link rel="stylesheet" href="/reader.css"></head>'
        f'<body><header><a href="/">S-notes</a><nav>{logout}</nav></header><main>{content}</main></body></html>')


def _search_form(csrf: str, query: str = "") -> str:
    return ('<form method="post" action="/search" role="search"><label for="query">搜索笔记</label>'
        f'<input type="hidden" name="csrf" value="{escape(csrf, quote=True)}">'
        f'<input id="query" name="q" type="search" maxlength="512" value="{escape(query, quote=True)}" required>'
        '<button type="submit">搜索</button></form>')


def _notes_list(notes: list[dict]) -> str:
    if not notes:
        return '<p class="empty">暂无笔记。</p>'
    return '<ul class="note-list">' + "".join(
        f'<li><a href="/note/{note["note_id"]}">{escape(note["title"] or "无标题")}</a>'
        f'<time>{escape(note["updated_at"])}</time></li>' for note in notes) + '</ul>'


async def _form(request: Request) -> dict[str, list[str]]:
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
        raise ValueError("invalid_form")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise ValueError("form_too_large")
    return parse_qs(body.decode("utf-8"), max_num_fields=4, keep_blank_values=True)


def create_reader_app(
    settings: ReaderSettings, service: ReaderService, *,
    login: ReaderLogin | None = None, oidc: Auth0OIDC | None = None,
):
    login = login or ReaderLogin(settings)
    oidc = oidc or Auth0OIDC(settings)

    def current(request):
        return login.session(request.cookies.get(SESSION_COOKIE, ""))

    async def post_form(request, session):
        # Referrer-Policy:no-referrer makes ordinary form POSTs send Origin:null
        # (Fetch's origin-header algorithm). The session-bound CSRF token remains
        # mandatory; an opaque/missing Origin is never itself authorization.
        if request.headers.get("origin") not in {None, "null", ORIGIN}:
            raise ValueError("invalid_origin")
        if request.headers.get("sec-fetch-site") not in {None, "same-origin", "none"}:
            raise ValueError("invalid_origin")
        form = await _form(request)
        values = form.get("csrf", [])
        if len(values) != 1 or not secrets.compare_digest(values[0], session.csrf):
            raise ValueError("invalid_csrf")
        return form

    async def home(request):
        session = current(request)
        if session is None:
            return _page('<h1>S-notes</h1><p>登录后阅读你的笔记。</p><a class="button" href="/login">登录</a>')
        snapshot, directories, recent = service.home()
        content = ('<h1>你的笔记</h1><p class="snapshot">内容快照生成于：'
            + escape(snapshot["generated_at"]) + '</p>' + _search_form(session.csrf))
        content += '<h2>目录</h2><ul class="directory-list">' + "".join(
            f'<li><a href="/directory/{item["directory_id"]}">{escape(" / ".join(item["path"]))}</a></li>'
            for item in directories) + '</ul><h2>最近更新</h2>' + _notes_list(recent)
        return _page(content, csrf=session.csrf)

    async def search(request):
        session = current(request)
        if session is None:
            return RedirectResponse("/", status_code=303)
        try:
            form = await post_form(request, session)
            query_values = form.get("q", [])
            if len(query_values) != 1 or len(query_values[0]) > 512:
                raise ValueError("invalid_query")
            query = query_values[0].strip()
        except (ValueError, UnicodeError):
            return PlainTextResponse("请求无效。", status_code=400)
        notes = service.search(query)
        return _page('<h1>搜索结果</h1>' + _search_form(session.csrf, query) + _notes_list(notes), csrf=session.csrf)

    async def directory(request):
        session = current(request)
        if session is None:
            return RedirectResponse("/", status_code=303)
        result = service.directory(request.path_params["directory_id"])
        if result is None:
            return PlainTextResponse("Not Found", status_code=404)
        item, notes = result
        return _page('<h1>' + escape(" / ".join(item["path"])) + '</h1>' + _notes_list(notes), csrf=session.csrf)

    async def note(request):
        session = current(request)
        if session is None:
            return RedirectResponse("/", status_code=303)
        item = service.note(request.path_params["note_id"])
        if item is None:
            return PlainTextResponse("Not Found", status_code=404)
        if item["visibility"] == "full" and item["safe_markdown"] is not None:
            body = '<article class="article">' + render_markdown(item["safe_markdown"]) + '</article>'
        else:
            body = '<article class="redacted">' + render_redacted(item["safe_text"]) + '</article>'
        content = ('<h1>' + escape(item["title"] or "无标题") + '</h1><p class="updated">更新于：'
            + escape(item["updated_at"]) + '</p><ul class="tags">'
            + "".join('<li>' + escape(tag) + '</li>' for tag in item["tags"]) + '</ul>' + body)
        return _page(content, csrf=session.csrf, title=item["title"] or "S-notes")

    async def begin_login(request):
        try:
            location, binding = login.begin()
        except LoginError:
            return PlainTextResponse("请稍后再登录。", status_code=429)
        response = RedirectResponse(location, status_code=303)
        response.set_cookie(TRANSACTION_COOKIE, binding, max_age=TRANSACTION_TTL, secure=True,
                            httponly=True, samesite="lax", path="/")
        return response

    async def callback(request):
        try:
            params = request.query_params
            if len(params.getlist("state")) != 1:
                raise LoginError("login_failed")
            transaction = login.consume(params["state"], request.cookies.get(TRANSACTION_COOKIE, ""))
            if params.get("error") or len(params.getlist("code")) != 1 or len(params["code"]) > 4096:
                raise LoginError("login_failed")
            await oidc.complete(params["code"], transaction)
            # Rotate any previously held local session on successful login.
            login.sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
            session_id = login.establish()
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(SESSION_COOKIE, session_id, max_age=SESSION_TTL, secure=True,
                                httponly=True, samesite="lax", path="/")
        except (LoginError, ValueError, KeyError):
            response = _page('<h1>无法登录</h1><p>请使用获准账号重新登录。</p><a href="/login">重新登录</a>')
            response.status_code = 403
        response.delete_cookie(TRANSACTION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return response

    async def logout(request):
        session = current(request)
        if session is not None:
            try:
                await post_form(request, session)
            except (ValueError, UnicodeError):
                return PlainTextResponse("请求无效。", status_code=400)
            login.sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return response

    async def css(request):
        return Response(Path(__file__).with_name("reader.css").read_bytes(), media_type="text/css")

    async def health(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[
        Route("/", home), Route("/search", search, methods=["POST"]),
        Route("/directory/{directory_id:int}", directory), Route("/note/{note_id:int}", note),
        Route("/login", begin_login), Route("/auth/callback", callback),
        Route("/logout", logout, methods=["POST"]), Route("/healthz", health), Route("/reader.css", css),
    ])
    return ReaderBoundary(TrustedHostMiddleware(app, allowed_hosts=["notes.ke-den.de"], www_redirect=False))
