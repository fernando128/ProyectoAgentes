import logging
import mimetypes
from pathlib import Path

import azure.functions as func
from azure.ai.projects.aio import AIProjectClient
from azurefunctions.extensions.http.fastapi import Request, Response

from azure_clients.client_factory import _get_credential
from azure_clients.files import _read_binary_response
from config.settings import ENTORNO, FOUNDRY_ENDPOINT, TOKEN_SCOPE
from utils.cors import _cors_headers
from utils.helpers import _get_route_param, _json_response, _maybe_await


def register_routes(app):
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
