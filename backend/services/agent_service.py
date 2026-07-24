import os
import re
import uuid
from io import BytesIO

from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import AutoCodeInterpreterToolParam
from azure.ai.projects.models import CodeInterpreterTool
from azure.ai.projects.models import FileSearchTool
from azure.ai.projects.models import PromptAgentDefinition
try:
    from azure.ai.projects.models import StructuredInputDefinition
except ImportError:
    StructuredInputDefinition = None

from azure_clients.agents import _get_agent_version
from config.settings import (
    CODE_INTERPRETER_FILE_INPUT_COUNT,
    DEFAULT_AGENT_INSTRUCTIONS,
    DEFAULT_AGENT_NAME,
    TOOL_USAGE_INSTRUCTIONS,
    UPLOAD_VECTOR_STORE_INPUT_NAME,
    VISION_IMAGE_INSTRUCTION,
)
from repositories.agent_repository import _save_agent_record
from utils.helpers import _maybe_await, _utc_now

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

    if "Capacidades disponibles:" not in instructions:
        instructions = f"{instructions}\n\n{TOOL_USAGE_INSTRUCTIONS}"

    if VISION_IMAGE_INSTRUCTION not in instructions:
        instructions = f"{instructions.rstrip()}\n{VISION_IMAGE_INSTRUCTION}"

    return instructions

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

def _template_input(name: str) -> str:
    return "{{" + name + "}}"

def _structured_input_definition(description: str, required: bool = False):
    schema = {"type": "string"}

    if StructuredInputDefinition:
        return StructuredInputDefinition(
            description=description,
            required=required,
            default_value="",
            schema=schema,
        )

    return {
        "description": description,
        "required": required,
        "default_value": "",
        "schema": schema,
    }

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

def _build_unified_upload_tools(
    vector_store_ids: list[str] | None = None,
    code_file_ids: list[str] | None = None,
) -> list:
    vector_store_ids = list(dict.fromkeys(vector_store_ids or []))
    code_file_ids = list(dict.fromkeys(code_file_ids or []))
    code_placeholders = [
        _template_input(_code_input_name(index))
        for index in range(CODE_INTERPRETER_FILE_INPUT_COUNT)
    ]
    dynamic_vector_store_ids = vector_store_ids + [
        _template_input(UPLOAD_VECTOR_STORE_INPUT_NAME)
    ]

    return [
        CodeInterpreterTool(
            container=AutoCodeInterpreterToolParam(
                file_ids=(
                    code_file_ids[:CODE_INTERPRETER_FILE_INPUT_COUNT]
                    + code_placeholders
                )
            )
        ),
        FileSearchTool(vector_store_ids=dynamic_vector_store_ids),
    ]

def _build_unified_structured_inputs() -> dict:
    structured_inputs = {
        UPLOAD_VECTOR_STORE_INPUT_NAME: _structured_input_definition(
            "Vector store ID asociado al thread para File Search."
        )
    }

    for index in range(CODE_INTERPRETER_FILE_INPUT_COUNT):
        structured_inputs[_code_input_name(index)] = _structured_input_definition(
            f"File ID {index + 1} para Code Interpreter."
        )

    return structured_inputs

async def _ensure_unified_upload_agent(
    project_client: AIProjectClient,
    agent_record: dict,
) -> dict:
    instructions = _build_agent_instructions(agent_record.get("instructions", ""))
    needs_update = (
        not agent_record.get("supports_unified_upload")
        or instructions != agent_record.get("instructions")
    )

    if not needs_update:
        return agent_record

    code_file_ids = [
        file_record.get("file_id")
        for file_record in agent_record.get("code_files", [])
        if file_record.get("file_id")
    ]
    vector_store_ids = _as_string_list(
        agent_record.get("vector_store_ids") or agent_record.get("vector_store_id")
    )
    tools = _build_unified_upload_tools(
        vector_store_ids=vector_store_ids,
        code_file_ids=code_file_ids,
    )

    agent = await _maybe_await(
        project_client.agents.create_version(
            agent_name=agent_record["agent_name"],
            definition=PromptAgentDefinition(
                model=agent_record["model"],
                instructions=instructions,
                tools=tools,
                structured_inputs=_build_unified_structured_inputs(),
            ),
            description=agent_record.get("description")
            or "Agente actualizado para subida unificada de archivos.",
        )
    )
    agent_record["agent_version"] = getattr(agent, "version", None) or _get_agent_version(agent)
    agent_record["instructions"] = instructions
    agent_record["supports_unified_upload"] = True
    agent_record["updated_at"] = _utc_now()
    _save_agent_record(agent_record)
    return agent_record

async def _create_vector_store(openai_client, name: str):
    create_kwargs = {
        "name": name,
        "expires_after": {"anchor": "last_active_at", "days": 7},
    }

    try:
        return await _maybe_await(openai_client.vector_stores.create(**create_kwargs))
    except TypeError:
        create_kwargs.pop("expires_after", None)
        return await _maybe_await(openai_client.vector_stores.create(**create_kwargs))

async def _upload_to_code_interpreter(openai_client, uploaded_file: dict) -> dict:
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

    return {
        "upload_id": uuid.uuid4().hex,
        "file_id": getattr(uploaded, "id", None),
        "filename": uploaded_file["filename"],
        "content_type": uploaded_file["content_type"],
        "bytes": len(uploaded_file["content"]),
        "purpose": "assistants",
        "route": "code_interpreter",
        "destination": "code_interpreter",
        "tool": "code_interpreter",
        "created_at": _utc_now(),
    }

async def _upload_to_vector_store(
    openai_client,
    vector_store_id: str,
    uploaded_file: dict,
) -> dict:
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

    return {
        "upload_id": uuid.uuid4().hex,
        "file_id": getattr(vector_store_file, "id", None),
        "vector_store_id": getattr(vector_store_file, "vector_store_id", vector_store_id),
        "filename": uploaded_file["filename"],
        "content_type": uploaded_file["content_type"],
        "bytes": len(uploaded_file["content"]),
        "status": getattr(vector_store_file, "status", None),
        "route": "file_search",
        "destination": "file_search",
        "tool": "file_search",
        "created_at": _utc_now(),
    }

def _uploaded_resource_payload(file_record: dict, fallback_destination: str) -> dict:
    destination = file_record.get("destination") or file_record.get("route") or fallback_destination
    file_id = file_record.get("file_id")
    upload_id = file_record.get("upload_id") or uuid.uuid4().hex

    return {
        "id": file_id or upload_id,
        "upload_id": upload_id,
        "file_id": file_id,
        "filename": file_record.get("filename"),
        "content_type": file_record.get("content_type"),
        "bytes": file_record.get("bytes"),
        "destination": destination,
        "tool": file_record.get("tool") or destination,
        "vector_store_id": file_record.get("vector_store_id"),
        "status": file_record.get("status"),
        "purpose": file_record.get("purpose"),
        "created_at": file_record.get("created_at"),
    }

def _thread_resource_payloads(thread_record: dict) -> list[dict]:
    resources = []

    for file_record in thread_record.get("files", []):
        resources.append(_uploaded_resource_payload(file_record, "file_search"))

    for file_record in thread_record.get("code_files", []):
        resources.append(_uploaded_resource_payload(file_record, "code_interpreter"))

    for file_record in thread_record.get("images", []):
        resources.append(_uploaded_resource_payload(file_record, "vision"))

    return resources
