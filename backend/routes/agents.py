import json
import logging
import uuid
from io import BytesIO

import azure.functions as func
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azurefunctions.extensions.http.fastapi import Request, Response

from azure_clients.agents import _get_agent_payload, _get_agent_version
from azure_clients.client_factory import _get_credential
from config.settings import (
    CODE_INTERPRETER_FILE_INPUT_COUNT,
    DEFAULT_AGENT_INSTRUCTIONS,
    DEFAULT_AGENT_NAME,
    ENTORNO,
    FOUNDRY_ENDPOINT,
    TOKEN_SCOPE,
)
from repositories.agent_repository import (
    _append_file_record,
    _get_agent_record,
    _list_agent_records,
    _save_agent_record,
)
from services.agent_service import (
    _as_bool,
    _as_string_list,
    _build_agent_instructions,
    _build_agent_tools,
    _build_unified_structured_inputs,
    _build_unified_upload_tools,
    _get_model_deployment,
    _unique_agent_name,
)
from utils.cors import _cors_headers
from utils.helpers import _get_route_param, _json_response, _maybe_await, _request_json, _utc_now
from utils.multipart import _read_uploaded_files


def register_routes(app):
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

            prompt_definition_kwargs = {
                "model": model,
                "instructions": instructions,
            }

            if enable_code_interpreter and enable_file_search:
                tools = _build_unified_upload_tools(vector_store_ids=vector_store_ids)
                prompt_definition_kwargs["structured_inputs"] = (
                    _build_unified_structured_inputs()
                )
                supports_unified_upload = True
            else:
                tools = _build_agent_tools(
                    enable_code_interpreter=enable_code_interpreter,
                    enable_file_search=enable_file_search,
                    vector_store_ids=vector_store_ids,
                )
                supports_unified_upload = False

            prompt_definition_kwargs["tools"] = tools

            agent = await _maybe_await(
                project_client.agents.create_version(
                    agent_name=agent_name,
                    definition=PromptAgentDefinition(**prompt_definition_kwargs),
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
                "supports_unified_upload": supports_unified_upload,
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
            agent_record["supports_unified_upload"] = False
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
