from utils.helpers import _maybe_await

async def _create_conversation_id(openai_client) -> str | None:
    conversations = getattr(openai_client, "conversations", None)
    create = getattr(conversations, "create", None) if conversations else None

    if not callable(create):
        return None

    conversation = await _maybe_await(create())
    return str(getattr(conversation, "id", conversation))
