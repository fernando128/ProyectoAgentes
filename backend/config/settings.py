import os
from pathlib import Path

TOKEN_SCOPE = "https://ai.azure.com/.default"
DEFAULT_AGENT_NAME = "agente-code-interpreter"
DEFAULT_ALLOWED_ORIGIN = "http://localhost:5173"
DEFAULT_AGENT_INSTRUCTIONS = (
    "Eres un agente util que puede responder preguntas, buscar informacion en "
    "archivos asociados y usar Python para analisis, calculos y transformacion "
    "de datos cuando sea necesario."
)
VISION_IMAGE_INSTRUCTION = (
    "Si el mensaje incluye una imagen, analízala directamente con tu capacidad "
    "de visión; nunca uses code interpreter ni file search para interpretar imágenes."
)
TOOL_USAGE_INSTRUCTIONS = (
    "Capacidades disponibles:\n"
    "- Usa File Search cuando la pregunta dependa de documentos, archivos, "
    "manuales, politicas, contratos, datos cargados o conocimiento privado del "
    "usuario.\n"
    "- Usa Code Interpreter cuando necesites ejecutar Python para calculos, "
    "analisis de datos, transformaciones, tablas, graficos, validaciones o "
    "razonamiento numerico.\n"
    "- Si el usuario pide crear, generar o descargar un archivo, usa Code "
    "Interpreter para escribir el archivo real en /mnt/data. No simules enlaces "
    "sandbox ni digas que un archivo fue creado si no lo escribiste con la "
    "herramienta.\n"
    "- Si la pregunta se responde con conocimiento general y no requiere "
    "archivos ni calculos, responde directamente.\n"
    f"{VISION_IMAGE_INSTRUCTION}"
)
DEFAULT_DB_FILENAME = "agents_db.json"
CODE_INTERPRETER_FILE_INPUT_COUNT = 10
UPLOAD_VECTOR_STORE_INPUT_NAME = "upload_vector_store_id"
IMAGE_MAX_BYTES = 20 * 1024 * 1024
NON_IMAGE_MAX_BYTES = 512 * 1024 * 1024
IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
TABULAR_EXTENSIONS = {".csv", ".xlsx", ".json", ".tsv"}
TABULAR_MIME_TYPES = {
    "application/csv",
    "application/json",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
    "text/tab-separated-values",
}
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".pptx"}
DOCUMENT_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/markdown",
    "text/plain",
}

# Etiqueta de log: "local" o "nube".
ENTORNO = os.getenv("ENTORNO", "local")

# Este es el endpoint del RECURSO DE AZURE AI FOUNDRY, siempre el mismo,
# sin importar si tu backend corre local o desplegado.
FOUNDRY_ENDPOINT = os.getenv(
    "FOUNDRY_ENDPOINT",
    "https://audibotfoundryagentes.services.ai.azure.com/api/projects/AudibotProyectoAgentes",
)
BACKEND_DIR = Path(__file__).resolve().parents[1]
