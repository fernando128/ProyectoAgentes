import azure.functions as func

from routes.agents import register_routes as register_agent_routes
from routes.chat import register_routes as register_chat_routes
from routes.generated_files import register_routes as register_generated_file_routes
from routes.uploads import register_routes as register_upload_routes

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

register_chat_routes(app)
register_upload_routes(app)
register_generated_file_routes(app)
register_agent_routes(app)
