from repositories.database import _load_db, _save_db

def _get_thread_record(thread_id: str) -> dict | None:
    return _load_db().get("threads", {}).get(thread_id)

def _save_thread_record(record: dict) -> dict:
    db = _load_db()
    db.setdefault("threads", {})
    db["threads"][record["thread_id"]] = record
    _save_db(db)
    return record
