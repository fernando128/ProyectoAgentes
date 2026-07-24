import json
import logging
import os
from collections.abc import AsyncIterator

from azure.ai.projects.aio import AIProjectClient

from azure_clients.agents import _resolve_agent_version
from azure_clients.client_factory import _get_credential
from azure_clients.streaming import _create_conversation_id
from config.settings import (
    CODE_INTERPRETER_FILE_INPUT_COUNT,
    DEFAULT_AGENT_NAME,
    ENTORNO,
    FOUNDRY_ENDPOINT,
    TOKEN_SCOPE,
)
from services.agent_service import _should_require_code_interpreter, _tool_enforced_message
from services.artifact_service import (
    _download_markdown_link,
    _generated_file_payload,
    _iter_annotations,
    _resolve_sandbox_file_payloads,
    _sandbox_filenames,
)
from utils.helpers import _find_first_value
from utils.sse import _sse

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
