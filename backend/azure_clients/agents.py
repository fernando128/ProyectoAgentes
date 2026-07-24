from azure.ai.projects.aio import AIProjectClient

def _get_agent_version(agent) -> str | None:
    versions = getattr(agent, "versions", None)
    latest = getattr(versions, "latest", None) if versions else None
    return getattr(latest, "version", None) if latest else None

def _get_agent_payload(agent, vector_store_ids: list[str] | None = None) -> dict:
    name = getattr(agent, "name", None)
    version = getattr(agent, "version", None) or _get_agent_version(agent)
    agent_id = getattr(agent, "id", None) or (
        f"{name}:{version}" if name and version else name
    )

    payload = {
        "id": agent_id,
        "name": name,
        "version": version,
    }

    if vector_store_ids is not None:
        payload["vector_store_ids"] = vector_store_ids

    return payload

async def _resolve_agent_version(
    client: AIProjectClient,
    agent_name: str,
    configured_version: str | None,
    auto_resolve: bool,
) -> str:
    if configured_version:
        return configured_version

    if not auto_resolve:
        raise ValueError(
            "Falta AZURE_AI_AGENT_VERSION. Configura la version del agente en "
            "local.settings.json para evitar listar agentes con agents/read."
        )

    async for agent in client.agents.list():
        if getattr(agent, "name", None) == agent_name:
            version = _get_agent_version(agent)

            if version:
                return version

            break

    raise ValueError(
        f"No se pudo encontrar una versión activa para el agente '{agent_name}'."
    )
