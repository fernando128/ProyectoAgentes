import azure.functions as func
from azurefunctions.extensions.http.fastapi import Request, Response, StreamingResponse

from repositories.agent_repository import _get_agent_record
from services.chat_service import _stream_agent_response
from utils.cors import _cors_headers
from utils.helpers import _get_request_base_url, _json_response


def register_routes(app):
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
