import logging
import uuid

import azure.functions as func
from azure.core.exceptions import HttpResponseError
from azurefunctions.extensions.http.fastapi import Request, Response, StreamingResponse

from services.upload_service import _prepare_upload_and_ask_invocation, _stream_prepared_upload_invocation
from utils.cors import _cors_headers
from utils.file_validation import _validate_uploaded_file
from utils.helpers import _get_request_base_url, _get_route_param, _json_response, _utc_now
from utils.multipart import _first_form_value, _read_multipart_form


def register_routes(app):
    @app.route(
        route="upload-and-ask",
        methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    )
    async def upload_and_ask(req: Request):
        if req.method == "OPTIONS":
            return Response(status_code=204, headers=_cors_headers())

        fields, uploaded_files, form_error = await _read_multipart_form(req)

        if form_error:
            return _json_response(400, {"error": form_error})

        message = _first_form_value(fields, "message")
        thread_id = _first_form_value(fields, "thread_id")
        agent_id = _first_form_value(fields, "agent_id")
        agent_name = _first_form_value(fields, "agent_name") or None
        agent_version = _first_form_value(fields, "agent_version") or None

        if not message:
            return _json_response(400, {"error": "Falta el campo multipart 'message'."})

        sanitized_files = []

        for uploaded_file in uploaded_files:
            sanitized_file, validation_error, status_code = _validate_uploaded_file(uploaded_file)

            if validation_error:
                return _json_response(status_code, {"error": validation_error})

            sanitized_files.append(sanitized_file)

        image_files = [file for file in sanitized_files if file["route"] == "image"]
        code_files = [
            file for file in sanitized_files if file["route"] == "code_interpreter"
        ]
        document_files = [file for file in sanitized_files if file["route"] == "file_search"]

        credential = None
        project_client = None
        thread_record = None
        code_file_records = []
        document_file_records = []
        image_file_records = [
            {
                "upload_id": uuid.uuid4().hex,
                "file_id": None,
                "filename": file["filename"],
                "content_type": file["content_type"],
                "bytes": len(file["content"]),
                "route": "vision",
                "destination": "vision",
                "tool": "native_vision",
                "created_at": _utc_now(),
            }
            for file in image_files
        ]

        try:
            invocation = await _prepare_upload_and_ask_invocation(
                message=message,
                thread_id=thread_id,
                agent_id=agent_id,
                agent_name=agent_name,
                agent_version=agent_version,
                image_files=image_files,
                code_files=code_files,
                document_files=document_files,
                image_file_records=image_file_records,
            )

        except ValueError as exc:
            return _json_response(400, {"error": str(exc), "thread_id": thread_id or None})

        except HttpResponseError as exc:
            status_code = getattr(exc, "status_code", None) or 502
            response_status = status_code if 400 <= status_code < 500 else 502
            logging.exception("Error HTTP de Azure AI Foundry en upload-and-ask.")
            return _json_response(
                response_status,
                {
                    "error": getattr(exc, "message", None) or str(exc),
                    "thread_id": thread_id or None,
                },
            )

        except Exception as exc:
            logging.exception("Error al preparar upload-and-ask.")
            return _json_response(
                500,
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "thread_id": thread_id or None,
                },
            )

        return _json_response(
            202,
            {
                "invocation_id": invocation["invocation_id"],
                "thread_id": invocation["thread_id"],
                "agent": invocation["agent_name"],
                "version": invocation["agent_version"],
                "archivos_subidos": invocation["uploaded_resources"],
                "archivos_thread": invocation["thread_resources"],
                "stream_url": f"{_get_request_base_url(req)}/upload-and-ask-stream/{invocation['invocation_id']}",
            },
        )


    @app.route(
        route="upload-and-ask-stream/{invocation_id}",
        methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    )
    async def upload_and_ask_stream(req: Request):
        if req.method == "OPTIONS":
            return Response(status_code=204, headers=_cors_headers())

        invocation_id = _get_route_param(req, "invocation_id")

        return StreamingResponse(
            _stream_prepared_upload_invocation(
                invocation_id=invocation_id,
                download_base_url=_get_request_base_url(req),
            ),
            media_type="text/event-stream",
            headers=_cors_headers(),
        )
