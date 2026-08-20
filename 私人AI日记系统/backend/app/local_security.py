from __future__ import annotations

from urllib.parse import urlsplit
from pathlib import PurePosixPath

from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles


ALLOWED_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1", "testserver"})
INLINE_RASTER_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"})


class SecureAttachmentFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
        if PurePosixPath(path).suffix.lower() not in INLINE_RASTER_SUFFIXES:
            response.headers["Content-Disposition"] = "attachment"
        return response


def _header(scope: dict, name: str) -> str:
    wanted = name.lower().encode("latin-1")
    for key, value in scope.get("headers", ()):
        if key.lower() == wanted:
            return value.decode("latin-1", errors="replace").strip()
    return ""


def _authority(value: str) -> tuple[str, int | None] | None:
    clean = value.strip()
    if not clean:
        return None
    try:
        parsed = urlsplit(f"//{clean}")
        if parsed.path not in {"", "/"} or parsed.username or parsed.password:
            return None
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if not hostname:
            return None
        return hostname, parsed.port
    except ValueError:
        return None


def _origin_matches_host(origin: str, host: tuple[str, int | None]) -> bool:
    clean = origin.strip()
    if not clean:
        return True
    if clean.lower() == "null":
        return False
    try:
        parsed = urlsplit(clean)
        if parsed.scheme not in {"http", "https"} or parsed.path not in {"", "/"}:
            return False
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return False
        origin_host = (parsed.hostname or "").lower().rstrip(".")
        return (origin_host, parsed.port) == host
    except ValueError:
        return False


def _reject(message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=403)


class LocalControlMiddleware:
    """Keep the localhost control plane out of cross-site browser requests."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        host = _authority(_header(scope, "host"))
        origin = _header(scope, "origin")
        fetch_site = _header(scope, "sec-fetch-site").lower()
        allowed = bool(host and host[0] in ALLOWED_HOSTNAMES and _origin_matches_host(origin, host))
        if fetch_site in {"cross-site", "cross-origin"}:
            allowed = False
        if allowed:
            async def send_with_security_headers(message):
                if message.get("type") == "http.response.start":
                    headers = list(message.get("headers", ()))
                    headers.extend((
                        (b"x-frame-options", b"DENY"),
                        (b"content-security-policy", b"frame-ancestors 'none'"),
                        (b"referrer-policy", b"no-referrer"),
                    ))
                    message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, send_with_security_headers)
            return

        if scope.get("type") == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "本地控制接口拒绝了非本机来源"})
            return
        response = _reject("Mio 的本地控制接口只接受本机来源。")
        await response(scope, receive, send)
