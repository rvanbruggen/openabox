import os

# The key currently in .env is named CBEAPI-RIXTESTKEY. Hyphens are not valid
# shell identifiers, so CBE_API_KEY is the preferred name — but read the
# original too so an existing .env keeps working without being edited.
_KEY_NAMES = ("CBE_API_KEY", "CBEAPI-RIXTESTKEY")

CBE_BASE_URL = "https://cbeapi.be/api"
DEFAULT_LANG = os.environ.get("OPENABOX_LANG", "nl")

# How long a hydrated Company node is trusted before we consider re-fetching.
# The register changes slowly and the brief is explicit about not re-hitting
# the API, so this is deliberately long.
CACHE_TTL_DAYS = int(os.environ.get("OPENABOX_CACHE_TTL_DAYS", "90"))

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "openabox-local")


def cbe_api_key() -> str | None:
    for name in _KEY_NAMES:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return None
