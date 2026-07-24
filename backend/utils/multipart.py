from email.parser import BytesParser
from email.policy import default as email_policy

from azurefunctions.extensions.http.fastapi import Request

from utils.helpers import _get_header, _maybe_await

def _first_form_value(fields: dict[str, list[str]], name: str, default: str = "") -> str:
    values = fields.get(name) or []
    return str(values[0]).strip() if values else default

async def _request_body_bytes(req: Request) -> bytes:
    body_reader = getattr(req, "body", None)

    if callable(body_reader):
        body = await _maybe_await(body_reader())
    else:
        body_getter = getattr(req, "get_body", None)
        body = body_getter() if callable(body_getter) else b""

    if isinstance(body, str):
        return body.encode("utf-8")

    return body or b""

async def _read_multipart_form_without_dependency(
    req: Request,
) -> tuple[dict[str, list[str]], list[dict], str | None]:
    content_type = _get_header(req, "content-type")

    if "multipart/form-data" not in content_type.lower():
        return {}, [], "Debes enviar archivos usando multipart/form-data."

    body = await _request_body_bytes(req)

    if not body:
        return {}, [], "El cuerpo de la solicitud esta vacio."

    raw_message = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        + body
    )
    message = BytesParser(policy=email_policy).parsebytes(raw_message)

    if not message.is_multipart():
        return {}, [], "No se pudo interpretar el multipart/form-data."

    fields = {}
    uploaded_files = []

    for part in message.iter_parts():
        field_name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        content = part.get_payload(decode=True) or b""

        if not filename:
            if field_name:
                charset = part.get_content_charset() or "utf-8"
                fields.setdefault(field_name, []).append(
                    content.decode(charset, errors="replace")
                )
            continue

        if not content:
            continue

        uploaded_files.append(
            {
                "filename": filename,
                "content_type": part.get_content_type()
                or "application/octet-stream",
                "content": content,
            }
        )

    return fields, uploaded_files, None

async def _read_multipart_without_dependency(req: Request) -> tuple[list[dict], str | None]:
    _, uploaded_files, upload_error = await _read_multipart_form_without_dependency(req)

    if upload_error:
        return [], upload_error

    if not uploaded_files:
        return [], "Debes enviar al menos un archivo en multipart/form-data."

    return uploaded_files, None

async def _read_multipart_form(
    req: Request,
) -> tuple[dict[str, list[str]], list[dict], str | None]:
    try:
        form = await _maybe_await(req.form())

    except Exception as exc:
        if "python-multipart" in str(exc):
            return await _read_multipart_form_without_dependency(req)

        return {}, [], f"No se pudo leer el multipart/form-data: {exc}"

    if hasattr(form, "multi_items"):
        form_items = form.multi_items()
    else:
        form_items = form.items()

    fields = {}
    uploaded_files = []

    for key, item in form_items:
        filename = getattr(item, "filename", None)

        if not filename:
            fields.setdefault(str(key), []).append(str(item))
            continue

        reader = getattr(item, "read", None)

        if not callable(reader):
            continue

        content = await _maybe_await(reader())

        if isinstance(content, str):
            content = content.encode("utf-8")

        if not content:
            continue

        uploaded_files.append(
            {
                "filename": filename,
                "content_type": getattr(item, "content_type", None)
                or "application/octet-stream",
                "content": content,
            }
        )

    return fields, uploaded_files, None

async def _read_uploaded_files(req: Request) -> tuple[list[dict], str | None]:
    _, uploaded_files, upload_error = await _read_multipart_form(req)

    if upload_error:
        return [], upload_error

    if not uploaded_files:
        return [], "Debes enviar al menos un archivo en multipart/form-data."

    return uploaded_files, None
