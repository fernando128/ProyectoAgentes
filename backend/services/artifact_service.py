import re
from pathlib import Path
from urllib.parse import quote

from utils.helpers import _find_first_value, _get_obj_value, _maybe_await

def _generated_file_payload(
    annotation,
    download_base_url: str,
    fallback_container_id: str | None = None,
) -> dict | None:
    annotation_type = _get_obj_value(annotation, "type")

    if annotation_type not in {"container_file_citation", "file_path"}:
        return None

    container_id = _get_obj_value(annotation, "container_id") or fallback_container_id
    file_id = _get_obj_value(annotation, "file_id")
    filename = (
        _get_obj_value(annotation, "filename")
        or _get_obj_value(annotation, "text")
        or _get_obj_value(annotation, "path")
    )

    if not (container_id and file_id):
        return None

    filename = Path(str(filename or file_id)).name
    download_url = (
        f"{download_base_url}/generated-files/"
        f"{quote(str(container_id), safe='')}/{quote(str(file_id), safe='')}"
        f"?filename={quote(filename, safe='')}"
    )

    return {
        "type": annotation_type,
        "container_id": str(container_id),
        "file_id": str(file_id),
        "filename": filename,
        "download_url": download_url,
    }

def _iter_annotations(value, depth: int = 0):
    if value is None or depth > 8:
        return

    if isinstance(value, dict):
        if value.get("type") in {"container_file_citation", "file_path"}:
            yield value

        for child in value.values():
            yield from _iter_annotations(child, depth + 1)

        return

    if isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_annotations(child, depth + 1)

        return

    model_dump = getattr(value, "model_dump", None)

    if callable(model_dump):
        yield from _iter_annotations(model_dump(), depth + 1)
        return

    value_dict = getattr(value, "__dict__", None)

    if isinstance(value_dict, dict):
        yield from _iter_annotations(value_dict, depth + 1)

def _download_markdown_link(file_payload: dict) -> str:
    filename = file_payload["filename"]
    download_url = file_payload["download_url"]
    return f"\n\n[Descargar {filename}]({download_url})\n\n"

def _sandbox_filenames(text: str) -> set[str]:
    paths = re.findall(r"sandbox:/mnt/data/[^\s)\]]+", text)
    filenames = set()

    for path in paths:
        clean_path = path.rstrip(".,;:")
        filename = Path(clean_path.replace("\\_", "_")).name

        if filename:
            filenames.add(filename)

    return filenames

async def _iter_container_files(openai_client, container_id: str):
    files_resource = getattr(getattr(openai_client, "containers", None), "files", None)

    if not files_resource:
        return

    list_method = getattr(files_resource, "list", None)

    if not callable(list_method):
        return

    files_result = await _maybe_await(list_method(container_id=container_id))

    async_iter = getattr(files_result, "__aiter__", None)

    if callable(async_iter):
        async for file_item in files_result:
            yield file_item
        return

    data = getattr(files_result, "data", None)

    if data is not None:
        for file_item in data:
            yield file_item
        return

    for file_item in files_result or []:
        yield file_item

async def _resolve_sandbox_file_payloads(
    openai_client,
    container_id: str | None,
    filenames: set[str],
    download_base_url: str,
) -> list[dict]:
    if not container_id or not filenames:
        return []

    payloads = []

    async for file_item in _iter_container_files(openai_client, container_id):
        file_id = _get_obj_value(file_item, "id") or _get_obj_value(file_item, "file_id")
        filename = (
            _get_obj_value(file_item, "filename")
            or _get_obj_value(file_item, "name")
            or _get_obj_value(file_item, "path")
        )
        filename = Path(str(filename or "")).name

        if not file_id or filename not in filenames:
            continue

        payload = _generated_file_payload(
            {
                "type": "container_file_citation",
                "container_id": container_id,
                "file_id": file_id,
                "filename": filename,
            },
            download_base_url,
        )

        if payload:
            payloads.append(payload)

    return payloads

def _extract_output_text(value, depth: int = 0) -> str:
    if value is None or depth > 8:
        return ""

    output_text = getattr(value, "output_text", None)

    if isinstance(output_text, str) and output_text:
        return output_text

    if isinstance(value, dict):
        if value.get("type") in {"output_text", "text"}:
            text_value = value.get("text")

            if isinstance(text_value, str):
                return text_value

            nested_value = _get_obj_value(text_value, "value")

            if nested_value:
                return str(nested_value)

        parts = [_extract_output_text(child, depth + 1) for child in value.values()]
        return "".join(part for part in parts if part)

    if isinstance(value, (list, tuple)):
        parts = [_extract_output_text(child, depth + 1) for child in value]
        return "".join(part for part in parts if part)

    if getattr(value, "type", None) in {"output_text", "text"}:
        text_value = getattr(value, "text", None)

        if isinstance(text_value, str):
            return text_value

        nested_value = _get_obj_value(text_value, "value")

        if nested_value:
            return str(nested_value)

    model_dump = getattr(value, "model_dump", None)

    if callable(model_dump):
        return _extract_output_text(model_dump(), depth + 1)

    value_dict = getattr(value, "__dict__", None)

    if isinstance(value_dict, dict):
        return _extract_output_text(value_dict, depth + 1)

    return ""

async def _collect_generated_file_payloads(
    openai_client,
    response,
    download_base_url: str,
) -> list[dict]:
    active_container_id = _find_first_value(response, "container_id")
    generated_files_seen = set()
    generated_files = []

    for annotation in _iter_annotations(response):
        file_payload = _generated_file_payload(
            annotation,
            download_base_url,
            active_container_id,
        )

        if not file_payload:
            continue

        file_key = (file_payload["container_id"], file_payload["file_id"])

        if file_key in generated_files_seen:
            continue

        generated_files_seen.add(file_key)
        generated_files.append(file_payload)

    sandbox_filenames = _sandbox_filenames(_extract_output_text(response))
    sandbox_payloads = await _resolve_sandbox_file_payloads(
        openai_client,
        str(active_container_id) if active_container_id else None,
        sandbox_filenames,
        download_base_url,
    )

    for file_payload in sandbox_payloads:
        file_key = (file_payload["container_id"], file_payload["file_id"])

        if file_key in generated_files_seen:
            continue

        generated_files_seen.add(file_key)
        generated_files.append(file_payload)

    return generated_files
