from utils.helpers import _maybe_await


def _object_to_dict(value) -> dict:
    if isinstance(value, dict):
        return value

    model_dump = getattr(value, "model_dump", None)

    if callable(model_dump):
        return model_dump()

    value_dict = getattr(value, "__dict__", None)

    if isinstance(value_dict, dict):
        return value_dict

    return {}


def _content_block_text(block) -> str:
    block_data = _object_to_dict(block)
    block_type = str(block_data.get("type") or getattr(block, "type", "") or "")

    if block_data.get("text"):
        return str(block_data["text"])

    if block_type == "input_image":
        return "[Imagen]"

    if block_type == "input_file":
        filename = block_data.get("filename") or block_data.get("file_id") or "archivo"
        return f"[Archivo: {filename}]"

    return ""


def _conversation_item_to_message(item) -> dict | None:
    item_data = _object_to_dict(item)
    item_type = str(item_data.get("type") or getattr(item, "type", "") or "")

    if item_type != "message":
        return None

    role = str(item_data.get("role") or getattr(item, "role", "") or "").strip()

    if role not in {"user", "assistant", "system"}:
        return None

    content_blocks = item_data.get("content") or getattr(item, "content", []) or []
    text_parts = [
        text
        for text in (_content_block_text(block) for block in content_blocks)
        if text
    ]
    content = "\n".join(text_parts).strip()

    if not content:
        return None

    return {
        "id": item_data.get("id") or getattr(item, "id", None),
        "role": role,
        "content": content,
        "status": item_data.get("status") or getattr(item, "status", None),
    }


async def _collect_messages_from_paginator(paginator) -> list[dict]:
    messages = []

    async for item in paginator:
        message = _conversation_item_to_message(item)

        if message:
            messages.append(message)

    return messages


async def _list_conversation_messages(openai_client, thread_id: str) -> list[dict]:
    conversations = getattr(openai_client, "conversations", None)
    items = getattr(conversations, "items", None) if conversations else None
    list_items = getattr(items, "list", None) if items else None

    if not callable(list_items):
        return []

    try:
        paginator = await _maybe_await(
            list_items(
                thread_id,
                order="asc",
                limit=100,
                include=["message.input_image.image_url"],
            )
        )
        return await _collect_messages_from_paginator(paginator)

    except Exception:
        paginator = await _maybe_await(
            list_items(
                thread_id,
                order="asc",
                limit=100,
            )
        )
        return await _collect_messages_from_paginator(paginator)
