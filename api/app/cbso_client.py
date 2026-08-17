"""Client for the National Bank's Central Balance Sheet Office (CBSO).

This is where shareholder and director data comes from. The CBE register has
neither; the annual accounts filed with the NBB have both, as structured XBRL.

Two routes exist to the same filings, and the choice between them is a
deliberate one:

* **Consult** (`consult.cbso.nbb.be`) is the public consultation portal. Its
  JSON API needs no credentials at all, which is what makes it usable today.
  Its terms of use state the application "is not intended for the systematic -
  or mass - consultation or downloading of files", and the NBB reserves the
  right to block access without warning. That is compatible with OpenABox only
  because the app is cache-first: one fetch per company on first lookup, then
  served from Neo4j. Do not batch-crawl through this client.

* **The official web services** (`ws.cbso.nbb.be`) are the sanctioned route and
  the "Authentic Data Query" product is free of charge, but access requires a
  signed subscription with the NBB plus a developer-portal account. Set
  `CBSO_SUBSCRIPTION_KEY` and this client switches over automatically — same
  methods, same return shapes, no other code changes.

Quirks found by probing the Consult API, all of which are load-bearing below:

1. The language path segment must be **uppercase** (`/NL`); lowercase returns
   HTTP 500 from a Spring type-conversion error.
2. The paginated deposits endpoint **requires** a `sort` parameter. Without it
   the request fails with HTTP 500, and the unpaginated variant returns 403.
3. Only filings whose `importFileType` is `XBRL` or `ZIP` have machine-readable
   data. Older ones are `MICROFILM`/`PDF` — scanned images with nothing to
   extract.
"""

import logging
import re
import uuid

import httpx

from . import config

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Filings we can actually parse. Anything else is a scanned image.
MACHINE_READABLE = {"XBRL", "ZIP"}

# Consult addresses a filing by GUID; the official web services address the same
# filing by reference number ("2025-00539072"). Only the GUID resolves on the
# public portal, so citation links are emitted for it alone rather than
# constructing URLs that would 404.
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def enterprise_url(cbe_number: str) -> str:
    """The public Consult page listing every filing for a company.

    Always valid, whichever backend supplied the data, so this is the fallback
    citation when a specific filing cannot be linked directly.
    """
    return f"{config.CBSO_CONSULT_URL}/consult-enterprise/{cbe_number}"


def deposit_urls(deposit_id: str | None) -> dict[str, str] | None:
    """Public links to one filing, or None if it cannot be addressed publicly.

    These are citation links for a human to click, not fetch targets — which is
    why they point at the NBB rather than being proxied through this app. The
    figures shown in the UI should be checkable against the original document
    without taking anyone's word for the extraction.
    """
    if not deposit_id or not _GUID.match(str(deposit_id)):
        return None
    base = f"{config.CBSO_CONSULT_URL}/api/external/broker/public/deposits"
    return {
        "pdf": f"{base}/pdf/{deposit_id}",
        "xbrl": f"{base}/xbrl/{deposit_id}",
        "csv": f"{base}/consult/csv/{deposit_id}",
    }


class CBSOError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class CBSOClient:
    def __init__(self, subscription_key: str | None = None):
        self.subscription_key = subscription_key or config.cbso_subscription_key()
        self.official = bool(self.subscription_key)
        base_url = config.CBSO_WS_URL if self.official else config.CBSO_CONSULT_URL
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=60.0,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {"Accept": accept}
        if self.official:
            # The official gateway wants the key plus a per-request UUID it can
            # correlate with its own logs when something needs debugging.
            headers["NBB-CBSO-Subscription-Key"] = self.subscription_key
            headers["X-Request-Id"] = str(uuid.uuid4())
        return headers

    async def _get(self, path: str, accept: str, params: dict | None = None):
        try:
            response = await self._client.get(
                path, params=params, headers=self._headers(accept)
            )
        except httpx.RequestError as exc:
            raise CBSOError(f"Could not reach the CBSO API: {exc}") from exc

        if response.status_code == 404:
            raise CBSOError("Not found at the CBSO.", status_code=404)
        if response.status_code == 429:
            raise CBSOError(
                "CBSO rate limit hit. This client is for interactive lookups "
                "only; use the official web services for bulk access.",
                status_code=429,
            )
        if response.status_code >= 400:
            raise CBSOError(
                f"CBSO error {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )
        return response

    async def list_deposits(self, cbe_number: str, limit: int = 20) -> list[dict]:
        """Return filings for a company, newest first.

        Shapes differ between the two backends, so both are normalised here and
        callers never need to know which one answered.
        """
        if self.official:
            response = await self._get(
                f"/authentic/legalEntity/{cbe_number}/references", "application/json"
            )
            return [_normalise_official(row) for row in response.json()][:limit]

        response = await self._get(
            "/api/rs-consult/published-deposits",
            "application/json",
            params={
                "page": 0,
                "size": limit,
                "enterpriseNumber": cbe_number,
                "sort": "depositDate,desc",
            },
        )
        return [_normalise_consult(row) for row in response.json().get("content", [])]

    async def fetch_xbrl(self, deposit_id: str) -> bytes:
        """Download one filing's XBRL instance."""
        if self.official:
            response = await self._get(
                f"/authentic/deposit/{deposit_id}/accountingData", "application/xbrl"
            )
        else:
            # The Consult broker returns 406 for a specific Accept header — it
            # serves the file only when the client expresses no preference.
            response = await self._get(
                f"/api/external/broker/public/deposits/xbrl/{deposit_id}", "*/*"
            )
        return response.content

    async def fetch_csv(self, deposit_id: str) -> bytes:
        """Download one filing's flattened rubriek-code CSV.

        Only the Consult portal produces this; the official web services return
        XBRL/JSON/PDF, so a subscribed client falls back to the public route
        for this one call rather than pretending the format does not exist.
        """
        path = f"/api/external/broker/public/deposits/consult/csv/{deposit_id}"
        if not self.official:
            return (await self._get(path, "*/*")).content

        async with httpx.AsyncClient(
            base_url=config.CBSO_CONSULT_URL,
            timeout=60.0,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA, "Accept": "*/*"},
        ) as client:
            response = await client.get(path)
            if response.status_code >= 400:
                raise CBSOError(
                    f"CBSO CSV error {response.status_code}",
                    status_code=response.status_code,
                )
            return response.content

    async def latest_parsable_deposit(self, cbe_number: str) -> dict | None:
        """The most recent filing that actually has structured data in it.

        A company can have decades of filings where only the last few are XBRL,
        so "latest" and "latest usable" are different questions.
        """
        for deposit in await self.list_deposits(cbe_number):
            if deposit["import_file_type"] in MACHINE_READABLE:
                return deposit
        return None


def _normalise_consult(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "reference": row.get("reference"),
        "cbe_number": row.get("enterpriseNumber"),
        "enterprise_name": row.get("enterpriseName"),
        "model_id": row.get("modelId"),
        "model_name": row.get("modelName"),
        "language": row.get("language"),
        "deposit_date": row.get("depositDate"),
        "period_start": row.get("periodStartDate"),
        "period_end": row.get("periodEndDate"),
        "import_file_type": row.get("importFileType"),
        "taxonomy": row.get("taxonomyName"),
    }


def _normalise_official(row: dict) -> dict:
    exercise = row.get("ExerciseDates") or {}
    return {
        # The official API addresses filings by reference number where Consult
        # uses a GUID, so `id` is whatever that backend needs for a later fetch.
        "id": row.get("ReferenceNumber"),
        "reference": row.get("ReferenceNumber"),
        "cbe_number": row.get("EnterpriseNumber"),
        "enterprise_name": row.get("EnterpriseName"),
        "model_id": row.get("ModelType"),
        "model_name": None,
        "language": row.get("Language"),
        "deposit_date": row.get("DepositDate"),
        "period_start": exercise.get("startDate"),
        "period_end": exercise.get("endDate"),
        # The references endpoint does not report the source file type; every
        # filing it serves has accounting data, so assume it is parsable.
        "import_file_type": "XBRL",
        "taxonomy": None,
    }
