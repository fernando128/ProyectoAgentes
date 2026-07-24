import mimetypes
import re
import uuid
from pathlib import Path

from config.settings import (
    DOCUMENT_EXTENSIONS,
    DOCUMENT_MIME_TYPES,
    IMAGE_EXTENSIONS,
    IMAGE_MAX_BYTES,
    IMAGE_MIME_TYPES,
    NON_IMAGE_MAX_BYTES,
    TABULAR_EXTENSIONS,
    TABULAR_MIME_TYPES,
)

def _clean_filename(filename: str) -> str:
    raw_filename = str(filename or "").replace("\\", "/").split("/")[-1]
    raw_filename = re.sub(r"[\x00-\x1f\x7f]+", "", raw_filename).strip()
    raw_filename = re.sub(r"[^0-9A-Za-z._ -]+", "_", raw_filename)
    raw_filename = re.sub(r"\s+", " ", raw_filename).strip(" ._")

    if not raw_filename:
        raw_filename = f"upload-{uuid.uuid4().hex[:8]}"

    if len(raw_filename) <= 120:
        return raw_filename

    suffix = Path(raw_filename).suffix[:20]
    stem = Path(raw_filename).stem[: 120 - len(suffix)]
    return f"{stem}{suffix}".strip(" ._") or f"upload-{uuid.uuid4().hex[:8]}"

def _effective_content_type(filename: str, content_type: str | None) -> str:
    content_type = (content_type or "").split(";")[0].strip().lower()

    if content_type and content_type != "application/octet-stream":
        return content_type

    guessed_type = mimetypes.guess_type(filename)[0]
    return (guessed_type or "application/octet-stream").lower()

def _route_uploaded_file(uploaded_file: dict) -> str | None:
    filename = uploaded_file["filename"]
    content_type = uploaded_file["content_type"]
    extension = Path(filename).suffix.lower()

    if content_type in IMAGE_MIME_TYPES or extension in IMAGE_EXTENSIONS:
        return "image"

    if extension in TABULAR_EXTENSIONS or content_type in TABULAR_MIME_TYPES:
        return "code_interpreter"

    if extension in DOCUMENT_EXTENSIONS or content_type in DOCUMENT_MIME_TYPES:
        return "file_search"

    return None

def _validate_uploaded_file(uploaded_file: dict) -> tuple[dict | None, str | None, int]:
    sanitized = dict(uploaded_file)
    sanitized["filename"] = _clean_filename(uploaded_file.get("filename", ""))
    sanitized["content_type"] = _effective_content_type(
        sanitized["filename"],
        uploaded_file.get("content_type"),
    )
    sanitized["route"] = _route_uploaded_file(sanitized)

    if not sanitized["route"]:
        return (
            None,
            (
                f"Tipo de archivo no soportado: {sanitized['filename']} "
                f"({sanitized['content_type']})."
            ),
            415,
        )

    size = len(sanitized.get("content") or b"")
    max_size = IMAGE_MAX_BYTES if sanitized["route"] == "image" else NON_IMAGE_MAX_BYTES

    if size > max_size:
        limit_mb = max_size // (1024 * 1024)
        return (
            None,
            f"El archivo {sanitized['filename']} supera el limite de {limit_mb} MB.",
            413,
        )

    return sanitized, None, 0

def _uploaded_file_names(files: list[dict]) -> str:
    return ", ".join(file["filename"] for file in files)

def _routing_context_message(
    image_files: list[dict],
    code_files: list[dict],
    document_files: list[dict],
) -> str:
    instructions = []

    if image_files:
        instructions.append(
            "Imagenes adjuntas para vision nativa: "
            f"{_uploaded_file_names(image_files)}. Analizalas directamente con vision; "
            "no uses herramientas para interpretar imagenes."
        )

    if document_files:
        instructions.append(
            "Documentos adjuntos para File Search: "
            f"{_uploaded_file_names(document_files)}. Para responder sobre estos "
            "documentos, usa File Search y busca en el vector store asociado al thread. "
            "No uses Code Interpreter para leer, resumir o interpretar estos documentos."
        )

    if code_files:
        instructions.append(
            "Archivos tabulares adjuntos para Code Interpreter: "
            f"{_uploaded_file_names(code_files)}. Usa Code Interpreter si necesitas "
            "calculos, transformaciones, tablas o graficos sobre esos datos."
        )

    if not instructions:
        return ""

    return "\n\nContexto de archivos adjuntos:\n- " + "\n- ".join(instructions)
