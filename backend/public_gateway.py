"""Public candidate entry point. HR endpoints and local files are not exposed."""
from contextlib import asynccontextmanager
from pathlib import Path
import re

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

DIST = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
MAX_BODY = 16 * 1024 * 1024
PUBLIC_API = frozenset({
    ('GET', '/api/health'),
    ('GET', '/api/invitations/access'),
    ('GET', '/api/interview/state'),
    *[('POST', '/api/interview/' + name) for name in (
        'preflight', 'sessions', 'events', 'pause', 'resume', 'terminate', 'advance')],
})
LANDING = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Interview invitation</title></head>
<body style="font:16px system-ui;background:#f4f8fa;color:#173649;padding:48px 24px">
<main style="max-width:560px;margin:auto;background:white;padding:32px;border-radius:16px">
<h1>Interview invitations</h1><p>Open the personal interview link sent to your email.</p>
<p>If you need an invitation or an updated slot, contact your interview coordinator.</p></main></body></html>'''


class CandidateHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def headers(message):
            if message['type'] == 'http.response.start':
                message['headers'] = [*message.get('headers', []),
                    (b'referrer-policy', b'no-referrer'),
                    (b'x-content-type-options', b'nosniff'),
                    (b'cache-control', b'no-store'),
                    (b'permissions-policy', b'camera=(self), microphone=(self), display-capture=(self), fullscreen=(self)')]
            await send(message)
        await self.app(scope, receive, headers)


def make_gateway(dist=DIST, *, transport=None):
    dist = Path(dist)

    @asynccontextmanager
    async def lifespan(app):
        if not (dist / 'index.html').is_file():
            raise RuntimeError('Build the candidate UI with VITE_API_BASE_URL empty before starting the gateway.')
        async with httpx.AsyncClient(
            base_url='http://127.0.0.1:8000', transport=transport, trust_env=False,
            timeout=httpx.Timeout(115, connect=5), follow_redirects=False,
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=10),
        ) as client:
            app.state.api = client
            yield

    async def landing(request):
        return HTMLResponse(LANDING)

    async def interview(request):
        token = request.query_params.get('token', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{20,200}', token):
            return HTMLResponse(LANDING, status_code=400)
        return FileResponse(dist / 'index.html')

    async def proxy(request: Request):
        if (request.method, request.url.path) not in PUBLIC_API:
            return JSONResponse({'detail': 'This endpoint is not available on the candidate site.'}, status_code=404)
        try:
            if int(request.headers.get('content-length', '0')) > MAX_BODY:
                return JSONResponse({'detail': 'The upload is too large.'}, status_code=413)
        except ValueError:
            return JSONResponse({'detail': 'Invalid upload length.'}, status_code=400)
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY:
                return JSONResponse({'detail': 'The upload is too large.'}, status_code=413)
            chunks.append(chunk)
        try:
            upstream = await request.app.state.api.request(
                request.method, request.url.path, params=list(request.query_params.multi_items()),
                content=b''.join(chunks),
                headers={name: request.headers[name] for name in ('content-type', 'accept') if name in request.headers},
            )
            return Response(upstream.content, status_code=upstream.status_code,
                            headers={'content-type': upstream.headers.get('content-type', 'application/json')})
        except httpx.TimeoutException:
            return JSONResponse({'detail': 'The interviewer is taking longer than expected. Check your saved progress and retry shortly.'}, status_code=504)
        except httpx.RequestError:
            return JSONResponse({'detail': 'The interview server is temporarily unavailable. Please retry shortly.'}, status_code=503)

    app = Starlette(routes=[
        Route('/', landing), Route('/interview', interview),
        Route('/api/{path:path}', proxy, methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD']),
        Mount('/assets', StaticFiles(directory=dist / 'assets', check_dir=False)),
        Mount('/preflight', StaticFiles(directory=dist / 'preflight', check_dir=False)),
    ], lifespan=lifespan)
    app.add_middleware(CandidateHeaders)
    return app


app = make_gateway()
