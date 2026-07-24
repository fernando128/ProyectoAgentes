from repositories.database import _load_db, _save_db
from utils.helpers import _utc_now

def _save_agent_record(record: dict) -> dict:
    db = _load_db()
    db["agents"][record["agent_id"]] = record
    _save_db(db)
    return record

def _get_agent_record(agent_id: str) -> dict | None:
    return _load_db().get("agents", {}).get(agent_id)

def _list_agent_records() -> list[dict]:
    return list(_load_db().get("agents", {}).values())

def _append_file_record(agent_id: str, file_record: dict) -> dict:
    db = _load_db()
    record = db.get("agents", {}).get(agent_id)

    if not record:
        raise KeyError(agent_id)

    record.setdefault("files", []).append(file_record)
    record["updated_at"] = _utc_now()
    db["agents"][agent_id] = record
    _save_db(db)
    return record

def _append_code_file_record(agent_id: str, file_record: dict) -> dict:
    db = _load_db()
    record = db.get("agents", {}).get(agent_id)

    if not record:
        raise KeyError(agent_id)

    record.setdefault("code_files", []).append(file_record)
    record["updated_at"] = _utc_now()
    db["agents"][agent_id] = record
    _save_db(db)
    return record
