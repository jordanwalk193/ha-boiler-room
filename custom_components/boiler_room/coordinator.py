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
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AuthError,
    BoilerRoomError,
    CivicClient,
    CongressClient,
    CookClient,
    NotFoundError,
    OpenStatesClient,
    PolymarketClient,
    fetch_text,
)
from .const import (
    COOK_CHAMBERS,
    DOMAIN,
    EVENT_BILL_ACTION,
    EVENT_NEW_HEADLINE,
    EVENT_ODDS_MOVED,
    EVENT_RATING_CHANGED,
    INTERVAL_COOK,
    INTERVAL_ELECTIONS,
    INTERVAL_FEDERAL_BILLS,
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
    merge_elections,
    merge_headlines,
    parse_civic_elections,
    parse_cook_race,
    parse_event,
    parse_feed,
    parse_market,
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
    ) -> None:
        super().__init__(hass, entry, "markets", INTERVAL_MARKETS)
        self._client = client
        self.slugs = slugs
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
            raise UpdateFailed("Polymarket did not return any of the configured markets")
        self._fire_moves(data)
        return data

    async def _fetch(self, slug: str) -> dict[str, Any] | None:
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
