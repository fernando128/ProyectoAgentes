from repositories.database import _load_db, _save_db
from utils.helpers import _utc_now

def _get_thread_record(thread_id: str) -> dict | None:
    return _load_db().get("threads", {}).get(thread_id)

def _list_thread_records(agent_id: str | None = None) -> list[dict]:
    records = list(_load_db().get("threads", {}).values())

    if agent_id:
        records = [
            record
            for record in records
            if record.get("agent_id") == agent_id
        ]

    return sorted(
        records,
        key=lambda record: record.get("updated_at") or record.get("created_at") or "",
        reverse=True,
    )

def _save_thread_record(record: dict) -> dict:
    db = _load_db()
    db.setdefault("threads", {})
    db["threads"][record["thread_id"]] = record
    _save_db(db)
    return record

def _update_thread_record(thread_id: str, updates: dict) -> dict:
    db = _load_db()
    record = db.setdefault("threads", {}).get(thread_id)

    if not record:
        raise KeyError(thread_id)

    record.update(updates)
    record["updated_at"] = _utc_now()
    db["threads"][thread_id] = record
    _save_db(db)
    return record
