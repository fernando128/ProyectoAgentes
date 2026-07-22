import json
import logging
import os
from collections.abc import AsyncIterator

import azure.functions as func
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import AzureCliCredential
from azure.identity.aio import DefaultAzureCredential
from azurefunctions.extensions.http.fastapi import Request, Response, StreamingResponse

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

TOKEN_SCOPE = "https://ai.azure.com/.default"
DEFAULT_AGENT_NAME = "agente-code-interpreter"
DEFAULT_ALLOWED_ORIGIN = "http://localhost:5173"

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
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


def _sse(data: str, event: str | None = None) -> str:
    lines = []

    if event:
        lines.append(f"event: {event}")

    for line in data.splitlines() or [""]:
        lines.append(f"data: {line}")

    return "\n".join(lines) + "\n\n"


def _get_agent_version(agent) -> str | None:
    versions = getattr(agent, "versions", None)
    latest = getattr(versions, "latest", None) if versions else None
    return getattr(latest, "version", None) if latest else None


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
        stream = await openai_client.responses.create(
            input=[{"role": "user", "content": message}],
            stream=True,
            extra_body={
                "agent_reference": {
                    "name": agent_name,
                    "version": resolved_version,
                    "type": "agent_reference",
                }
            },
        )

        async for event in stream:
            event_type = getattr(event, "type", "")

            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "")

                if delta:
                    yield _sse(delta)

            elif event_type == "response.completed":
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
    agent_name = req.query_params.get("agent_name", "").strip() or None
    agent_version = req.query_params.get("agent_version", "").strip() or None

    return StreamingResponse(
        _stream_agent_response(message, agent_name, agent_version),
        media_type="text/event-stream",
        headers=_cors_headers(),
    )


@app.route(route="agents", methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS])
async def list_agents(req: Request):
    if req.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())

    endpoint = FOUNDRY_ENDPOINT

    credential = None
    project_client = None
    agents_payload = []

    try:
        credential = _get_credential()
        project_client = AIProjectClient(endpoint=endpoint, credential=credential)

        logging.info("Validando credencial de Azure AI. Entorno=%s", ENTORNO)
        await credential.get_token(TOKEN_SCOPE)

        async for agent in project_client.agents.list():
            agents_payload.append({
                "name": getattr(agent, "name", None),
                "version": _get_agent_version(agent),
            })

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