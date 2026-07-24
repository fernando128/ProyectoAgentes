import json
from datetime import datetime
from datetime import timezone
from inspect import isawaitable

from azurefunctions.extensions.http.fastapi import Request, Response

from utils.cors import _cors_headers

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _json_response(status_code: int, payload: dict) -> Response:
    return Response(
        status_code=status_code,
        headers={**_cors_headers(), "Content-Type": "application/json"},
        content=json.dumps(payload, ensure_ascii=False),
    )

def _get_request_base_url(req: Request) -> str:
    forwarded_proto = _get_header(req, "x-forwarded-proto")
    forwarded_host = _get_header(req, "x-forwarded-host")
    host = forwarded_host or _get_header(req, "host")

    if host:
        proto = forwarded_proto or "http"
        return f"{proto}://{host}".rstrip("/")

    url = getattr(req, "url", None)
    scheme = getattr(url, "scheme", "http")
    netloc = getattr(url, "netloc", "")

    if netloc:
        return f"{scheme}://{netloc}".rstrip("/")

    return "http://localhost:7071"

def _get_obj_value(obj, name: str):
    if isinstance(obj, dict):
        return obj.get(name)

    return getattr(obj, name, None)

def _find_first_value(value, field_name: str, depth: int = 0):
    if value is None or depth > 8:
        return None

    if isinstance(value, dict):
        if value.get(field_name):
            return value[field_name]

        for child in value.values():
            found = _find_first_value(child, field_name, depth + 1)

            if found:
                return found

        return None

    if isinstance(value, (list, tuple)):
        for child in value:
            found = _find_first_value(child, field_name, depth + 1)

            if found:
                return found

        return None

    direct_value = getattr(value, field_name, None)

    if direct_value:
        return direct_value

    model_dump = getattr(value, "model_dump", None)

    if callable(model_dump):
        return _find_first_value(model_dump(), field_name, depth + 1)

    value_dict = getattr(value, "__dict__", None)

    if isinstance(value_dict, dict):
        return _find_first_value(value_dict, field_name, depth + 1)

    return None

def _get_route_param(req: Request, name: str) -> str:
    for attr_name in ("path_params", "route_params"):
        params = getattr(req, attr_name, None)
        if params and params.get(name):
            return str(params[name]).strip()

    return ""

async def _maybe_await(value):
    if isawaitable(value):
        return await value

    return value

def _get_header(req: Request, name: str) -> str:
    headers = getattr(req, "headers", None) or {}

    for key in (name, name.lower(), name.upper()):
        value = headers.get(key) if hasattr(headers, "get") else None

        if value:
            return str(value)

    return ""

async def _request_json(req: Request) -> tuple[dict, str | None]:
    try:
        json_reader = getattr(req, "json", None)

        if callable(json_reader):
            body = json_reader()
            if isawaitable(body):
                body = await body
        else:
            body = req.get_json()

    except Exception as exc:
        return {}, f"JSON invalido: {exc}"

    if body in (None, ""):
        return {}, None

    if not isinstance(body, dict):
        return {}, "El cuerpo debe ser un objeto JSON."

    return body, None
