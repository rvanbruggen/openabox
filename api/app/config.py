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

# National Bank Central Balance Sheet Office — the source of shareholder,
# director and participation data. The Consult portal needs no credentials; the
# official web services do, and setting a key switches the client over.
CBSO_CONSULT_URL = "https://consult.cbso.nbb.be"
CBSO_WS_URL = "https://ws.cbso.nbb.be"

# Filings change once a year, so a hydrated ownership record is trusted far
# longer than nothing — but not forever, since a company can file late or file
# a correction.
CBSO_CACHE_TTL_DAYS = int(os.environ.get("OPENABOX_CBSO_TTL_DAYS", "180"))

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "openabox-local")


def cbe_api_key() -> str | None:
    for name in _KEY_NAMES:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return None


def cbso_subscription_key() -> str | None:
    """Key for the official NBB web services, if one has been obtained.

    Absent, the client falls back to the credential-free Consult portal, which
    is fine for interactive use and not fine for bulk.
    """
    value = os.environ.get("CBSO_SUBSCRIPTION_KEY")
    return value.strip() if value else None
