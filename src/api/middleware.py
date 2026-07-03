from aiohttp import web


@web.middleware
async def cors_middleware(request, handler):
    # Responder preflight sin tocar el handler
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=_cors_headers(request))
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        exc.headers.update(_cors_headers(request))
        raise
    response.headers.update(_cors_headers(request))
    return response


def _cors_headers(request: web.Request) -> dict[str, str]:
    origin = request.headers.get("Origin", "*")
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Max-Age": "86400",
    }
