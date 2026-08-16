"""Client for the cbeapi.be CBE/KBO wrapper.

Two things learned from probing the live API, both baked in here:

1. The site sits behind Cloudflare with a browser-signature rule. Requests sent
   with a default library User-Agent are rejected with HTTP 403 (error 1010)
   *before reaching the API*, which looks exactly like an auth failure but is
   not. A browser-like UA is required.
2. Quota is reported per response via x-ratelimit-* headers (2500 per window
   observed). We track the last seen values so the app can surface remaining
   budget instead of discovering exhaustion mid-session.
"""

import logging

import httpx

from . import config

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class CBEError(RuntimeError):
    """Raised when the upstream API cannot satisfy a request."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class CBEClient:
    def __init__(self, api_key: str | None = None, lang: str = config.DEFAULT_LANG):
        self.api_key = api_key or config.cbe_api_key()
        self.lang = lang
        self.rate_limit: dict[str, str] = {}
        self._client = httpx.AsyncClient(
            base_url=config.CBE_BASE_URL,
            timeout=30.0,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict | None = None) -> dict | list:
        if not self.api_key:
            raise CBEError(
                "No CBE API key configured. Set CBE_API_KEY in .env.", status_code=500
            )

        params = {k: v for k, v in (params or {}).items() if v is not None}
        params.setdefault("lang", self.lang)

        try:
            response = await self._client.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.RequestError as exc:
            raise CBEError(f"Could not reach the CBE API: {exc}") from exc

        for header in ("x-ratelimit-limit", "x-ratelimit-remaining", "retry-after"):
            if header in response.headers:
                self.rate_limit[header] = response.headers[header]

        if response.status_code == 403 and "cloudflare" in response.text.lower():
            raise CBEError(
                "Blocked by Cloudflare before reaching the API — the User-Agent "
                "was rejected, not the API key.",
                status_code=403,
            )
        if response.status_code == 401:
            raise CBEError("CBE API rejected the key (401).", status_code=401)
        if response.status_code == 404:
            raise CBEError("Not found in the CBE register.", status_code=404)
        if response.status_code == 429:
            raise CBEError(
                f"CBE API rate limit exhausted. retry-after="
                f"{response.headers.get('retry-after', 'unknown')}",
                status_code=429,
            )
        if response.status_code >= 400:
            raise CBEError(
                f"CBE API error {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )

        return response.json()

    @staticmethod
    def _unwrap(payload: dict | list) -> list[dict]:
        """Return the record list from either envelope shape.

        The spec documents a paginated {data, links, meta} envelope, but the
        live search endpoint returns a bare {data: [...]} with no meta and a
        hard cap of 10 results. Handle both, plus a raw list.
        """
        if isinstance(payload, list):
            return payload
        data = payload.get("data", payload)
        return data if isinstance(data, list) else [data]

    async def search_by_name(self, name: str) -> list[dict]:
        """Search by company name. Note: capped at 10 results upstream."""
        return self._unwrap(await self._get("/v1/company/search", {"name": name}))

    async def search_by_post_code(self, post_code: int) -> list[dict]:
        return self._unwrap(
            await self._get("/v1/company/search", {"post_code": post_code})
        )

    async def search_by_address(
        self,
        street: str | None = None,
        house_number: str | None = None,
        city: str | None = None,
        post_code: int | None = None,
    ) -> list[dict]:
        return self._unwrap(
            await self._get(
                "/v1/company/search/address",
                {
                    "street": street,
                    "house_number": house_number,
                    "city": city,
                    "post_code": post_code,
                },
            )
        )

    async def get_company(self, cbe_number: str) -> dict:
        payload = await self._get(f"/v1/company/{cbe_number}")
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    async def get_establishment(self, establishment_number: str) -> dict:
        payload = await self._get(f"/v1/establishment/{establishment_number}")
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    async def companies_by_nace(self, code: str, nace_version: str = "2008") -> list[dict]:
        return self._unwrap(
            await self._get(
                f"/v1/nace/{code}/companies", {"nace_version": nace_version}
            )
        )

    async def nace_hierarchy(self) -> list[dict]:
        return self._unwrap(await self._get("/v1/nace/hierarchy"))

    async def juridical_forms(self) -> list[dict]:
        return self._unwrap(await self._get("/v1/juridical-forms"))
