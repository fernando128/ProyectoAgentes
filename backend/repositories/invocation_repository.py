from repositories.database import _load_db, _save_db

def _get_invocation_record(invocation_id: str) -> dict | None:
    return _load_db().get("invocations", {}).get(invocation_id)

def _save_invocation_record(record: dict) -> dict:
    db = _load_db()
    db.setdefault("invocations", {})
    db["invocations"][record["invocation_id"]] = record
    _save_db(db)
    return record

def _delete_invocation_record(invocation_id: str) -> None:
    db = _load_db()
    invocations = db.setdefault("invocations", {})

    if invocation_id in invocations:
        del invocations[invocation_id]
        _save_db(db)
