def _sse(data: str, event: str | None = None) -> str:
    lines = []

    if event:
        lines.append(f"event: {event}")

    normalized_data = data.replace("\r\n", "\n").replace("\r", "\n")

    for line in normalized_data.split("\n"):
        lines.append(f"data: {line}")

    return "\n".join(lines) + "\n\n"
