import os

from config.settings import DEFAULT_ALLOWED_ORIGIN

def _cors_headers() -> dict[str, str]:
    allowed_origin = os.getenv("ALLOWED_ORIGIN", DEFAULT_ALLOWED_ORIGIN)
    return {
        "Access-Control-Allow-Origin": allowed_origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
