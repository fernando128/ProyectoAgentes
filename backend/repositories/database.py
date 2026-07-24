import json
import os
import uuid
from pathlib import Path

from config.settings import BACKEND_DIR, DEFAULT_DB_FILENAME


def _db_path() -> Path:
    configured_path = os.getenv("AGENTS_DB_PATH", "").strip()

    if configured_path:
        path = Path(configured_path)
        if path.is_absolute():
            return path

        return BACKEND_DIR / path

    return BACKEND_DIR / DEFAULT_DB_FILENAME

def _empty_db() -> dict:
    return {"agents": {}, "threads": {}, "invocations": {}}

def _load_db() -> dict:
    path = _db_path()

    if not path.exists():
        return _empty_db()

    with path.open("r", encoding="utf-8") as db_file:
        data = json.load(db_file)

    if not isinstance(data, dict):
        return _empty_db()

    data.setdefault("agents", {})
    data.setdefault("threads", {})
    data.setdefault("invocations", {})
    return data

def _save_db(data: dict) -> None:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")

    with temp_path.open("w", encoding="utf-8") as db_file:
        json.dump(data, db_file, ensure_ascii=False, indent=2)

    os.replace(temp_path, path)
