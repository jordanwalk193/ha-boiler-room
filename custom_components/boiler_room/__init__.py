"""Boiler Room: US politics for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .api import (
    BlueskyClient,
    CivicClient,
    CongressClient,
    CookClient,
    FecClient,
    FederalRegisterClient,
    KalshiClient,
    OpenStatesClient,
    PolymarketClient,
)
from .const import (
    CONF_CAMPAIGN_FINANCE,
    CONF_CIVIC_KEY,
    CONF_CONGRESS_KEY,
    CONF_COOK_EMAIL,
    CONF_COOK_PASSWORD,
    CONF_CRYSTAL_BALL,
    CONF_CUSTOM_FEEDS,
    CONF_DISTRICT,
    CONF_EXECUTIVE,
    CONF_FEC_KEY,
    CONF_FEDERAL_BILLS,
    CONF_FLOOR_VOTES,
    CONF_HEADLINE_COUNT,
    CONF_KALSHI,
    CONF_MARKETS,
    CONF_NEWS_KEYWORDS,
    CONF_NEWS_SOURCES,
    CONF_ODDS_THRESHOLD,
    CONF_OPENSTATES_KEY,
    CONF_POLITICOS,
    CONF_STATE,
    CONF_STATE_BILLS,
    DEFAULT_HEADLINE_COUNT,
    DEFAULT_NEWS_SOURCES,
    DEFAULT_ODDS_THRESHOLD,
    DEFAULT_POLITICOS,
    DOMAIN,
    FEC_DEMO_KEY,
    FRONTEND_URL,
    NEWS_SOURCES,
    STATE_AUTO,
)
from .coordinator import (
    CampaignFinanceCoordinator,
    CookCoordinator,
    CrystalBallCoordinator,
    ElectionsCoordinator,
    ExecutiveCoordinator,
    FederalBillsCoordinator,
    FloorVotesCoordinator,
    MarketsCoordinator,
    NewsCoordinator,
    PoliticosCoordinator,
    RepsCoordinator,
    StateBillsCoordinator,
)
from .parsers import (
    current_congress,
    parse_bluesky_ref,
    parse_custom_feeds,
    parse_federal_bill,
    parse_kalshi_ref,
    parse_market_ref,
    parse_state_bill,
    split_list,
)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.CALENDAR, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the dashboard card and strategy to every browser."""
    integration = await async_get_integration(hass, DOMAIN)
    script = Path(__file__).parent / "frontend" / "boiler-room.js"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL, str(script), False)]
    )
    # Version + file date in the URL so browsers pick up new cards after an update.
    changed = int((await hass.async_add_executor_job(script.stat)).st_mtime)
    frontend.add_extra_js_url(hass, f"{FRONTEND_URL}?v={integration.version}-{changed}")
    return True


@dataclass
class BoilerRoomData:
    reps: RepsCoordinator | None
    federal_bills: FederalBillsCoordinator | None
    state_bills: StateBillsCoordinator | None
    markets: MarketsCoordinator | None
    news: NewsCoordinator | None
    elections: ElectionsCoordinator
    cook: CookCoordinator | None = None
    floor_votes: FloorVotesCoordinator | None = None
    finance: CampaignFinanceCoordinator | None = None
    executive: ExecutiveCoordinator | None = None
    crystal_ball: CrystalBallCoordinator | None = None
    politicos: PoliticosCoordinator | None = None
    home_district: int | None = None
    home_state: str | None = None

    @property
    def coordinators(self) -> list:
        return [
            c
            for c in (
                self.reps,
                self.federal_bills,
                self.state_bills,
                self.markets,
                self.news,
                self.elections,
                self.cook,
                self.floor_votes,
                self.finance,
                self.executive,
                self.crystal_ball,
                self.politicos,
            )
            if c is not None
        ]


type BoilerRoomConfigEntry = ConfigEntry[BoilerRoomData]


async def async_setup_entry(hass: HomeAssistant, entry: BoilerRoomConfigEntry) -> bool:
    """Set up Boiler Room from a config entry."""
    session = async_get_clientsession(hass)
    opts = entry.options
    congress = (
        CongressClient(session, entry.data[CONF_CONGRESS_KEY])
        if entry.data.get(CONF_CONGRESS_KEY)
        else None
    )
    openstates = (
        OpenStatesClient(session, entry.data[CONF_OPENSTATES_KEY])
        if entry.data.get(CONF_OPENSTATES_KEY)
        else None
    )
    civic = (
        CivicClient(session, entry.data[CONF_CIVIC_KEY]) if entry.data.get(CONF_CIVIC_KEY) else None
    )

    state_opt = opts.get(CONF_STATE, STATE_AUTO)
    state = None if state_opt in (None, "", STATE_AUTO) else state_opt
    district_raw = opts.get(CONF_DISTRICT)
    district = int(district_raw) if district_raw not in (None, "") else None

    reps = None
    if openstates or (congress and state):
        reps = RepsCoordinator(hass, entry, openstates, congress, state, district)

    def home_state() -> str | None:
        return state or (reps.home_state if reps else None)

    congress_now = current_congress(dt_util.now().date())
    federal_refs = list(
        {
            ref.key: ref
            for ref in (
                parse_federal_bill(t, congress_now)
                for t in split_list(opts.get(CONF_FEDERAL_BILLS))
            )
            if ref
        }.values()
    )
    state_refs = list(
        {
            ref.key: ref
            for ref in (parse_state_bill(t) for t in split_list(opts.get(CONF_STATE_BILLS)))
            if ref
        }.values()
    )
    slugs = list(
        dict.fromkeys(
            s for s in (parse_market_ref(t) for t in split_list(opts.get(CONF_MARKETS))) if s
        )
    )

    kalshi = list(
        dict.fromkeys(
            k for k in (parse_kalshi_ref(t) for t in split_list(opts.get(CONF_KALSHI))) if k
        )
    )

    politicos = politico_handles(opts.get(CONF_POLITICOS))

    def home_district() -> int | None:
        if district is not None:
            return district
        house = (reps.data or {}).get("us_representative") if reps else None
        return (house or {}).get("congressional_district")

    def house_rep() -> dict | None:
        return (reps.data or {}).get("us_representative") if reps else None

    feeds = [
        NEWS_SOURCES[key]
        for key in opts.get(CONF_NEWS_SOURCES, DEFAULT_NEWS_SOURCES)
        if key in NEWS_SOURCES
    ]
    custom = parse_custom_feeds(opts.get(CONF_CUSTOM_FEEDS))
    feeds += [(_feed_name(url), url) for url, _ in custom]
    feed_keywords = {url: words for url, words in custom if words}

    data = BoilerRoomData(
        reps=reps,
        federal_bills=(
            FederalBillsCoordinator(hass, entry, congress, federal_refs)
            if congress and federal_refs
            else None
        ),
        state_bills=(
            StateBillsCoordinator(hass, entry, openstates, state_refs, home_state)
            if openstates and state_refs
            else None
        ),
        markets=(
            MarketsCoordinator(
                hass,
                entry,
                PolymarketClient(session),
                slugs,
                float(opts.get(CONF_ODDS_THRESHOLD, DEFAULT_ODDS_THRESHOLD)),
                KalshiClient(session) if kalshi else None,
                kalshi,
            )
            if slugs or kalshi
            else None
        ),
        news=(
            NewsCoordinator(
                hass,
                entry,
                session,
                feeds,
                split_list(opts.get(CONF_NEWS_KEYWORDS)),
                int(opts.get(CONF_HEADLINE_COUNT, DEFAULT_HEADLINE_COUNT)),
                feed_keywords,
            )
            if feeds
            else None
        ),
        elections=ElectionsCoordinator(hass, entry, civic, home_state),
        cook=(
            CookCoordinator(
                hass,
                entry,
                CookClient(session, entry.data[CONF_COOK_EMAIL], entry.data[CONF_COOK_PASSWORD]),
            )
            if entry.data.get(CONF_COOK_EMAIL) and entry.data.get(CONF_COOK_PASSWORD)
            else None
        ),
        home_district=district,
        floor_votes=(
            FloorVotesCoordinator(hass, entry, congress, house_rep)
            if congress and opts.get(CONF_FLOOR_VOTES, True)
            else None
        ),
        executive=(
            ExecutiveCoordinator(hass, entry, FederalRegisterClient(session))
            if opts.get(CONF_EXECUTIVE, True)
            else None
        ),
        crystal_ball=(
            CrystalBallCoordinator(hass, entry, session)
            if opts.get(CONF_CRYSTAL_BALL, False)
            else None
        ),
        politicos=(
            PoliticosCoordinator(hass, entry, BlueskyClient(session), politicos)
            if politicos
            else None
        ),
    )

    # Reps first: state bills, elections, votes and money use what it discovers.
    if data.reps:
        await data.reps.async_config_entry_first_refresh()
    if opts.get(CONF_CAMPAIGN_FINANCE, True):
        fec_key = entry.data.get(CONF_FEC_KEY)
        finance = CampaignFinanceCoordinator(
            hass,
            entry,
            FecClient(session, fec_key or FEC_DEMO_KEY),
            home_state,
            home_district,
            personal_key=bool(fec_key),
        )
        # Nothing to look up without a home state (and no presidential cycle).
        data.finance = finance if finance.races() else None
    # The extras added in 0.3 start soft: if one of them is down, its entities show
    # unavailable and retry on schedule instead of holding up the whole integration.
    soft = {
        id(c)
        for c in (data.floor_votes, data.finance, data.executive, data.crystal_ball, data.politicos)
        if c
    }
    for coordinator in data.coordinators:
        if coordinator is data.reps:
            continue
        if id(coordinator) in soft:
            await coordinator.async_refresh()
        else:
            await coordinator.async_config_entry_first_refresh()

    if data.home_district is None and data.reps and data.reps.data:
        house = data.reps.data.get("us_representative") or {}
        data.home_district = house.get("congressional_district")

    data.home_state = home_state()
    entry.runtime_data = data
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BoilerRoomConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: BoilerRoomConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _feed_name(url: str) -> str:
    """A readable label for a custom feed, e.g. 'reddit.com/r/NeutralPolitics'."""
    parsed = urlparse(url)
    host = parsed.netloc.removeprefix("www.")
    path = parsed.path.rstrip("/").removesuffix(".rss").rstrip("/")
    if host.endswith("reddit.com") and "/r/" in path:
        return f"reddit {path[path.index('/r/') + 1 :]}"
    return host or url


def politico_handles(text: str | None) -> list[str]:
    """Bluesky handles to follow. Unset means the curated defaults; "none" turns it off."""
    if text is None:
        items = list(DEFAULT_POLITICOS)
    elif text.strip().lower() == "none":
        items = []
    else:
        items = split_list(text)
    return list(dict.fromkeys(h for h in (parse_bluesky_ref(i) for i in items) if h))
