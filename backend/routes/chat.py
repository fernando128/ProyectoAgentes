import logging

import azure.functions as func
from azure.ai.projects.aio import AIProjectClient
from azurefunctions.extensions.http.fastapi import Request, Response, StreamingResponse

from azure_clients.client_factory import _get_credential
from azure_clients.streaming import _create_conversation_id
from config.settings import ENTORNO, FOUNDRY_ENDPOINT, TOKEN_SCOPE
from repositories.agent_repository import _get_agent_record
from repositories.thread_repository import (
    _get_thread_record,
    _list_thread_records,
    _save_thread_record,
    _update_thread_record,
)
from services.chat_service import _stream_agent_response
from services.conversation_history_service import _list_conversation_messages
from utils.cors import _cors_headers
from utils.helpers import (
    _get_request_base_url,
    _get_route_param,
    _json_response,
    _request_json,
    _utc_now,
)


def _thread_payload(record: dict, include_messages: bool = False) -> dict:
    payload = {
        "thread_id": record.get("thread_id"),
        "agent_id": record.get("agent_id"),
        "agent_name": record.get("agent_name"),
        "agent_version": record.get("agent_version"),
        "title": record.get("title") or "Nuevo chat",
        "vector_store_id": record.get("vector_store_id"),
        "files": record.get("files", []),
        "code_files": record.get("code_files", []),
        "images": record.get("images", []),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "message_count": record.get("message_count", 0),
    }

    if include_messages:
        payload["messages"] = record.get("messages", [])

    return payload


def register_routes(app):
    @app.route(route="chat-stream", methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS])
    async def chat_stream(req: Request):
        if req.method == "OPTIONS":
            return Response(status_code=204, headers=_cors_headers())

        message = req.query_params.get("message", "").strip()
        thread_id = req.query_params.get("thread_id", "").strip()
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
                    thread_id,
                    agent_id,
                    agent_name,
                    agent_version,
                    _get_request_base_url(req),
                    code_file_ids,
                ),
            media_type="text/event-stream",
            headers=_cors_headers(),
        )

    @app.route(
        route="agents/{agent_id}/threads",
        methods=[func.HttpMethod.GET, func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    )
    async def agent_threads(req: Request):
        if req.method == "OPTIONS":
            return Response(status_code=204, headers=_cors_headers())

        agent_id = _get_route_param(req, "agent_id")

        if not agent_id:
            return _json_response(400, {"error": "Falta agent_id en la ruta."})

        agent_record = _get_agent_record(agent_id)

        if not agent_record:
            return _json_response(404, {"error": "No existe un agente con ese agent_id."})

        if req.method == "GET":
            threads = [
                _thread_payload(record)
                for record in _list_thread_records(agent_id)
            ]
            return _json_response(200, {"agent_id": agent_id, "threads": threads})

        payload, payload_error = await _request_json(req)

        if payload_error:
            return _json_response(400, {"error": payload_error})

        title = str(payload.get("title") or "Nuevo chat").strip() or "Nuevo chat"
        credential = None
        project_client = None

        try:
            credential = _get_credential()
            project_client = AIProjectClient(endpoint=FOUNDRY_ENDPOINT, credential=credential)

            logging.info("Validando credencial de Azure AI. Entorno=%s", ENTORNO)
            await credential.get_token(TOKEN_SCOPE)

            thread_id = await _create_conversation_id(project_client.get_openai_client())

            if not thread_id:
                return _json_response(
                    500,
                    {"error": "No se pudo crear una conversacion en Foundry."},
                )

            now = _utc_now()
            record = {
                "thread_id": thread_id,
                "agent_id": agent_id,
                "agent_name": agent_record.get("agent_name"),
                "agent_version": agent_record.get("agent_version"),
                "title": title,
                "vector_store_id": None,
                "files": [],
                "code_files": [],
                "images": [],
                "created_at": now,
                "updated_at": now,
            }
            _save_thread_record(record)
            return _json_response(201, {"thread": _thread_payload(record, True)})

        except Exception as exc:
            logging.exception("Error al crear thread de agente.")
            return _json_response(500, {"error": f"{type(exc).__name__}: {exc}"})

        finally:
            if project_client:
                await project_client.close()
            if credential:
                await credential.close()

    @app.route(
        route="threads/{thread_id}",
        methods=[func.HttpMethod.GET, func.HttpMethod.PATCH, func.HttpMethod.OPTIONS],
    )
    async def thread_detail(req: Request):
        if req.method == "OPTIONS":
            return Response(status_code=204, headers=_cors_headers())

        thread_id = _get_route_param(req, "thread_id")

        if not thread_id:
            return _json_response(400, {"error": "Falta thread_id en la ruta."})

        record = _get_thread_record(thread_id)

        if not record:
            return _json_response(404, {"error": "No existe un chat con ese thread_id."})

        if req.method == "GET":
            credential = None
            project_client = None

            try:
                credential = _get_credential()
                project_client = AIProjectClient(endpoint=FOUNDRY_ENDPOINT, credential=credential)

                logging.info("Validando credencial de Azure AI. Entorno=%s", ENTORNO)
                await credential.get_token(TOKEN_SCOPE)

                messages = await _list_conversation_messages(
                    project_client.get_openai_client(),
                    thread_id,
                )
                record_with_messages = {
                    **record,
                    "messages": messages,
                    "message_count": len(messages),
                }
                return _json_response(
                    200,
                    {"thread": _thread_payload(record_with_messages, True)},
                )

            except Exception as exc:
                logging.exception("Error al leer historial remoto de conversacion.")
                return _json_response(500, {"error": f"{type(exc).__name__}: {exc}"})

            finally:
                if project_client:
                    await project_client.close()
                if credential:
                    await credential.close()

        payload, payload_error = await _request_json(req)

        if payload_error:
            return _json_response(400, {"error": payload_error})

        updates = {}

        if "title" in payload:
            title = str(payload.get("title") or "").strip()

            if not title:
                return _json_response(400, {"error": "El titulo no puede estar vacio."})

            updates["title"] = title[:120]

        if not updates:
            return _json_response(400, {"error": "No hay campos validos para actualizar."})

        record = _update_thread_record(thread_id, updates)
        return _json_response(200, {"thread": _thread_payload(record, True)})
