"""Single source of truth for the application version and licence.

Everything that displays a version reads it from here: the FastAPI app and
its OpenAPI schema, the /health endpoint, and the web UI (which fetches it
from /health rather than hardcoding it). Bumping this one constant and
tagging the commit is the whole release process — see README.

The licence follows the same rule for the same reason: the terms shown in the
UI and in /docs are the ones in the LICENSE file next to the code that is
running, not a string typed into a template months ago.
"""

__version__ = "0.8.0"

__license__ = "MIT"
__license_url__ = "https://github.com/rvanbruggen/openabox/blob/main/LICENSE"
__copyright__ = "Copyright (c) 2026 Rik Van Bruggen"

# The warranty disclaimer in the licence, in one sentence, for surfaces that
# have room for a line but not for the full text.
__disclaimer__ = "Provided as is, without warranty or guarantee of any kind."
