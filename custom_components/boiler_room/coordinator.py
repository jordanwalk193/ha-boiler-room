"""Data update coordinators, one per source so each polls at its own pace."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import TYPE_CHECKING, Any
import xml.etree.ElementTree as ET

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AuthError,
    BlockedError,
    BoilerRoomError,
    CivicClient,
    CongressClient,
    CookClient,
    FecClient,
    FederalRegisterClient,
    KalshiClient,
    NotFoundError,
    OpenStatesClient,
    PolymarketClient,
    fetch_text,
)
from .const import (
    COOK_CHAMBERS,
    CRYSTAL_BALL_FEED,
    CRYSTAL_BALL_URL,
    DOMAIN,
    EVENT_BILL_ACTION,
    EVENT_CRYSTAL_BALL_POST,
    EVENT_EXECUTIVE_ACTION,
    EVENT_FEC_REPORT,
    EVENT_NEW_HEADLINE,
    EVENT_ODDS_MOVED,
    EVENT_RATING_CHANGED,
    EVENT_REP_VOTE,
    FLOOR_VOTES_KEPT,
    INTERVAL_COOK,
    INTERVAL_CRYSTAL_BALL,
    INTERVAL_ELECTIONS,
    INTERVAL_EXECUTIVE,
    INTERVAL_FEC,
    INTERVAL_FEDERAL_BILLS,
    INTERVAL_FLOOR_VOTES,
    INTERVAL_MARKETS,
    INTERVAL_NEWS,
    INTERVAL_REPS,
    INTERVAL_STATE_BILLS,
)
from .parsers import (
    FederalBillRef,
    Headline,
    StateBillRef,
    assign_rep_slots,
    computed_federal_elections,
    cook_race_key,
    current_congress,
    current_session,
    fec_cycle,
    is_rating_post,
    merge_elections,
    merge_headlines,
    newest_rolls,
    parse_civic_elections,
    parse_cook_race,
    parse_event,
    parse_fec_race,
    parse_feed,
    parse_house_vote,
    parse_kalshi_event,
    parse_market,
    parse_presidential_documents,
    summarize_ratings,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


class _Base(DataUpdateCoordinator[Any]):
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, name: str, interval: timedelta
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{name}",
            update_interval=interval,
        )


# --------------------------------------------------------------------------
# Representatives
# --------------------------------------------------------------------------


class RepsCoordinator(_Base):
    """Who represents the home location, refreshed daily."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        openstates: OpenStatesClient | None,
        congress: CongressClient | None,
        state: str | None,
        district: int | None,
    ) -> None:
        super().__init__(hass, entry, "reps", INTERVAL_REPS)
        self._openstates = openstates
        self._congress = congress
        self._state = state
        self._district = district
        self.home_state: str | None = state

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            slots = await self._from_openstates() if self._openstates else {}
            if not slots and self._congress and self._state:
                slots = await self._from_congress()
            if self._congress:
                await self._enrich_federal(slots)
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except BoilerRoomError as err:
            raise UpdateFailed(str(err)) from err
        if not self.home_state:
            self.home_state = next((r["state"] for r in slots.values() if r.get("state")), None)
        return slots

    async def _from_openstates(self) -> dict[str, dict[str, Any]]:
        assert self._openstates
        people = await self._openstates.people_geo(
            self.hass.config.latitude, self.hass.config.longitude
        )
        return assign_rep_slots(people)

    async def _from_congress(self) -> dict[str, dict[str, Any]]:
        """Fallback when there's no Open States key: state (+ district) from options."""
        assert self._congress and self._state
        slots: dict[str, dict[str, Any]] = {}
        members = await self._congress.members(self._state)
        senators = [m for m in members if _latest_chamber(m) == "Senate"]
        for idx, member in enumerate(sorted(senators, key=lambda m: m.get("name", ""))[:2]):
            slots[f"us_senator_{idx + 1}"] = _rep_from_congress(member, self._state)
        if self._district is not None:
            house = [
                m
                for m in await self._congress.members(self._state, self._district)
                if _latest_chamber(m) == "House of Representatives"
            ]
            if house:
                rep = _rep_from_congress(house[0], self._state)
                rep["congressional_district"] = self._district
                slots["us_representative"] = rep
        return slots

    async def _enrich_federal(self, slots: dict[str, dict[str, Any]]) -> None:
        """Add bioguide IDs and each member's newest sponsored bill from Congress.gov."""
        assert self._congress
        state = next(
            (r["state"] for k, r in slots.items() if k.startswith("us_") and r.get("state")),
            None,
        )
        if not state:
            return
        members = await self._congress.members(state)
        for key, rep in slots.items():
            if not key.startswith("us_"):
                continue
            if not rep.get("bioguide_id"):
                rep["bioguide_id"] = _match_bioguide(rep, members, key)
            if bioguide := rep.get("bioguide_id"):
                try:
                    latest = await self._congress.latest_sponsored(bioguide)
                except NotFoundError:
                    latest = None
                if latest:
                    rep["latest_sponsored"] = {
                        "title": latest.get("title"),
                        "introduced": latest.get("introducedDate"),
                        "bill": f"{(latest.get('type') or '').upper()} {latest.get('number') or ''}".strip(),
                        "latest_action": (latest.get("latestAction") or {}).get("text"),
                    }
                rep["congress_url"] = f"https://www.congress.gov/member/{bioguide}"


def _latest_chamber(member: dict[str, Any]) -> str | None:
    terms = (member.get("terms") or {}).get("item") or []
    return terms[-1].get("chamber") if terms else None


def _rep_from_congress(member: dict[str, Any], state: str) -> dict[str, Any]:
    party = member.get("partyName")
    return {
        "name": _display_name(member.get("name") or ""),
        "party": party,
        "title": "Senator" if _latest_chamber(member) == "Senate" else "Representative",
        "district": str(member.get("district")) if member.get("district") else None,
        "state": state,
        "image": (member.get("depiction") or {}).get("imageUrl"),
        "bioguide_id": member.get("bioguideId"),
    }


def _display_name(name: str) -> str:
    """Congress.gov lists 'Last, First M.'; flip it for display."""
    if "," in name:
        last, first = name.split(",", 1)
        return f"{first.strip()} {last.strip()}"
    return name


def _match_bioguide(rep: dict[str, Any], members: list[dict[str, Any]], slot: str) -> str | None:
    want_chamber = "Senate" if slot.startswith("us_senator") else "House of Representatives"
    last = (rep.get("name") or "").split()[-1:] or [""]
    for member in members:
        if _latest_chamber(member) != want_chamber:
            continue
        if want_chamber != "Senate" and member.get("district") != rep.get("congressional_district"):
            continue
        if last[0].lower() in (member.get("name") or "").lower():
            return member.get("bioguideId")
    return None


# --------------------------------------------------------------------------
# Bills
# --------------------------------------------------------------------------


class _BillCoordinator(_Base):
    """Shared change detection: fire an event when a bill's latest action changes."""

    level: str = ""

    def _fire_changes(self, new: dict[str, dict[str, Any] | None]) -> None:
        old = self.data or {}
        for key, bill in new.items():
            if not bill:
                continue
            previous = old.get(key)
            if previous is None:
                continue  # first sighting; nothing to compare to
            if (previous.get("latest_action"), previous.get("latest_action_date")) != (
                bill.get("latest_action"),
                bill.get("latest_action_date"),
            ):
                self.hass.bus.async_fire(
                    EVENT_BILL_ACTION,
                    {
                        "level": self.level,
                        "bill": bill.get("label"),
                        "title": bill.get("title"),
                        "action": bill.get("latest_action"),
                        "date": bill.get("latest_action_date"),
                        "url": bill.get("url"),
                    },
                )


class FederalBillsCoordinator(_BillCoordinator):
    level = "federal"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: CongressClient,
        bills: list[FederalBillRef],
    ) -> None:
        super().__init__(hass, entry, "federal_bills", INTERVAL_FEDERAL_BILLS)
        self._client = client
        self.bills = bills

    async def _async_update_data(self) -> dict[str, dict[str, Any] | None]:
        results = await asyncio.gather(
            *(self._fetch(ref) for ref in self.bills), return_exceptions=True
        )
        data: dict[str, dict[str, Any] | None] = {}
        failures = 0
        for ref, result in zip(self.bills, results, strict=True):
            if isinstance(result, AuthError):
                raise ConfigEntryAuthFailed(str(result))
            if isinstance(result, BaseException):
                failures += 1
                _LOGGER.debug("Bill %s: %s", ref.label, result)
                data[ref.key] = (self.data or {}).get(ref.key)
            else:
                data[ref.key] = result
        if self.bills and failures == len(self.bills) and not self.data:
            raise UpdateFailed("Congress.gov did not return any of the followed bills")
        self._fire_changes(data)
        return data

    async def _fetch(self, ref: FederalBillRef) -> dict[str, Any] | None:
        try:
            bill = await self._client.bill(ref.congress, ref.bill_type, ref.number)
        except NotFoundError:
            return None
        latest = bill.get("latestAction") or {}
        sponsors = bill.get("sponsors") or []
        return {
            "label": ref.label,
            "congress": ref.congress,
            "title": bill.get("title"),
            "latest_action": latest.get("text"),
            "latest_action_date": latest.get("actionDate"),
            "introduced": bill.get("introducedDate"),
            "sponsor": sponsors[0].get("fullName") if sponsors else None,
            "cosponsors": (bill.get("cosponsors") or {}).get("count"),
            "policy_area": (bill.get("policyArea") or {}).get("name"),
            "url": bill.get("legislationUrl") or ref.public_url,
        }


class StateBillsCoordinator(_BillCoordinator):
    level = "state"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: OpenStatesClient,
        bills: list[StateBillRef],
        home_state: Callable[[], str | None],
    ) -> None:
        super().__init__(hass, entry, "state_bills", INTERVAL_STATE_BILLS)
        self._client = client
        self.bills = bills
        self._home_state = home_state

    async def _async_update_data(self) -> dict[str, dict[str, Any] | None]:
        data: dict[str, dict[str, Any] | None] = {}
        # Sequential on purpose: Open States' free tier allows about one request a second.
        for ref in self.bills:
            state = ref.state or self._home_state()
            if not state:
                data[ref.key] = None
                continue
            try:
                bill = await self._client.bill(state, ref.identifier)
            except AuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except BoilerRoomError as err:
                _LOGGER.debug("State bill %s: %s", ref.identifier, err)
                data[ref.key] = (self.data or {}).get(ref.key)
                continue
            data[ref.key] = (
                {
                    "label": f"{state.upper()} {bill.get('identifier', ref.identifier)}",
                    "title": bill.get("title"),
                    "session": bill.get("session"),
                    "chamber": (bill.get("from_organization") or {}).get("name"),
                    "latest_action": bill.get("latest_action_description"),
                    "latest_action_date": bill.get("latest_action_date"),
                    "url": bill.get("openstates_url"),
                }
                if bill
                else None
            )
            await asyncio.sleep(1.1)
        self._fire_changes(data)
        return data


# --------------------------------------------------------------------------
# Prediction markets
# --------------------------------------------------------------------------


class MarketsCoordinator(_Base):
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: PolymarketClient,
        slugs: list[str],
        threshold: float,
        kalshi: KalshiClient | None = None,
        kalshi_tickers: list[str] | None = None,
    ) -> None:
        super().__init__(hass, entry, "markets", INTERVAL_MARKETS)
        self._client = client
        self._kalshi = kalshi
        # Polymarket slugs as-is; Kalshi tickers as "kalshi:TICKER" so the two never collide.
        self.slugs = slugs + [f"kalshi:{t}" for t in kalshi_tickers or []]
        self._threshold = threshold

    async def _async_update_data(self) -> dict[str, dict[str, Any] | None]:
        results = await asyncio.gather(
            *(self._fetch(slug) for slug in self.slugs), return_exceptions=True
        )
        data: dict[str, dict[str, Any] | None] = {}
        failures = 0
        for slug, result in zip(self.slugs, results, strict=True):
            if isinstance(result, BaseException):
                failures += 1
                _LOGGER.debug("Market %s: %s", slug, result)
                data[slug] = (self.data or {}).get(slug)
            else:
                data[slug] = result
        if self.slugs and failures == len(self.slugs) and not self.data:
            raise UpdateFailed("None of the configured prediction markets could be read")
        self._fire_moves(data)
        return data

    async def _fetch(self, slug: str) -> dict[str, Any] | None:
        if slug.startswith("kalshi:"):
            assert self._kalshi
            ticker = slug.removeprefix("kalshi:")
            if event := await self._kalshi.event(ticker):
                return parse_kalshi_event(event)
            if market := await self._kalshi.market(ticker):
                return parse_kalshi_event({**market, "markets": [market]})
            return None
        if market := await self._client.market(slug):
            return parse_market(market)
        if event := await self._client.event(slug):
            return parse_event(event)
        return None

    def _fire_moves(self, new: dict[str, dict[str, Any] | None]) -> None:
        if not self._threshold or not self.data:
            return
        for slug, item in new.items():
            old = self.data.get(slug)
            if not item or not old:
                continue
            for outcome, pct in item["outcomes"].items():
                before = old["outcomes"].get(outcome)
                if before is None or abs(pct - before) < self._threshold:
                    continue
                self.hass.bus.async_fire(
                    EVENT_ODDS_MOVED,
                    {
                        "market": item["title"],
                        "outcome": outcome,
                        "from": before,
                        "to": pct,
                        "change": round(pct - before, 1),
                        "url": item["url"],
                        "source": item.get("source"),
                    },
                )


# --------------------------------------------------------------------------
# News
# --------------------------------------------------------------------------


@dataclass
class NewsData:
    headlines: list[Headline] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)


class NewsCoordinator(_Base):
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session: Any,
        feeds: list[tuple[str, str]],
        keywords: list[str],
        limit: int,
    ) -> None:
        super().__init__(hass, entry, "news", INTERVAL_NEWS)
        self._session = session
        self.feeds = feeds
        self._keywords = keywords
        self._limit = limit

    async def _async_update_data(self) -> NewsData:
        results = await asyncio.gather(
            *(self._fetch(name, url) for name, url in self.feeds), return_exceptions=True
        )
        groups: list[list[Headline]] = []
        failed: list[str] = []
        for (name, _), result in zip(self.feeds, results, strict=True):
            if isinstance(result, BaseException):
                _LOGGER.debug("Feed %s: %s", name, result)
                failed.append(name)
            else:
                groups.append(result)
        if self.feeds and not groups:
            raise UpdateFailed("None of the news feeds could be read")
        headlines = merge_headlines(groups, self._keywords, self._limit)
        if self.data is not None:
            seen = {h.link for h in self.data.headlines}
            for headline in reversed(headlines):
                if headline.link not in seen:
                    self.hass.bus.async_fire(EVENT_NEW_HEADLINE, headline.as_dict())
        return NewsData(headlines, failed)

    async def _fetch(self, name: str, url: str) -> list[Headline]:
        body = await fetch_text(self._session, url)
        try:
            return parse_feed(body, name)
        except ET.ParseError as err:
            raise BoilerRoomError(f"{name}: not a valid feed ({err})") from err


# --------------------------------------------------------------------------
# Elections
# --------------------------------------------------------------------------


class ElectionsCoordinator(_Base):
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: CivicClient | None,
        home_state: Callable[[], str | None],
    ) -> None:
        super().__init__(hass, entry, "elections", INTERVAL_ELECTIONS)
        self._client = client
        self._home_state = home_state
        self.civic_ok: bool | None = None

    async def _async_update_data(self) -> list[dict[str, Any]]:
        today = dt_util.now().date()
        civic: list[dict[str, Any]] = []
        if self._client:
            try:
                civic = parse_civic_elections(await self._client.elections(), self._home_state())
                self.civic_ok = True
            except AuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except BoilerRoomError as err:
                # Computed federal dates still work; don't take the calendar down.
                _LOGGER.debug("Google Civic: %s", err)
                self.civic_ok = False
        merged = merge_elections(computed_federal_elections(today), civic)
        return [e for e in merged if e["date"] >= today]


# --------------------------------------------------------------------------
# Cook Political Report ratings
# --------------------------------------------------------------------------


@dataclass
class CookData:
    races: dict[str, dict[str, Any]] = field(default_factory=dict)
    summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw_keys: dict[str, list[str]] = field(default_factory=dict)


class CookCoordinator(_Base):
    """Cook race ratings, pulled once a day as Cook asks."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: CookClient) -> None:
        super().__init__(hass, entry, "cook", INTERVAL_COOK)
        self._client = client

    async def _async_update_data(self) -> CookData:
        data = CookData()
        for chamber in COOK_CHAMBERS:
            try:
                records = await self._client.races(chamber)
            except AuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except BoilerRoomError as err:
                if self.data and chamber in self.data.summaries:
                    _LOGGER.debug("Cook %s: %s (keeping yesterday's ratings)", chamber, err)
                    data.summaries[chamber] = self.data.summaries[chamber]
                    data.races.update(
                        {k: v for k, v in self.data.races.items() if v["chamber"] == chamber}
                    )
                    continue
                raise UpdateFailed(str(err)) from err
            if records:
                data.raw_keys[chamber] = sorted(records[0])
            races = [parse_cook_race(r, chamber) for r in records]
            for race in races:
                data.races[cook_race_key(race)] = race
            data.summaries[chamber] = summarize_ratings(races)
        self._fire_changes(data)
        return data

    def _fire_changes(self, new: CookData) -> None:
        if not self.data:
            return
        for key, race in new.races.items():
            old = self.data.races.get(key)
            if old and old["rating"] and race["rating"] and old["rating"] != race["rating"]:
                self.hass.bus.async_fire(
                    EVENT_RATING_CHANGED,
                    {
                        "source": "Cook Political Report",
                        "chamber": race["chamber"],
                        "race": race["title"],
                        "state": race["state"],
                        "district": race["district"],
                        "from": old["rating"],
                        "to": race["rating"],
                    },
                )

    def home_race(
        self, chamber: str, state: str | None, district: int | None = None
    ) -> dict[str, Any] | None:
        if not self.data or not state:
            return None
        if chamber == "house":
            if district is None:
                return None
            return self.data.races.get(f"house-{state}-{district}")
        matches = [
            r for r in self.data.races.values() if r["chamber"] == chamber and r["state"] == state
        ]
        return matches[0] if matches else None


# --------------------------------------------------------------------------
# House floor votes
# --------------------------------------------------------------------------


@dataclass
class VotesData:
    votes: list[dict[str, Any]] = field(default_factory=list)
    rep_bioguide: str | None = None
    rep_name: str | None = None
    session_label: str | None = None


class FloorVotesCoordinator(_Base):
    """Recent House roll calls and how your representative voted on each.

    Roll calls never change once they're posted, so each one is fetched once and
    kept; an hourly check only pulls the session list plus anything new.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: CongressClient,
        rep: Callable[[], dict[str, Any] | None],
        keep: int = FLOOR_VOTES_KEPT,
    ) -> None:
        super().__init__(hass, entry, "floor_votes", INTERVAL_FLOOR_VOTES)
        self._client = client
        self._rep = rep
        self._keep = keep
        self._cache: dict[tuple[int, int, int], dict[str, Any]] = {}

    async def _session_items(self, congress: int, session: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        for _ in range(6):  # a busy session runs ~700 roll calls
            page = await self._client.house_votes(congress, session, offset=offset)
            batch = page.get("houseRollCallVotes") or []
            items += batch
            total = (page.get("pagination") or {}).get("count") or 0
            offset += len(batch)
            if not batch or offset >= total:
                break
        return items

    async def _async_update_data(self) -> VotesData:
        today = dt_util.now().date()
        congress, session = current_congress(today), current_session(today)
        rep = self._rep() or {}
        bioguide = rep.get("bioguide_id")
        try:
            items = await self._session_items(congress, session)
            if not items and session == 2:
                session = 1  # early January: nothing on the floor yet this session
                items = await self._session_items(congress, session)
            wanted = newest_rolls(items, self._keep)
            missing = [
                int(i["rollCallNumber"])
                for i in wanted
                if (congress, session, int(i["rollCallNumber"])) not in self._cache
            ]
            fetched = await asyncio.gather(
                *(self._fetch(congress, session, roll, bioguide) for roll in missing),
                return_exceptions=True,
            )
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except BoilerRoomError as err:
            raise UpdateFailed(str(err)) from err
        for roll, result in zip(missing, fetched, strict=True):
            if isinstance(result, AuthError):
                raise ConfigEntryAuthFailed(str(result))
            if isinstance(result, BaseException):
                _LOGGER.debug("House roll call %s: %s", roll, result)
                continue
            self._cache[(congress, session, roll)] = result
        votes = [
            self._cache[key]
            for i in wanted
            if (key := (congress, session, int(i["rollCallNumber"]))) in self._cache
        ]
        # Drop cache entries that scrolled off, so memory stays flat.
        keep = {(congress, session, v["roll"]) for v in votes}
        self._cache = {k: v for k, v in self._cache.items() if k in keep}
        data = VotesData(
            votes=votes,
            rep_bioguide=bioguide.upper() if bioguide else None,
            rep_name=rep.get("name"),
            session_label=f"{congress}th Congress, session {session}",
        )
        self._fire_new(data)
        return data

    async def _fetch(
        self, congress: int, session: int, roll: int, bioguide: str | None
    ) -> dict[str, Any]:
        detail, members = await asyncio.gather(
            self._client.house_vote(congress, session, roll),
            self._client.house_vote_members(congress, session, roll),
        )
        return parse_house_vote(detail, members, [bioguide] if bioguide else [])

    def _fire_new(self, new: VotesData) -> None:
        if self.data is None or not new.rep_bioguide:
            return  # first load: everything is "new", so stay quiet
        seen = {v["roll"] for v in self.data.votes}
        for vote in reversed(new.votes):
            mine = vote["members"].get(new.rep_bioguide)
            if vote["roll"] in seen or not mine:
                continue
            self.hass.bus.async_fire(
                EVENT_REP_VOTE,
                {
                    "representative": new.rep_name,
                    "vote": mine["vote"],
                    "with_party": mine["with_party"],
                    "roll": vote["roll"],
                    "question": vote["question"],
                    "bill": vote["bill"],
                    "result": vote["result"],
                    "date": vote["date"],
                    "url": vote["bill_url"] or vote["clerk_url"],
                },
            )


# --------------------------------------------------------------------------
# Campaign finance
# --------------------------------------------------------------------------


class CampaignFinanceCoordinator(_Base):
    """FEC money totals for the races that cover home."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: FecClient,
        home_state: Callable[[], str | None],
        home_district: Callable[[], int | None],
        personal_key: bool,
    ) -> None:
        super().__init__(hass, entry, "campaign_finance", INTERVAL_FEC)
        self._client = client
        self._home_state = home_state
        self._home_district = home_district
        self._personal_key = personal_key

    def races(self) -> list[str]:
        cycle = fec_cycle(dt_util.now().date())
        offices = []
        if self._home_state() and self._home_district() is not None:
            offices.append("house")
        if self._home_state():
            offices.append("senate")
        if cycle % 4 == 0:
            offices.append("president")
        return offices

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        cycle = fec_cycle(dt_util.now().date())
        state, district = self._home_state(), self._home_district()
        data: dict[str, dict[str, Any]] = {}
        for office in self.races():
            try:
                rows = await self._client.race(cycle, office, state, district)
            except AuthError as err:
                if self._personal_key:
                    raise ConfigEntryAuthFailed(str(err)) from err
                raise UpdateFailed(f"FEC demo key refused: {err}") from err
            except BoilerRoomError as err:
                if self.data and office in self.data:
                    _LOGGER.debug("FEC %s: %s (keeping last totals)", office, err)
                    data[office] = self.data[office]
                    continue
                raise UpdateFailed(str(err)) from err
            data[office] = {
                "office": office,
                "cycle": cycle,
                "state": state if office != "president" else None,
                "district": district if office == "house" else None,
                "candidates": parse_fec_race(rows),
            }
        self._fire_reports(data)
        return data

    def _fire_reports(self, new: dict[str, dict[str, Any]]) -> None:
        if not self.data:
            return
        for office, race in new.items():
            before = {
                c["candidate_id"]: c for c in (self.data.get(office) or {}).get("candidates", [])
            }
            for cand in race["candidates"]:
                old = before.get(cand["candidate_id"])
                if old and cand["through"] and cand["through"] != old["through"]:
                    self.hass.bus.async_fire(
                        EVENT_FEC_REPORT,
                        {
                            "office": office,
                            "candidate": cand["name"],
                            "party": cand["party"],
                            "raised": cand["raised"],
                            "raised_since_last": round(cand["raised"] - old["raised"], 2),
                            "cash_on_hand": cand["cash_on_hand"],
                            "through": cand["through"],
                            "url": cand["url"],
                        },
                    )


# --------------------------------------------------------------------------
# Executive actions
# --------------------------------------------------------------------------


@dataclass
class ExecutiveData:
    documents: list[dict[str, Any]] = field(default_factory=list)
    executive_orders_this_year: int | None = None


class ExecutiveCoordinator(_Base):
    """Executive orders, proclamations and memoranda as published in the Federal Register."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: FederalRegisterClient
    ) -> None:
        super().__init__(hass, entry, "executive", INTERVAL_EXECUTIVE)
        self._client = client

    async def _async_update_data(self) -> ExecutiveData:
        year = dt_util.now().year
        try:
            docs = parse_presidential_documents(await self._client.presidential_documents())
            count = await self._client.executive_order_count(f"{year}-01-01")
        except BoilerRoomError as err:
            raise UpdateFailed(str(err)) from err
        data = ExecutiveData(docs, count)
        if self.data is not None:
            seen = {d["document_number"] for d in self.data.documents}
            for doc in reversed(docs):
                if doc["document_number"] not in seen:
                    self.hass.bus.async_fire(EVENT_EXECUTIVE_ACTION, doc)
        return data


# --------------------------------------------------------------------------
# Sabato's Crystal Ball (opt-in)
# --------------------------------------------------------------------------

ISSUE_CRYSTAL_BALL_BLOCKED = "crystal_ball_blocked"


@dataclass
class CrystalBallData:
    posts: list[Headline] = field(default_factory=list)
    blocked: bool = False
    last_success: str | None = None


class CrystalBallCoordinator(_Base):
    """Crystal Ball articles from its official feed, when the site lets us read it.

    The site's bot challenge usually refuses non-browser clients. When that
    happens this raises a Repairs issue explaining it and keeps the last posts it
    did get; it never tries to get past the challenge.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, session: Any) -> None:
        super().__init__(hass, entry, "crystal_ball", INTERVAL_CRYSTAL_BALL)
        self._session = session

    async def _async_update_data(self) -> CrystalBallData:
        previous = self.data or CrystalBallData()
        try:
            body = await fetch_text(self._session, CRYSTAL_BALL_FEED)
            posts = parse_feed(body, "Sabato's Crystal Ball")
        except BlockedError as err:
            _LOGGER.debug("Crystal Ball: %s", err)
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_CRYSTAL_BALL_BLOCKED,
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_CRYSTAL_BALL_BLOCKED,
                learn_more_url=CRYSTAL_BALL_URL,
            )
            return CrystalBallData(previous.posts, True, previous.last_success)
        except (BoilerRoomError, ET.ParseError) as err:
            raise UpdateFailed(f"Crystal Ball feed: {err}") from err
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_CRYSTAL_BALL_BLOCKED)
        posts.sort(key=lambda h: h.published or dt_util.utc_from_timestamp(0), reverse=True)
        if self.data is not None and self.data.posts:
            seen = {h.link for h in self.data.posts}
            for post in reversed(posts):
                if post.link not in seen:
                    self.hass.bus.async_fire(
                        EVENT_CRYSTAL_BALL_POST,
                        {**post.as_dict(), "rating_change": is_rating_post(post.title)},
                    )
        return CrystalBallData(posts[:15], False, dt_util.utcnow().isoformat())
