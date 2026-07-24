from azure.identity.aio import AzureCliCredential
from azure.identity.aio import DefaultAzureCredential

from config.settings import ENTORNO


def _get_credential():
    """Crea una credencial nueva según el entorno. Se crea por request para poder cerrarla bien."""
    if ENTORNO == "local":
        return AzureCliCredential()
    return DefaultAzureCredential()
