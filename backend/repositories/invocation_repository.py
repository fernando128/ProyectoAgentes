_INVOCATIONS: dict[str, dict] = {}

def _get_invocation_record(invocation_id: str) -> dict | None:
    return _INVOCATIONS.get(invocation_id)

def _save_invocation_record(record: dict) -> dict:
    _INVOCATIONS[record["invocation_id"]] = record
    return record

def _delete_invocation_record(invocation_id: str) -> None:
    _INVOCATIONS.pop(invocation_id, None)
