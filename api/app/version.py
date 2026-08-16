"""Single source of truth for the application version.

Everything that displays a version reads it from here: the FastAPI app and
its OpenAPI schema, the /health endpoint, and the web UI (which fetches it
from /health rather than hardcoding it). Bumping this one constant and
tagging the commit is the whole release process — see README.
"""

__version__ = "0.1.0"
