from utils.helpers import _maybe_await

async def _read_binary_response(file_content) -> bytes:
    if isinstance(file_content, bytes):
        return file_content

    if hasattr(file_content, "read") and callable(file_content.read):
        content = file_content.read()
        content = await _maybe_await(content)
        return content or b""

    body = getattr(file_content, "body", None)

    if body is not None:
        chunks = []

        async_iter = getattr(body, "__aiter__", None)

        if callable(async_iter):
            async for chunk in body:
                chunks.append(chunk if isinstance(chunk, bytes) else bytes(chunk))
            return b"".join(chunks)

        for chunk in body:
            chunks.append(chunk if isinstance(chunk, bytes) else bytes(chunk))

        return b"".join(chunks)

    content = getattr(file_content, "content", None)

    if content is not None:
        content = await _maybe_await(content)
        return content or b""

    return bytes(file_content)
