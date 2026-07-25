import base64
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator

from azure.ai.projects.aio import AIProjectClient
from azure.core.exceptions import HttpResponseError

from azure_clients.agents import _resolve_agent_version
from azure_clients.client_factory import _get_credential
from azure_clients.streaming import _create_conversation_id
from config.settings import (
    CODE_INTERPRETER_FILE_INPUT_COUNT,
    DEFAULT_AGENT_NAME,
    ENTORNO,
    FOUNDRY_ENDPOINT,
    TOKEN_SCOPE,
    UPLOAD_VECTOR_STORE_INPUT_NAME,
)
from repositories.agent_repository import _get_agent_record
from repositories.invocation_repository import (
    _delete_invocation_record,
    _get_invocation_record,
    _save_invocation_record,
)
from repositories.thread_repository import _get_thread_record, _save_thread_record
from services.agent_service import (
    _code_input_name,
    _create_vector_store,
    _ensure_unified_upload_agent,
    _thread_resource_payloads,
    _tool_enforced_message,
    _upload_to_code_interpreter,
    _upload_to_vector_store,
    _uploaded_resource_payload,
)
from services.artifact_service import (
    _generated_file_payload,
    _iter_annotations,
    _resolve_sandbox_file_payloads,
    _sandbox_filenames,
)
from utils.file_validation import _routing_context_message
from utils.helpers import _find_first_value, _maybe_await, _utc_now
from utils.sse import _sse

async def _stream_upload_and_ask_response(
    message: str,
    thread_id: str,
    agent_id: str,
    agent_name: str | None,
    agent_version: str | None,
    image_files: list[dict],
    code_files: list[dict],
    document_files: list[dict],
    image_file_records: list[dict],
    download_base_url: str,
) -> AsyncIterator[str]:
    credential = None
    project_client = None

    try:
        credential = _get_credential()
        project_client = AIProjectClient(endpoint=FOUNDRY_ENDPOINT, credential=credential)

        logging.info("Validando credencial de Azure AI. Entorno=%s", ENTORNO)
        await credential.get_token(TOKEN_SCOPE)

        openai_client = project_client.get_openai_client()

        if agent_id:
            agent_record = _get_agent_record(agent_id)

            if not agent_record:
                yield _sse("No existe un agente con ese agent_id.", "agent-error")
                return

            agent_record = await _ensure_unified_upload_agent(
                project_client,
                agent_record,
            )
            agent_name = agent_record.get("agent_name")
            agent_version = agent_record.get("agent_version")

        agent_name = (
            agent_name
            or os.getenv("AZURE_AI_AGENT_NAME", DEFAULT_AGENT_NAME)
        ).strip()
        agent_version = (
            agent_version
            or os.getenv("AZURE_AI_AGENT_VERSION", "")
        ).strip() or None
        auto_resolve_agent_version = (
            os.getenv("AZURE_AI_AUTO_RESOLVE_AGENT_VERSION", "false").strip().lower()
            == "true"
        )
        resolved_version = await _resolve_agent_version(
            client=project_client,
            agent_name=agent_name,
            configured_version=agent_version,
            auto_resolve=auto_resolve_agent_version,
        )

        if not thread_id:
            thread_id = await _create_conversation_id(openai_client)

            if not thread_id:
                yield _sse(
                    "No se pudo crear una conversacion en Foundry para devolver thread_id.",
                    "agent-error",
                )
                return

        thread_record = _get_thread_record(thread_id) or {
            "thread_id": thread_id,
            "agent_id": agent_id or None,
            "agent_name": agent_name,
            "agent_version": resolved_version,
            "title": message.strip().replace("\n", " ")[:80] or "Nuevo chat",
            "vector_store_id": None,
            "files": [],
            "code_files": [],
            "images": [],
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }

        if (
            thread_record.get("agent_id")
            and agent_id
            and thread_record.get("agent_id") != agent_id
        ):
            yield _sse("El thread_id no pertenece al agent_id enviado.", "agent-error")
            return

        existing_code_file_ids = [
            file_record.get("file_id")
            for file_record in thread_record.get("code_files", [])
            if file_record.get("file_id")
        ]
        available_code_slots = CODE_INTERPRETER_FILE_INPUT_COUNT - len(existing_code_file_ids)

        if len(code_files) > available_code_slots:
            yield _sse(
                (
                    f"El thread ya tiene {len(existing_code_file_ids)} archivos para "
                    f"Code Interpreter. Solo quedan {available_code_slots} espacios disponibles."
                ),
                "agent-error",
            )
            return

        vector_store_id = thread_record.get("vector_store_id")

        if document_files and not vector_store_id:
            vector_store = await _create_vector_store(
                openai_client,
                f"upload-thread-{thread_id[:40]}",
            )
            vector_store_id = getattr(vector_store, "id", None)
            thread_record["vector_store_id"] = vector_store_id

        code_file_records = []
        document_file_records = []

        for uploaded_file in code_files:
            code_file_records.append(
                await _upload_to_code_interpreter(openai_client, uploaded_file)
            )

        for uploaded_file in document_files:
            document_file_records.append(
                await _upload_to_vector_store(
                    openai_client,
                    vector_store_id,
                    uploaded_file,
                )
            )

        thread_record.setdefault("code_files", []).extend(code_file_records)
        thread_record.setdefault("files", []).extend(document_file_records)
        thread_record.setdefault("images", []).extend(image_file_records)
        thread_record["agent_id"] = agent_id or thread_record.get("agent_id")
        thread_record["agent_name"] = agent_name
        thread_record["agent_version"] = resolved_version
        thread_record["updated_at"] = _utc_now()
        _save_thread_record(thread_record)

        runtime_code_file_ids = [
            file_record.get("file_id")
            for file_record in thread_record.get("code_files", [])
            if file_record.get("file_id")
        ][:CODE_INTERPRETER_FILE_INPUT_COUNT]
        structured_inputs = {
            UPLOAD_VECTOR_STORE_INPUT_NAME: vector_store_id or "",
        }

        for index in range(CODE_INTERPRETER_FILE_INPUT_COUNT):
            structured_inputs[_code_input_name(index)] = (
                runtime_code_file_ids[index]
                if index < len(runtime_code_file_ids)
                else ""
            )

        uploaded_resources = (
            [_uploaded_resource_payload(file_record, "file_search") for file_record in document_file_records]
            + [_uploaded_resource_payload(file_record, "code_interpreter") for file_record in code_file_records]
            + [_uploaded_resource_payload(file_record, "vision") for file_record in image_file_records]
        )
        thread_resources = _thread_resource_payloads(thread_record)

        yield _sse(
            json.dumps(
                {
                    "thread_id": thread_id,
                    "agent": agent_name,
                    "version": resolved_version,
                    "archivos_subidos": uploaded_resources,
                    "archivos_thread": thread_resources,
                },
                ensure_ascii=False,
            ),
            "metadata",
        )
        yield _sse(
            json.dumps(
                {
                    "archivos_subidos": uploaded_resources,
                    "archivos_thread": thread_resources,
                },
                ensure_ascii=False,
            ),
            "uploaded-files",
        )

        content_blocks = [
            {
                "type": "input_text",
                "text": (
                    f"{_tool_enforced_message(message)}"
                    f"{_routing_context_message(image_files, code_files, document_files)}"
                ),
            }
        ]

        for uploaded_file in image_files:
            encoded_image = base64.b64encode(uploaded_file["content"]).decode("ascii")
            content_blocks.append(
                {
                    "type": "input_image",
                    "image_url": (
                        f"data:{uploaded_file['content_type']};base64,{encoded_image}"
                    ),
                    "detail": "auto",
                }
            )

        response_kwargs = {
            "conversation": thread_id,
            "input": [{"role": "user", "content": content_blocks}],
            "stream": True,
            "extra_body": {
                "agent_reference": {
                    "name": agent_name,
                    "version": resolved_version,
                    "type": "agent_reference",
                },
                "structured_inputs": structured_inputs,
            },
        }

        if code_file_records:
            response_kwargs["tool_choice"] = "required"

        stream = await _maybe_await(openai_client.responses.create(**response_kwargs))
        generated_files_seen = set()
        active_container_id = None
        output_text_parts = []
        run_id = None

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
                yield _sse(json.dumps(file_payload, ensure_ascii=False), "artifact")

            elif event_type == "response.completed":
                response = getattr(event, "response", None)
                run_id = getattr(response, "id", None)
                response_container_id = _find_first_value(response, "container_id")

                if response_container_id:
                    active_container_id = str(response_container_id)

                for annotation in _iter_annotations(response):
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

                yield _sse(
                    json.dumps(
                        {
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "archivos_subidos": uploaded_resources,
                            "archivos_thread": thread_resources,
                        },
                        ensure_ascii=False,
                    ),
                    "done",
                )
                break

            elif event_type == "response.failed":
                error = getattr(event, "error", None)
                error_message = getattr(error, "message", None) or "Falló la respuesta."
                logging.error("El agente devolvio error en upload-and-ask: %s", error_message)
                yield _sse(error_message, "agent-error")
                break

    except HttpResponseError as exc:
        logging.exception("Error HTTP de Azure AI Foundry en upload-and-ask.")
        yield _sse(getattr(exc, "message", None) or str(exc), "agent-error")

    except Exception as exc:
        logging.exception("Error al ejecutar upload-and-ask.")
        yield _sse(f"{type(exc).__name__}: {exc}", "agent-error")

    finally:
        if project_client:
            await project_client.close()
        if credential:
            await credential.close()

        yield _sse("[FIN]")

async def _prepare_upload_and_ask_invocation(
    message: str,
    thread_id: str,
    agent_id: str,
    agent_name: str | None,
    agent_version: str | None,
    image_files: list[dict],
    code_files: list[dict],
    document_files: list[dict],
    image_file_records: list[dict],
) -> dict:
    credential = None
    project_client = None

    try:
        credential = _get_credential()
        project_client = AIProjectClient(endpoint=FOUNDRY_ENDPOINT, credential=credential)

        logging.info("Validando credencial de Azure AI. Entorno=%s", ENTORNO)
        await credential.get_token(TOKEN_SCOPE)

        openai_client = project_client.get_openai_client()

        if agent_id:
            agent_record = _get_agent_record(agent_id)

            if not agent_record:
                raise ValueError("No existe un agente con ese agent_id.")

            agent_record = await _ensure_unified_upload_agent(
                project_client,
                agent_record,
            )
            agent_name = agent_record.get("agent_name")
            agent_version = agent_record.get("agent_version")

        agent_name = (
            agent_name
            or os.getenv("AZURE_AI_AGENT_NAME", DEFAULT_AGENT_NAME)
        ).strip()
        agent_version = (
            agent_version
            or os.getenv("AZURE_AI_AGENT_VERSION", "")
        ).strip() or None
        auto_resolve_agent_version = (
            os.getenv("AZURE_AI_AUTO_RESOLVE_AGENT_VERSION", "false").strip().lower()
            == "true"
        )
        resolved_version = await _resolve_agent_version(
            client=project_client,
            agent_name=agent_name,
            configured_version=agent_version,
            auto_resolve=auto_resolve_agent_version,
        )

        if not thread_id:
            thread_id = await _create_conversation_id(openai_client)

            if not thread_id:
                raise RuntimeError(
                    "No se pudo crear una conversacion en Foundry para devolver thread_id."
                )

        thread_record = _get_thread_record(thread_id) or {
            "thread_id": thread_id,
            "agent_id": agent_id or None,
            "agent_name": agent_name,
            "agent_version": resolved_version,
            "title": message.strip().replace("\n", " ")[:80] or "Nuevo chat",
            "vector_store_id": None,
            "files": [],
            "code_files": [],
            "images": [],
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }

        if (
            thread_record.get("agent_id")
            and agent_id
            and thread_record.get("agent_id") != agent_id
        ):
            raise ValueError("El thread_id no pertenece al agent_id enviado.")

        existing_code_file_ids = [
            file_record.get("file_id")
            for file_record in thread_record.get("code_files", [])
            if file_record.get("file_id")
        ]
        available_code_slots = CODE_INTERPRETER_FILE_INPUT_COUNT - len(existing_code_file_ids)

        if len(code_files) > available_code_slots:
            raise ValueError(
                f"El thread ya tiene {len(existing_code_file_ids)} archivos para "
                f"Code Interpreter. Solo quedan {available_code_slots} espacios disponibles."
            )

        vector_store_id = thread_record.get("vector_store_id")

        if document_files and not vector_store_id:
            vector_store = await _create_vector_store(
                openai_client,
                f"upload-thread-{thread_id[:40]}",
            )
            vector_store_id = getattr(vector_store, "id", None)
            thread_record["vector_store_id"] = vector_store_id

        code_file_records = []
        document_file_records = []

        for uploaded_file in code_files:
            code_file_records.append(
                await _upload_to_code_interpreter(openai_client, uploaded_file)
            )

        for uploaded_file in document_files:
            document_file_records.append(
                await _upload_to_vector_store(
                    openai_client,
                    vector_store_id,
                    uploaded_file,
                )
            )

        thread_record.setdefault("code_files", []).extend(code_file_records)
        thread_record.setdefault("files", []).extend(document_file_records)
        thread_record.setdefault("images", []).extend(image_file_records)
        thread_record["agent_id"] = agent_id or thread_record.get("agent_id")
        thread_record["agent_name"] = agent_name
        thread_record["agent_version"] = resolved_version
        thread_record["updated_at"] = _utc_now()
        _save_thread_record(thread_record)

        runtime_code_file_ids = [
            file_record.get("file_id")
            for file_record in thread_record.get("code_files", [])
            if file_record.get("file_id")
        ][:CODE_INTERPRETER_FILE_INPUT_COUNT]
        structured_inputs = {
            UPLOAD_VECTOR_STORE_INPUT_NAME: vector_store_id or "",
        }

        for index in range(CODE_INTERPRETER_FILE_INPUT_COUNT):
            structured_inputs[_code_input_name(index)] = (
                runtime_code_file_ids[index]
                if index < len(runtime_code_file_ids)
                else ""
            )

        uploaded_resources = (
            [_uploaded_resource_payload(file_record, "file_search") for file_record in document_file_records]
            + [_uploaded_resource_payload(file_record, "code_interpreter") for file_record in code_file_records]
            + [_uploaded_resource_payload(file_record, "vision") for file_record in image_file_records]
        )
        thread_resources = _thread_resource_payloads(thread_record)
        image_inputs = []

        for uploaded_file in image_files:
            encoded_image = base64.b64encode(uploaded_file["content"]).decode("ascii")
            image_inputs.append(
                {
                    "filename": uploaded_file["filename"],
                    "content_type": uploaded_file["content_type"],
                    "data_uri": (
                        f"data:{uploaded_file['content_type']};base64,{encoded_image}"
                    ),
                }
            )

        invocation_record = {
            "invocation_id": uuid.uuid4().hex,
            "message": message,
            "thread_id": thread_id,
            "agent_id": agent_id or None,
            "agent_name": agent_name,
            "agent_version": resolved_version,
            "structured_inputs": structured_inputs,
            "image_inputs": image_inputs,
            "image_files": [
                {key: file[key] for key in ("filename", "content_type", "route")}
                for file in image_files
            ],
            "code_files": [
                {key: file[key] for key in ("filename", "content_type", "route")}
                for file in code_files
            ],
            "document_files": [
                {key: file[key] for key in ("filename", "content_type", "route")}
                for file in document_files
            ],
            "uploaded_resources": uploaded_resources,
            "thread_resources": thread_resources,
            "created_at": _utc_now(),
        }
        _save_invocation_record(invocation_record)
        return invocation_record

    finally:
        if project_client:
            await project_client.close()
        if credential:
            await credential.close()

async def _stream_prepared_upload_invocation(
    invocation_id: str,
    download_base_url: str,
) -> AsyncIterator[str]:
    invocation = _get_invocation_record(invocation_id)

    if not invocation:
        yield _sse("No existe una invocacion preparada con ese invocation_id.", "agent-error")
        yield _sse("[FIN]")
        return

    credential = None
    project_client = None

    try:
        credential = _get_credential()
        project_client = AIProjectClient(endpoint=FOUNDRY_ENDPOINT, credential=credential)

        await credential.get_token(TOKEN_SCOPE)
        openai_client = project_client.get_openai_client()
        thread_id = invocation["thread_id"]
        agent_name = invocation["agent_name"]
        agent_version = invocation["agent_version"]
        uploaded_resources = invocation.get("uploaded_resources", [])
        thread_resources = invocation.get("thread_resources", [])

        yield _sse(
            json.dumps(
                {
                    "thread_id": thread_id,
                    "agent": agent_name,
                    "version": agent_version,
                    "archivos_subidos": uploaded_resources,
                    "archivos_thread": thread_resources,
                },
                ensure_ascii=False,
            ),
            "metadata",
        )
        yield _sse(
            json.dumps(
                {
                    "archivos_subidos": uploaded_resources,
                    "archivos_thread": thread_resources,
                },
                ensure_ascii=False,
            ),
            "uploaded-files",
        )

        content_blocks = [
            {
                "type": "input_text",
                "text": (
                    f"{_tool_enforced_message(invocation['message'])}"
                    f"{_routing_context_message(invocation.get('image_files', []), invocation.get('code_files', []), invocation.get('document_files', []))}"
                ),
            }
        ]

        for image_input in invocation.get("image_inputs", []):
            content_blocks.append(
                {
                    "type": "input_image",
                    "image_url": image_input["data_uri"],
                    "detail": "auto",
                }
            )

        response_kwargs = {
            "conversation": thread_id,
            "input": [{"role": "user", "content": content_blocks}],
            "stream": True,
            "extra_body": {
                "agent_reference": {
                    "name": agent_name,
                    "version": agent_version,
                    "type": "agent_reference",
                },
                "structured_inputs": invocation.get("structured_inputs", {}),
            },
        }

        if invocation.get("code_files"):
            response_kwargs["tool_choice"] = "required"

        stream = await _maybe_await(openai_client.responses.create(**response_kwargs))
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

                file_key = (file_payload["container_id"], file_payload["file_id"])

                if file_key in generated_files_seen:
                    continue

                generated_files_seen.add(file_key)
                yield _sse(json.dumps(file_payload, ensure_ascii=False), "artifact")

            elif event_type == "response.completed":
                response = getattr(event, "response", None)
                run_id = getattr(response, "id", None)
                response_container_id = _find_first_value(response, "container_id")

                if response_container_id:
                    active_container_id = str(response_container_id)

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
                    yield _sse(json.dumps(file_payload, ensure_ascii=False), "artifact")

                sandbox_payloads = await _resolve_sandbox_file_payloads(
                    openai_client,
                    active_container_id,
                    _sandbox_filenames("".join(output_text_parts)),
                    download_base_url,
                )

                for file_payload in sandbox_payloads:
                    file_key = (file_payload["container_id"], file_payload["file_id"])

                    if file_key in generated_files_seen:
                        continue

                    generated_files_seen.add(file_key)
                    yield _sse(json.dumps(file_payload, ensure_ascii=False), "artifact")

                yield _sse(
                    json.dumps(
                        {
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "archivos_subidos": uploaded_resources,
                            "archivos_thread": thread_resources,
                        },
                        ensure_ascii=False,
                    ),
                    "done",
                )
                break

            elif event_type == "response.failed":
                error = getattr(event, "error", None)
                yield _sse(getattr(error, "message", None) or "Falló la respuesta.", "agent-error")
                break

    except Exception as exc:
        logging.exception("Error al streamear upload-and-ask.")
        yield _sse(f"{type(exc).__name__}: {exc}", "agent-error")

    finally:
        _delete_invocation_record(invocation_id)

        if project_client:
            await project_client.close()
        if credential:
            await credential.close()

        yield _sse("[FIN]")
