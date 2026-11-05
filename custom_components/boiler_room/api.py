"""Thin async clients for the data sources Boiler Room reads."""

from __future__ import annotations

from typing import Any

import aiohttp

from .const import USER_AGENT

CONGRESS_BASE = "https://api.congress.gov/v3"
OPENSTATES_BASE = "https://v3.openstates.org"
GAMMA_BASE = "https://gamma-api.polymarket.com"
CIVIC_BASE = "https://www.googleapis.com/civicinfo/v2"
FEC_BASE = "https://api.open.fec.gov/v1"
FEDERAL_REGISTER_BASE = "https://www.federalregister.gov/api/v1"
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

TIMEOUT = aiohttp.ClientTimeout(total=20)


class BoilerRoomError(Exception):
    """A data source could not be reached or returned something unusable."""


class AuthError(BoilerRoomError):
    """An API key was rejected."""


class NotFoundError(BoilerRoomError):
    """The requested item does not exist."""


class BlockedError(BoilerRoomError):
    """The site answered with a bot challenge instead of the content."""


async def _get_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: Any = None,
    headers: dict[str, str] | None = None,
) -> Any:
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    try:
        async with session.get(url, params=params, headers=hdrs, timeout=TIMEOUT) as resp:
            if resp.status in (401, 403):
                raise AuthError(f"{resp.status} from {resp.url.host}")
            if resp.status == 404:
                raise NotFoundError(str(resp.url.path))
            if resp.status == 429:
                raise BoilerRoomError(f"Rate limited by {resp.url.host}")
            resp.raise_for_status()
            return await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, ValueError) as err:
        raise BoilerRoomError(f"{url}: {err}") from err


class CookClient:
    """Cook Political Report ratings API.

    Requires a Cook subscription that includes API access. Authenticates with the
    subscriber's own email and password (HTTP basic). Cook asks for no more than
    one pull a day; ratings never change more often than that.
    """

    BASE = "https://www.cookpolitical.com/api/race"
    MAX_PAGES = 12

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str) -> None:
        self._session = session
        self._auth = aiohttp.BasicAuth(email, password)

    async def _page(self, chamber: str, page: int) -> Any:
        hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        try:
            async with self._session.get(
                f"{self.BASE}/{chamber}",
                params={"page": page},
                headers=hdrs,
                auth=self._auth,
                timeout=TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    raise AuthError(f"Cook Political Report: {resp.status}")
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            raise BoilerRoomError(f"Cook {chamber}: {err}") from err

    async def races(self, chamber: str) -> list[dict[str, Any]]:
        """All races for a chamber, following pages until they stop adding anything.

        The docs don't pin down whether pages count from 0 or 1, so this tolerates both.
        """
        from .parsers import cook_records  # local import keeps api.py dependency-free

        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for page in range(self.MAX_PAGES):
            records = cook_records(await self._page(chamber, page))
            if not records:
                if page == 0:
                    continue
                break
            fresh = 0
            for rec in records:
                key = repr(sorted(rec.items()))
                if key not in seen:
                    seen.add(key)
                    out.append(rec)
                    fresh += 1
            if not fresh and page > 1:
                break
            if len(records) < 50:
                break  # a short page is the last page
        return out

    async def validate(self) -> None:
        await self._page("senate", 0)


class CongressClient:
    """Congress.gov API v3 (free key from api.congress.gov/sign-up)."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._key = api_key

    async def _get(self, path: str, **params: Any) -> Any:
        params = {"api_key": self._key, "format": "json", **params}
        return await _get_json(self._session, f"{CONGRESS_BASE}{path}", params=params)

    async def validate(self) -> None:
        await self._get("/member", limit=1)

    async def bill(self, congress: int, bill_type: str, number: int) -> dict[str, Any]:
        data = await self._get(f"/bill/{congress}/{bill_type}/{number}")
        return data.get("bill") or {}

    async def members(self, state: str, district: int | None = None) -> list[dict[str, Any]]:
        path = f"/member/{state.upper()}"
        if district is not None:
            path += f"/{district}"
        data = await self._get(path, currentMember="true", limit=50)
        return data.get("members") or []

    async def house_votes(
        self, congress: int, session: int, offset: int = 0, limit: int = 250
    ) -> dict[str, Any]:
        """One page of a session's House roll calls. Congress.gov doesn't sort them."""
        return await self._get(f"/house-vote/{congress}/{session}", offset=offset, limit=limit)

    async def house_vote(self, congress: int, session: int, roll: int) -> dict[str, Any]:
        data = await self._get(f"/house-vote/{congress}/{session}/{roll}")
        return data.get("houseRollCallVote") or {}

    async def house_vote_members(
        self, congress: int, session: int, roll: int
    ) -> list[dict[str, Any]]:
        data = await self._get(f"/house-vote/{congress}/{session}/{roll}/members", limit=500)
        return (data.get("houseRollCallVoteMemberVotes") or {}).get("results") or []

    async def latest_sponsored(self, bioguide_id: str) -> dict[str, Any] | None:
        data = await self._get(f"/member/{bioguide_id}/sponsored-legislation", limit=1)
        items = data.get("sponsoredLegislation") or []
        return items[0] if items else None


class OpenStatesClient:
    """Open States / Plural API v3 (free key from open.pluralpolicy.com)."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._key = api_key

    async def _get(self, path: str, params: Any = None) -> Any:
        return await _get_json(
            self._session,
            f"{OPENSTATES_BASE}{path}",
            params=params,
            headers={"X-API-KEY": self._key},
        )

    async def validate(self) -> None:
        await self._get("/jurisdictions", {"classification": "state", "per_page": 1})

    async def people_geo(self, lat: float, lng: float) -> list[dict[str, Any]]:
        data = await self._get("/people.geo", {"lat": lat, "lng": lng})
        return data.get("results") or []

    async def bill(self, state: str, identifier: str) -> dict[str, Any] | None:
        data = await self._get(
            "/bills",
            [
                ("jurisdiction", state),
                ("identifier", identifier),
                ("sort", "updated_desc"),
                ("per_page", 1),
            ],
        )
        results = data.get("results") or []
        return results[0] if results else None


class PolymarketClient:
    """Polymarket Gamma API. Public, read-only, no key."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def market(self, slug: str) -> dict[str, Any] | None:
        data = await _get_json(self._session, f"{GAMMA_BASE}/markets", params={"slug": slug})
        return data[0] if isinstance(data, list) and data else None

    async def event(self, slug: str) -> dict[str, Any] | None:
        data = await _get_json(self._session, f"{GAMMA_BASE}/events", params={"slug": slug})
        return data[0] if isinstance(data, list) and data else None


class KalshiClient:
    """Kalshi's public market data. Read-only, no key."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def event(self, ticker: str) -> dict[str, Any] | None:
        try:
            data = await _get_json(
                self._session,
                f"{KALSHI_BASE}/events/{ticker.upper()}",
                params={"with_nested_markets": "true"},
            )
        except NotFoundError:
            return None
        return data.get("event") if isinstance(data, dict) else None

    async def market(self, ticker: str) -> dict[str, Any] | None:
        try:
            data = await _get_json(self._session, f"{KALSHI_BASE}/markets/{ticker.upper()}")
        except NotFoundError:
            return None
        return data.get("market") if isinstance(data, dict) else None


class FecClient:
    """OpenFEC (api.data.gov key; DEMO_KEY works at low volume)."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._key = api_key

    async def race(
        self, cycle: int, office: str, state: str | None = None, district: int | None = None
    ) -> list[dict[str, Any]]:
        """Every candidate in one race with their committee totals, biggest raiser first."""
        params: dict[str, Any] = {
            "api_key": self._key,
            "cycle": cycle,
            "office": office,
            "per_page": 30,
            "sort": "-total_receipts",
        }
        if state and office != "president":
            params["state"] = state.upper()
        if office == "house" and district is not None:
            params["district"] = f"{district:02d}"
        data = await _get_json(self._session, f"{FEC_BASE}/elections/", params=params)
        return data.get("results") or []

    async def validate(self) -> None:
        await _get_json(
            self._session, f"{FEC_BASE}/candidates/", params={"api_key": self._key, "per_page": 1}
        )


class FederalRegisterClient:
    """Federal Register API. Public, no key."""

    FIELDS = (
        "title",
        "document_number",
        "html_url",
        "pdf_url",
        "publication_date",
        "signing_date",
        "subtype",
        "executive_order_number",
        "presidential_document_number",
        "president",
        "abstract",
    )

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def presidential_documents(self, per_page: int = 15) -> dict[str, Any]:
        params: list[tuple[str, Any]] = [
            ("per_page", per_page),
            ("order", "newest"),
            ("conditions[type][]", "PRESDOCU"),
        ] + [("fields[]", f) for f in self.FIELDS]
        return await _get_json(
            self._session, f"{FEDERAL_REGISTER_BASE}/documents.json", params=params
        )

    async def executive_order_count(self, since: str) -> int | None:
        """How many executive orders were published on or after an ISO date."""
        params = [
            ("per_page", 1),
            ("conditions[type][]", "PRESDOCU"),
            ("conditions[presidential_document_type][]", "executive_order"),
            ("conditions[publication_date][gte]", since),
            ("fields[]", "document_number"),
        ]
        data = await _get_json(
            self._session, f"{FEDERAL_REGISTER_BASE}/documents.json", params=params
        )
        count = data.get("count") if isinstance(data, dict) else None
        return int(count) if isinstance(count, int) else None


class CivicClient:
    """Google Civic Information API, elections endpoint only."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._key = api_key

    async def elections(self) -> dict[str, Any]:
        return await _get_json(self._session, f"{CIVIC_BASE}/elections", params={"key": self._key})

    async def validate(self) -> None:
        try:
            await self.elections()
        except BoilerRoomError as err:
            # Google answers a bad key with 400 rather than 401/403.
            if "400" in str(err):
                raise AuthError(str(err)) from err
            raise


async def fetch_text(session: aiohttp.ClientSession, url: str) -> str:
    """Fetch a feed body as text.

    A bot challenge (Cloudflare's "Just a moment...") is reported as BlockedError
    so callers can tell the user, rather than treating it as a broken feed.
    Boiler Room never tries to get past one.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
    }
    try:
        async with session.get(url, headers=headers, timeout=TIMEOUT) as resp:
            if resp.status in (403, 429, 503):
                body = await resp.text()
                if is_bot_challenge(resp.headers.get("cf-mitigated"), body):
                    raise BlockedError(f"{resp.url.host} answered with a bot challenge")
            resp.raise_for_status()
            return await resp.text()
    except (TimeoutError, aiohttp.ClientError) as err:
        raise BoilerRoomError(f"{url}: {err}") from err


def is_bot_challenge(cf_mitigated: str | None, body: str) -> bool:
    """True for a Cloudflare-style interstitial instead of real content."""
    if (cf_mitigated or "").lower() == "challenge":
        return True
    head = body[:4000].lower()
    return "<title>just a moment" in head or "challenge-platform" in head or "_cf_chl_opt" in head
