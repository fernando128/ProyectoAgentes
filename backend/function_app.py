import json
import logging
import mimetypes
import os
import re
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from datetime import timezone
from email.parser import BytesParser
from email.policy import default as email_policy
from io import BytesIO
from inspect import isawaitable
from pathlib import Path
from urllib.parse import quote

import azure.functions as func
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import AutoCodeInterpreterToolParam
from azure.ai.projects.models import CodeInterpreterTool
from azure.ai.projects.models import FileSearchTool
from azure.ai.projects.models import PromptAgentDefinition
from azure.identity.aio import AzureCliCredential
from azure.identity.aio import DefaultAzureCredential
from azurefunctions.extensions.http.fastapi import Request, Response, StreamingResponse

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

TOKEN_SCOPE = "https://ai.azure.com/.default"
DEFAULT_AGENT_NAME = "agente-code-interpreter"
DEFAULT_ALLOWED_ORIGIN = "http://localhost:5173"
DEFAULT_AGENT_INSTRUCTIONS = (
    "Eres un agente util que puede responder preguntas, buscar informacion en "
    "archivos asociados y usar Python para analisis, calculos y transformacion "
    "de datos cuando sea necesario."
)
TOOL_USAGE_INSTRUCTIONS = (
    "Capacidades disponibles:\n"
    "- Usa File Search cuando la pregunta dependa de documentos, archivos, "
    "manuales, politicas, contratos, datos cargados o conocimiento privado del "
    "usuario.\n"
    "- Usa Code Interpreter cuando necesites ejecutar Python para calculos, "
    "analisis de datos, transformaciones, tablas, graficos, validaciones o "
    "razonamiento numerico.\n"
    "- Si el usuario pide crear, generar o descargar un archivo, usa Code "
    "Interpreter para escribir el archivo real en /mnt/data. No simules enlaces "
    "sandbox ni digas que un archivo fue creado si no lo escribiste con la "
    "herramienta.\n"
    "- Si la pregunta se responde con conocimiento general y no requiere "
    "archivos ni calculos, responde directamente."
)
DEFAULT_DB_FILENAME = "agents_db.json"
CODE_INTERPRETER_FILE_INPUT_COUNT = 10

# --- Cambia esto según donde vayas a correr el backend ---
ENTORNO = "local"  # "local" o "nube"

# Este es el endpoint del RECURSO DE AZURE AI FOUNDRY, siempre el mismo,
# sin importar si tu backend corre local o desplegado.
FOUNDRY_ENDPOINT = "https://audibotfoundryagentes.services.ai.azure.com/api/projects/AudibotProyectoAgentes"


def _get_credential():
    """Crea una credencial nueva según el entorno. Se crea por request para poder cerrarla bien."""
    if ENTORNO == "local":
        return AzureCliCredential()
    return DefaultAzureCredential()


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


def _sse(data: str, event: str | None = None) -> str:
    lines = []

    if event:
        lines.append(f"event: {event}")

    normalized_data = data.replace("\r\n", "\n").replace("\r", "\n")

    for line in normalized_data.split("\n"):
        lines.append(f"data: {line}")

    return "\n".join(lines) + "\n\n"


def _get_agent_version(agent) -> str | None:
    versions = getattr(agent, "versions", None)
    latest = getattr(versions, "latest", None) if versions else None
    return getattr(latest, "version", None) if latest else None


def _get_agent_payload(agent, vector_store_ids: list[str] | None = None) -> dict:
    name = getattr(agent, "name", None)
    version = getattr(agent, "version", None) or _get_agent_version(agent)
    agent_id = getattr(agent, "id", None) or (
        f"{name}:{version}" if name and version else name
    )

    payload = {
        "id": agent_id,
        "name": name,
        "version": version,
    }

    if vector_store_ids is not None:
        payload["vector_store_ids"] = vector_store_ids

    return payload


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    configured_path = os.getenv("AGENTS_DB_PATH", "").strip()

    if configured_path:
        path = Path(configured_path)
        if path.is_absolute():
            return path

        return Path(__file__).parent / path

    return Path(__file__).with_name(DEFAULT_DB_FILENAME)


def _empty_db() -> dict:
    return {"agents": {}}


def _load_db() -> dict:
    path = _db_path()

    if not path.exists():
        return _empty_db()

    with path.open("r", encoding="utf-8") as db_file:
        data = json.load(db_file)

    if not isinstance(data, dict):
        return _empty_db()

    data.setdefault("agents", {})
    return data


def _save_db(data: dict) -> None:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")

    with temp_path.open("w", encoding="utf-8") as db_file:
        json.dump(data, db_file, ensure_ascii=False, indent=2)

    os.replace(temp_path, path)


def _save_agent_record(record: dict) -> dict:
    db = _load_db()
    db["agents"][record["agent_id"]] = record
    _save_db(db)
    return record


def _get_agent_record(agent_id: str) -> dict | None:
    return _load_db().get("agents", {}).get(agent_id)


def _list_agent_records() -> list[dict]:
    return list(_load_db().get("agents", {}).values())


def _append_file_record(agent_id: str, file_record: dict) -> dict:
    db = _load_db()
    record = db.get("agents", {}).get(agent_id)

    if not record:
        raise KeyError(agent_id)

    record.setdefault("files", []).append(file_record)
    record["updated_at"] = _utc_now()
    db["agents"][agent_id] = record
    _save_db(db)
    return record


def _append_code_file_record(agent_id: str, file_record: dict) -> dict:
    db = _load_db()
    record = db.get("agents", {}).get(agent_id)

    if not record:
        raise KeyError(agent_id)

    record.setdefault("code_files", []).append(file_record)
    record["updated_at"] = _utc_now()
    db["agents"][agent_id] = record
    _save_db(db)
    return record


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


async def _create_conversation_id(openai_client) -> str | None:
    conversations = getattr(openai_client, "conversations", None)
    create = getattr(conversations, "create", None) if conversations else None

    if not callable(create):
        return None

    conversation = await _maybe_await(create())
    return str(getattr(conversation, "id", conversation))


async def _read_binary_response(file_content) -> bytes:
    if isinstance(file_content, bytes):
        return file_content

    if hasattr(file_content, "read") and callable(file_content.read):
        content = file_content.read()
        content = await _maybe_await(content)
        return content or b""

    body = getattr(file_content, "body", None)

    if body is not None:
        chunks = []

        async_iter = getattr(body, "__aiter__", None)

        if callable(async_iter):
            async for chunk in body:
                chunks.append(chunk if isinstance(chunk, bytes) else bytes(chunk))
            return b"".join(chunks)

        for chunk in body:
            chunks.append(chunk if isinstance(chunk, bytes) else bytes(chunk))

        return b"".join(chunks)

    content = getattr(file_content, "content", None)

    if content is not None:
        content = await _maybe_await(content)
        return content or b""

    return bytes(file_content)


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


def _clean_agent_name(name: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_-]+", "-", name.strip())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-_")
    return normalized or DEFAULT_AGENT_NAME


def _unique_agent_name(name: str) -> str:
    unique_suffix = uuid.uuid4().hex[:12]
    base_name = _clean_agent_name(name)
    max_base_length = 80 - len(unique_suffix) - 1
    return f"{base_name[:max_base_length].rstrip('-_')}-{unique_suffix}"


def _build_agent_instructions(instructions: str) -> str:
    instructions = instructions.strip() or DEFAULT_AGENT_INSTRUCTIONS

    if "Capacidades disponibles:" in instructions:
        return instructions

    return f"{instructions}\n\n{TOOL_USAGE_INSTRUCTIONS}"


def _requires_generated_file(message: str) -> bool:
    normalized = message.lower()
    keywords = (
        "archivo",
        "descarga",
        "descargar",
        "generar un .py",
        "crear un .py",
        ".py",
        ".csv",
        ".xlsx",
        ".json",
        ".txt",
        ".pdf",
        ".png",
        ".zip",
    )
    return any(keyword in normalized for keyword in keywords)


def _tool_enforced_message(message: str) -> str:
    if not _requires_generated_file(message):
        return message

    return (
        f"{message}\n\n"
        "Instruccion obligatoria para esta respuesta: si vas a decir que creaste "
        "un archivo descargable, debes usar Code Interpreter para escribir el "
        "archivo real en /mnt/data. No escribas ni simules rutas sandbox:/mnt/data "
        "si no ejecutaste codigo y creaste el archivo fisicamente. Despues de "
        "crearlo, devuelve el enlace generado por la herramienta."
    )


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "si", "sí"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False

    return default


def _as_string_list(value) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []

    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    return []


def _get_model_deployment(payload: dict) -> str:
    return (
        str(
            payload.get("model")
            or payload.get("model_deployment")
            or os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
            or os.getenv("MODEL_DEPLOYMENT_NAME")
            or ""
        )
        .strip()
    )


def _code_input_name(index: int) -> str:
    return f"analysis_file_id_{index + 1}"


def _should_require_code_interpreter(message: str, code_file_ids: list[str]) -> bool:
    if _requires_generated_file(message):
        return True

    if not code_file_ids:
        return False

    normalized = message.lower()
    keywords = (
        "excel",
        "xlsx",
        "csv",
        "analiza",
        "analizar",
        "calcula",
        "calcular",
        "grafica",
        "gráfico",
        "grafico",
        "tabla",
        "datos",
        "hoja",
    )
    return any(keyword in normalized for keyword in keywords)


def _build_agent_tools(
    enable_code_interpreter: bool,
    enable_file_search: bool,
    vector_store_ids: list[str],
    code_file_ids: list[str] | None = None,
) -> list:
    tools = []

    if enable_code_interpreter:
        code_file_ids = (code_file_ids or [])[:CODE_INTERPRETER_FILE_INPUT_COUNT]
        container = (
            AutoCodeInterpreterToolParam(file_ids=code_file_ids)
            if code_file_ids
            else AutoCodeInterpreterToolParam()
        )
        tools.append(
            CodeInterpreterTool(container=container)
        )

    if enable_file_search and vector_store_ids:
        tools.append(FileSearchTool(vector_store_ids=vector_store_ids))

    return tools


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


async def _read_multipart_without_dependency(req: Request) -> tuple[list[dict], str | None]:
    content_type = _get_header(req, "content-type")

    if "multipart/form-data" not in content_type.lower():
        return [], "Debes enviar archivos usando multipart/form-data."

    body = await _request_body_bytes(req)

    if not body:
        return [], "El cuerpo de la solicitud esta vacio."

    raw_message = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        + body
    )
    message = BytesParser(policy=email_policy).parsebytes(raw_message)

    if not message.is_multipart():
        return [], "No se pudo interpretar el multipart/form-data."

    uploaded_files = []

    for part in message.iter_parts():
        filename = part.get_filename()

        if not filename:
            continue

        content = part.get_payload(decode=True) or b""

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

    if not uploaded_files:
        return [], "Debes enviar al menos un archivo en multipart/form-data."

    return uploaded_files, None


async def _read_uploaded_files(req: Request) -> tuple[list[dict], str | None]:
    try:
        form = await _maybe_await(req.form())

    except Exception as exc:
        if "python-multipart" in str(exc):
            return await _read_multipart_without_dependency(req)

        return [], f"No se pudo leer el multipart/form-data: {exc}"

    if hasattr(form, "multi_items"):
        form_items = form.multi_items()
    else:
        form_items = form.items()

    uploaded_files = []

    for _, item in form_items:
        filename = getattr(item, "filename", None)

        if not filename:
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

    if not uploaded_files:
        return [], "Debes enviar al menos un archivo en multipart/form-data."

    return uploaded_files, None


async def _resolve_agent_version(
    client: AIProjectClient,
    agent_name: str,
    configured_version: str | None,
    auto_resolve: bool,
) -> str:
    if configured_version:
        return configured_version

    if not auto_resolve:
        raise ValueError(
            "Falta AZURE_AI_AGENT_VERSION. Configura la version del agente en "
            "local.settings.json para evitar listar agentes con agents/read."
        )

    async for agent in client.agents.list():
        if getattr(agent, "name", None) == agent_name:
            version = _get_agent_version(agent)

            if version:
                return version

            break

    raise ValueError(
        f"No se pudo encontrar una versión activa para el agente '{agent_name}'."
    )


async def _stream_agent_response(
    message: str,
    agent_name_override: str | None = None,
    agent_version_override: str | None = None,
    download_base_url: str = "http://localhost:7071",
    code_file_ids: list[str] | None = None,
) -> AsyncIterator[str]:
    endpoint = FOUNDRY_ENDPOINT
    agent_name = (agent_name_override or os.getenv("AZURE_AI_AGENT_NAME", DEFAULT_AGENT_NAME)).strip()
    agent_version = (agent_version_override or os.getenv("AZURE_AI_AGENT_VERSION", "")).strip() or None
    auto_resolve_agent_version = (
        os.getenv("AZURE_AI_AUTO_RESOLVE_AGENT_VERSION", "false").strip().lower()
        == "true"
    )

    if not endpoint:
        yield _sse("Falta AZURE_AI_PROJECT_ENDPOINT en la configuración.", "agent-error")
        yield _sse("[FIN]")
        return

    if not message:
        yield _sse("Envía una pregunta en el parámetro 'message'.", "agent-error")
        yield _sse("[FIN]")
        return

    credential = None
    project_client = None

    try:
        credential = _get_credential()
        project_client = AIProjectClient(endpoint=endpoint, credential=credential)

        logging.info("Validando credencial de Azure AI. Entorno=%s", ENTORNO)
        await credential.get_token(TOKEN_SCOPE)

        resolved_version = await _resolve_agent_version(
            client=project_client,
            agent_name=agent_name,
            configured_version=agent_version,
            auto_resolve=auto_resolve_agent_version,
        )

        logging.info("Iniciando stream con agente %s:%s", agent_name, resolved_version)
        yield _sse(
            json.dumps(
                {"agent": agent_name, "version": resolved_version},
                ensure_ascii=False,
            ),
            "metadata",
        )

        openai_client = project_client.get_openai_client()
        conversation_id = await _create_conversation_id(openai_client)
        request_message = _tool_enforced_message(message)
        runtime_code_file_ids = (code_file_ids or [])[:CODE_INTERPRETER_FILE_INPUT_COUNT]

        response_kwargs = {
            "input": [{"role": "user", "content": request_message}],
            "stream": True,
            "extra_body": {
                "agent_reference": {
                    "name": agent_name,
                    "version": resolved_version,
                    "type": "agent_reference",
                }
            },
        }

        if _should_require_code_interpreter(message, runtime_code_file_ids):
            response_kwargs["tool_choice"] = "required"

        if conversation_id:
            response_kwargs["conversation"] = conversation_id

        stream = await openai_client.responses.create(**response_kwargs)
        generated_files_seen = set()
        active_container_id = None
        output_text_parts = []

        async for event in stream:
            event_type = getattr(event, "type", "")
            event_container_id = _find_first_value(event, "container_id")

            if event_container_id:
                active_container_id = str(event_container_id)

            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "")

                if delta:
                    output_text_parts.append(delta)
                    yield _sse(delta)

            elif event_type == "response.output_text.annotation.added":
                annotation = getattr(event, "annotation", None)
                file_payload = _generated_file_payload(
                    annotation,
                    download_base_url,
                    active_container_id,
                )

                if not file_payload:
                    continue

                file_key = (
                    file_payload["container_id"],
                    file_payload["file_id"],
                )

                if file_key in generated_files_seen:
                    continue

                generated_files_seen.add(file_key)
                logging.info(
                    "Archivo generado por Code Interpreter: %s (%s/%s)",
                    file_payload["filename"],
                    file_payload["container_id"],
                    file_payload["file_id"],
                )
                yield _sse(json.dumps(file_payload, ensure_ascii=False), "artifact")
                yield _sse(_download_markdown_link(file_payload))

            elif event_type == "response.completed":
                response = getattr(event, "response", None)
                response_container_id = _find_first_value(response, "container_id")

                if response_container_id:
                    active_container_id = str(response_container_id)

                for annotation in _iter_annotations(getattr(event, "response", None)):
                    file_payload = _generated_file_payload(
                        annotation,
                        download_base_url,
                        active_container_id,
                    )

                    if not file_payload:
                        continue

                    file_key = (
                        file_payload["container_id"],
                        file_payload["file_id"],
                    )

                    if file_key in generated_files_seen:
                        continue

                    generated_files_seen.add(file_key)
                    yield _sse(json.dumps(file_payload, ensure_ascii=False), "artifact")
                    yield _sse(_download_markdown_link(file_payload))

                sandbox_filenames = _sandbox_filenames("".join(output_text_parts))
                sandbox_payloads = await _resolve_sandbox_file_payloads(
                    openai_client,
                    active_container_id,
                    sandbox_filenames,
                    download_base_url,
                )

                for file_payload in sandbox_payloads:
                    file_key = (
                        file_payload["container_id"],
                        file_payload["file_id"],
                    )

                    if file_key in generated_files_seen:
                        continue

                    generated_files_seen.add(file_key)
                    yield _sse(json.dumps(file_payload, ensure_ascii=False), "artifact")
                    yield _sse(_download_markdown_link(file_payload))

                break

            elif event_type == "response.failed":
                error = getattr(event, "error", None)
                error_message = getattr(error, "message", None) or "Falló la respuesta."
                logging.error("El agente devolvió error: %s", error_message)
                yield _sse(error_message, "agent-error")
                break

    except Exception as exc:
        logging.exception("Error al invocar el agente de Azure AI Foundry.")
        yield _sse(f"{type(exc).__name__}: {exc}", "agent-error")

    finally:
        if project_client:
            await project_client.close()

        if credential:
            await credential.close()

        yield _sse("[FIN]")


@app.route(route="chat-stream", methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS])
async def chat_stream(req: Request):
    if req.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())

    message = req.query_params.get("message", "").strip()
    agent_id = req.query_params.get("agent_id", "").strip()
    agent_name = req.query_params.get("agent_name", "").strip() or None
    agent_version = req.query_params.get("agent_version", "").strip() or None
    code_file_ids = []

    if agent_id and not (agent_name and agent_version):
        agent_record = _get_agent_record(agent_id)

        if not agent_record:
            return _json_response(404, {"error": "No existe un agente con ese agent_id."})

        agent_name = agent_record.get("agent_name")
        agent_version = agent_record.get("agent_version")
        code_file_ids = [
            file_record.get("file_id")
            for file_record in agent_record.get("code_files", [])
            if file_record.get("file_id")
        ]

    return StreamingResponse(
        _stream_agent_response(
            message,
            agent_name,
            agent_version,
            _get_request_base_url(req),
            code_file_ids,
        ),
        media_type="text/event-stream",
        headers=_cors_headers(),
    )


@app.route(
    route="generated-files/{container_id}/{file_id}",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
)
async def download_generated_file(req: Request):
    if req.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())

    container_id = _get_route_param(req, "container_id")
    file_id = _get_route_param(req, "file_id")
    filename = req.query_params.get("filename", "").strip() or file_id
    filename = Path(filename).name

    if not (container_id and file_id):
        return _json_response(
            400,
            {"error": "Faltan container_id o file_id para descargar el archivo."},
        )

    credential = None
    project_client = None

    try:
        credential = _get_credential()
        project_client = AIProjectClient(endpoint=FOUNDRY_ENDPOINT, credential=credential)

        logging.info("Validando credencial de Azure AI. Entorno=%s", ENTORNO)
        await credential.get_token(TOKEN_SCOPE)

        openai_client = project_client.get_openai_client()
        file_content = await _maybe_await(
            openai_client.containers.files.content.retrieve(
                container_id=container_id,
                file_id=file_id,
            )
        )
        content = await _read_binary_response(file_content)
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        return Response(
            status_code=200,
            headers={
                **_cors_headers(),
                "Content-Type": content_type,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
            content=content,
        )

    except Exception as exc:
        logging.exception("Error al descargar archivo generado por Code Interpreter.")
        return _json_response(500, {"error": f"{type(exc).__name__}: {exc}"})

    finally:
        if project_client:
            await project_client.close()
        if credential:
            await credential.close()


@app.route(
    route="agents",
    methods=[func.HttpMethod.GET, func.HttpMethod.POST, func.HttpMethod.OPTIONS],
)
async def list_agents(req: Request):
    if req.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())

    if req.method == "POST":
        return await create_agent(req)

    endpoint = FOUNDRY_ENDPOINT

    credential = None
    project_client = None
    agents_payload = []

    try:
        credential = _get_credential()
        project_client = AIProjectClient(endpoint=endpoint, credential=credential)

        logging.info("Validando credencial de Azure AI. Entorno=%s", ENTORNO)
        await credential.get_token(TOKEN_SCOPE)

        records_by_foundry_ref = {
            (
                record.get("agent_name"),
                record.get("agent_version"),
            ): record
            for record in _list_agent_records()
        }
        foundry_refs = set()

        async for agent in project_client.agents.list():
            agent_payload = _get_agent_payload(agent)
            foundry_ref = (agent_payload.get("name"), agent_payload.get("version"))
            foundry_refs.add(foundry_ref)

            record = records_by_foundry_ref.get(foundry_ref)

            if record:
                agent_payload.update(
                    {
                        "agent_id": record.get("agent_id"),
                        "display_name": record.get("display_name"),
                        "vector_store_id": record.get("vector_store_id"),
                        "files": record.get("files", []),
                        "code_files": record.get("code_files", []),
                    }
                )

            agents_payload.append(agent_payload)

        for foundry_ref, record in records_by_foundry_ref.items():
            if foundry_ref in foundry_refs:
                continue

            agents_payload.append(
                {
                    "id": record.get("agent_id"),
                    "agent_id": record.get("agent_id"),
                    "display_name": record.get("display_name"),
                    "name": record.get("agent_name"),
                    "version": record.get("agent_version"),
                    "vector_store_id": record.get("vector_store_id"),
                    "files": record.get("files", []),
                    "code_files": record.get("code_files", []),
                    "status": "stored_only",
                }
            )

    except Exception as exc:
        logging.exception("Error al listar agentes de Azure AI Foundry.")
        return Response(
            status_code=500,
            headers=_cors_headers(),
            content=json.dumps(
                {"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False
            ),
        )

    finally:
        if project_client:
            await project_client.close()
        if credential:
            await credential.close()

    return Response(
        status_code=200,
        headers={**_cors_headers(), "Content-Type": "application/json"},
        content=json.dumps({"agents": agents_payload}, ensure_ascii=False),
    )


async def create_agent(req: Request):
    payload, payload_error = await _request_json(req)

    if payload_error:
        return _json_response(400, {"error": payload_error})

    model = _get_model_deployment(payload)

    if not model:
        return _json_response(
            400,
            {
                "error": (
                    "Falta el modelo. Envia 'model' en el body o configura "
                    "AZURE_AI_MODEL_DEPLOYMENT_NAME."
                )
            },
        )

    endpoint = FOUNDRY_ENDPOINT
    agent_id = str(payload.get("agent_id") or uuid.uuid4()).strip()

    if _get_agent_record(agent_id):
        return _json_response(
            409,
            {"error": "Ya existe un agente local con ese agent_id."},
        )

    requested_name = str(payload.get("name") or payload.get("agent_name") or DEFAULT_AGENT_NAME)
    agent_name = _unique_agent_name(requested_name)
    instructions = _build_agent_instructions(
        str(payload.get("instructions") or DEFAULT_AGENT_INSTRUCTIONS)
    )
    description = str(
        payload.get("description")
        or "Agente con Code Interpreter y File Search creado desde el backend."
    ).strip()
    enable_code_interpreter = _as_bool(
        payload.get("enable_code_interpreter", payload.get("code_interpreter")),
        True,
    )
    enable_file_search = _as_bool(
        payload.get("enable_file_search", payload.get("file_search")),
        True,
    )
    vector_store_ids = _as_string_list(
        payload.get("vector_store_ids") or payload.get("vector_store_id")
    )

    credential = None
    project_client = None

    try:
        credential = _get_credential()
        project_client = AIProjectClient(endpoint=endpoint, credential=credential)

        logging.info("Validando credencial de Azure AI. Entorno=%s", ENTORNO)
        await credential.get_token(TOKEN_SCOPE)

        vector_store_id = None

        if enable_file_search:
            if not vector_store_ids:
                openai_client = project_client.get_openai_client()
                vector_store = await _maybe_await(
                    openai_client.vector_stores.create(name=f"{agent_name}-files")
                )
                vector_store_ids = [vector_store.id]

            vector_store_id = vector_store_ids[0]

        tools = _build_agent_tools(
            enable_code_interpreter=enable_code_interpreter,
            enable_file_search=enable_file_search,
            vector_store_ids=vector_store_ids,
        )

        agent = await _maybe_await(
            project_client.agents.create_version(
                agent_name=agent_name,
                definition=PromptAgentDefinition(
                    model=model,
                    instructions=instructions,
                    tools=tools,
                ),
                description=description,
            ),
        )
        agent_payload = _get_agent_payload(agent, vector_store_ids)
        agent_record = {
            "agent_id": agent_id,
            "display_name": requested_name.strip(),
            "agent_name": agent_payload.get("name"),
            "agent_version": agent_payload.get("version"),
            "vector_store_id": vector_store_id,
            "vector_store_ids": vector_store_ids,
            "model": model,
            "instructions": instructions,
            "description": description,
            "tools": {
                "code_interpreter": enable_code_interpreter,
                "file_search": enable_file_search,
            },
            "files": [],
            "code_files": [],
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        _save_agent_record(agent_record)

    except Exception as exc:
        logging.exception("Error al crear el agente de Azure AI Foundry.")
        return _json_response(500, {"error": f"{type(exc).__name__}: {exc}"})

    finally:
        if project_client:
            await project_client.close()
        if credential:
            await credential.close()

    return _json_response(
        201,
        {
            "agent": agent_record,
            "tools": agent_record["tools"],
        },
    )


@app.route(
    route="agents/{agent_id}/files",
    methods=[func.HttpMethod.GET, func.HttpMethod.POST, func.HttpMethod.OPTIONS],
)
async def agent_files(req: Request):
    if req.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())

    agent_id = _get_route_param(req, "agent_id")

    if not agent_id:
        return _json_response(400, {"error": "Falta agent_id en la ruta."})

    agent_record = _get_agent_record(agent_id)

    if not agent_record:
        return _json_response(404, {"error": "No existe un agente con ese agent_id."})

    if req.method == "GET":
        return _json_response(
            200,
            {
                "agent_id": agent_id,
                "vector_store_id": agent_record.get("vector_store_id"),
                "files": agent_record.get("files", []),
            },
        )

    vector_store_id = agent_record.get("vector_store_id")

    if not vector_store_id:
        return _json_response(
            400,
            {"error": "Este agente no tiene vector_store_id configurado."},
        )

    uploaded_files, upload_error = await _read_uploaded_files(req)

    if upload_error:
        return _json_response(400, {"error": upload_error})

    credential = None
    project_client = None
    saved_files = []

    try:
        credential = _get_credential()
        project_client = AIProjectClient(endpoint=FOUNDRY_ENDPOINT, credential=credential)

        logging.info("Validando credencial de Azure AI. Entorno=%s", ENTORNO)
        await credential.get_token(TOKEN_SCOPE)

        openai_client = project_client.get_openai_client()

        for uploaded_file in uploaded_files:
            file_content = BytesIO(uploaded_file["content"])
            file_content.name = uploaded_file["filename"]
            vector_store_file = await _maybe_await(
                openai_client.vector_stores.files.upload_and_poll(
                    vector_store_id=vector_store_id,
                    file=(
                        uploaded_file["filename"],
                        file_content,
                        uploaded_file["content_type"],
                    ),
                )
            )
            file_record = {
                "file_id": getattr(vector_store_file, "id", None),
                "vector_store_id": getattr(
                    vector_store_file, "vector_store_id", vector_store_id
                ),
                "filename": uploaded_file["filename"],
                "content_type": uploaded_file["content_type"],
                "bytes": len(uploaded_file["content"]),
                "status": getattr(vector_store_file, "status", None),
                "created_at": _utc_now(),
            }
            saved_files.append(file_record)

        for file_record in saved_files:
            agent_record = _append_file_record(agent_id, file_record)

    except Exception as exc:
        logging.exception("Error al subir archivos al vector store del agente.")
        return _json_response(500, {"error": f"{type(exc).__name__}: {exc}"})

    finally:
        if project_client:
            await project_client.close()
        if credential:
            await credential.close()

    return _json_response(
        201,
        {
            "agent_id": agent_id,
            "vector_store_id": vector_store_id,
            "files": saved_files,
            "agent": agent_record,
        },
    )


@app.route(
    route="agents/{agent_id}/code-files",
    methods=[func.HttpMethod.GET, func.HttpMethod.POST, func.HttpMethod.OPTIONS],
)
async def agent_code_files(req: Request):
    if req.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())

    agent_id = _get_route_param(req, "agent_id")

    if not agent_id:
        return _json_response(400, {"error": "Falta agent_id en la ruta."})

    agent_record = _get_agent_record(agent_id)

    if not agent_record:
        return _json_response(404, {"error": "No existe un agente con ese agent_id."})

    if req.method == "GET":
        return _json_response(
            200,
            {
                "agent_id": agent_id,
                "files": agent_record.get("code_files", []),
            },
        )

    uploaded_files, upload_error = await _read_uploaded_files(req)

    if upload_error:
        return _json_response(400, {"error": upload_error})

    existing_count = len(agent_record.get("code_files", []))
    available_slots = CODE_INTERPRETER_FILE_INPUT_COUNT - existing_count

    if available_slots <= 0:
        return _json_response(
            400,
            {
                "error": (
                    f"Este agente ya tiene el maximo de "
                    f"{CODE_INTERPRETER_FILE_INPUT_COUNT} archivos para analisis."
                )
            },
        )

    uploaded_files = uploaded_files[:available_slots]

    credential = None
    project_client = None
    saved_files = []

    try:
        credential = _get_credential()
        project_client = AIProjectClient(endpoint=FOUNDRY_ENDPOINT, credential=credential)

        logging.info("Validando credencial de Azure AI. Entorno=%s", ENTORNO)
        await credential.get_token(TOKEN_SCOPE)

        openai_client = project_client.get_openai_client()

        for uploaded_file in uploaded_files:
            file_content = BytesIO(uploaded_file["content"])
            file_content.name = uploaded_file["filename"]

            try:
                uploaded = await _maybe_await(
                    openai_client.files.create(
                        purpose="assistants",
                        file=file_content,
                    )
                )

            except TypeError:
                uploaded = await _maybe_await(
                    openai_client.files.create(
                        purpose="assistants",
                        file=(
                            uploaded_file["filename"],
                            BytesIO(uploaded_file["content"]),
                            uploaded_file["content_type"],
                        ),
                    )
                )

            file_record = {
                "file_id": getattr(uploaded, "id", None),
                "filename": uploaded_file["filename"],
                "content_type": uploaded_file["content_type"],
                "bytes": len(uploaded_file["content"]),
                "purpose": "assistants",
                "created_at": _utc_now(),
            }
            saved_files.append(file_record)

        existing_code_files = agent_record.get("code_files", [])
        all_code_files = existing_code_files + saved_files
        code_file_ids = [
            file_record.get("file_id")
            for file_record in all_code_files
            if file_record.get("file_id")
        ][:CODE_INTERPRETER_FILE_INPUT_COUNT]
        vector_store_ids = _as_string_list(
            agent_record.get("vector_store_ids") or agent_record.get("vector_store_id")
        )
        tools = _build_agent_tools(
            enable_code_interpreter=True,
            enable_file_search=bool(vector_store_ids),
            vector_store_ids=vector_store_ids,
            code_file_ids=code_file_ids,
        )
        agent = await _maybe_await(
            project_client.agents.create_version(
                agent_name=agent_record["agent_name"],
                definition=PromptAgentDefinition(
                    model=agent_record["model"],
                    instructions=agent_record["instructions"],
                    tools=tools,
                ),
                description=agent_record.get("description")
                or "Agente actualizado con archivos para Code Interpreter.",
            )
        )
        agent_record["agent_version"] = getattr(agent, "version", None) or _get_agent_version(agent)
        agent_record["code_files"] = all_code_files
        agent_record["updated_at"] = _utc_now()
        _save_agent_record(agent_record)

    except Exception as exc:
        logging.exception("Error al subir archivos para Code Interpreter.")
        return _json_response(500, {"error": f"{type(exc).__name__}: {exc}"})

    finally:
        if project_client:
            await project_client.close()
        if credential:
            await credential.close()

    return _json_response(
        201,
        {
            "agent_id": agent_id,
            "files": saved_files,
            "agent": agent_record,
        },
    )
