"""Config flow, options flow and full setup with every source mocked."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.boiler_room.const import (
    CONF_CIVIC_KEY,
    CONF_CONGRESS_KEY,
    CONF_COOK_EMAIL,
    CONF_COOK_PASSWORD,
    CONF_CUSTOM_FEEDS,
    CONF_FEDERAL_BILLS,
    CONF_MARKETS,
    CONF_NEWS_SOURCES,
    CONF_ODDS_THRESHOLD,
    CONF_OPENSTATES_KEY,
    CONF_STATE_BILLS,
    DOMAIN,
    EVENT_BILL_ACTION,
    EVENT_NEW_HEADLINE,
    EVENT_ODDS_MOVED,
    EVENT_RATING_CHANGED,
)

CONGRESS = "https://api.congress.gov/v3"
OPENSTATES = "https://v3.openstates.org"
GAMMA = "https://gamma-api.polymarket.com"
CIVIC = "https://www.googleapis.com/civicinfo/v2"
FEED = "https://example.com/politics.rss"
COOK = "https://www.cookpolitical.com/api/race"


def _person(name, classification, chamber, division, title, district=None):
    return {
        "id": f"ocd-person/{name}",
        "name": name,
        "party": "Independent",
        "jurisdiction": {"classification": classification},
        "current_role": {
            "org_classification": chamber,
            "division_id": division,
            "title": title,
            "district": district,
        },
        "image": f"https://img/{name}.jpg",
        "email": f"{name}@example.gov",
        "openstates_url": f"https://openstates.org/person/{name}",
    }


PEOPLE = [
    _person("Ann Able", "country", "upper", "ocd-division/country:us/state:ny", "Senator"),
    _person("Ben Baker", "country", "upper", "ocd-division/country:us/state:ny", "Senator"),
    _person(
        "Cal Cole",
        "country",
        "lower",
        "ocd-division/country:us/state:ny/cd:24",
        "Representative",
        "NY-24",
    ),
    _person(
        "Dee Dunn", "state", "upper", "ocd-division/country:us/state:ny/sldu:50", "Senator", "50"
    ),
    _person(
        "Eve Ellis",
        "state",
        "lower",
        "ocd-division/country:us/state:ny/sldl:126",
        "Assembly Member",
        "126",
    ),
]

MEMBERS = [
    {"bioguideId": "A000001", "name": "Able, Ann", "terms": {"item": [{"chamber": "Senate"}]}},
    {"bioguideId": "B000002", "name": "Baker, Ben", "terms": {"item": [{"chamber": "Senate"}]}},
    {
        "bioguideId": "C000003",
        "name": "Cole, Cal",
        "district": 24,
        "terms": {"item": [{"chamber": "House of Representatives"}]},
    },
]


def _bill(action: str, date: str) -> dict[str, Any]:
    return {
        "bill": {
            "title": "An Act to Test Things",
            "introducedDate": "2026-01-15",
            "latestAction": {"actionDate": date, "text": action},
            "sponsors": [{"fullName": "Rep. Cole, Cal [I-NY-24]"}],
            "policyArea": {"name": "Government Operations and Politics"},
        }
    }


def _market(yes: str) -> list[dict[str, Any]]:
    no = f"{1 - float(yes):.3f}"
    return [
        {
            "question": "Will the test bill pass the House?",
            "slug": "test-bill-passes",
            "outcomes": '["Yes","No"]',
            "outcomePrices": f'["{yes}","{no}"]',
            "volume": "1000",
        }
    ]


EVENT = [
    {
        "title": "Who controls the test chamber?",
        "slug": "test-chamber-control",
        "markets": [
            {
                "groupItemTitle": "Party A",
                "outcomes": '["Yes","No"]',
                "outcomePrices": '["0.58","0.42"]',
            },
            {
                "groupItemTitle": "Party B",
                "outcomes": '["Yes","No"]',
                "outcomePrices": '["0.42","0.58"]',
            },
        ],
    }
]


def _rss(*items: tuple[str, str, str]) -> str:
    body = "".join(
        f"<item><title>{t}</title><link>{link}</link><pubDate>{d}</pubDate></item>"
        for t, link, d in items
    )
    return (
        f'<?xml version="1.0"?><rss version="2.0"><channel><title>X</title>{body}</channel></rss>'
    )


def _cook(house_24: str = "Lean R") -> dict[str, Any]:
    return {
        "house": [
            {"Title": "NY-24", "Incumbent": "Cal Cole", "Rating": house_24, "Cook PVI": "R+11"},
            {"Title": "NY-22", "Incumbent": "Someone Else", "Rating": "Toss Up", "Cook PVI": "D+2"},
        ],
        "senate": [{"Title": "Georgia", "State": "GA", "Rating": "Toss Up"}],
        "governor": [{"Title": "New York", "State": "NY", "Rating": "Likely D"}],
    }


def _mock_cook(aioclient_mock: AiohttpClientMocker, payload: dict[str, Any]) -> None:
    for chamber, races in payload.items():
        aioclient_mock.get(f"{COOK}/{chamber}", json=races)


def _mock_all(aioclient_mock: AiohttpClientMocker) -> None:
    _mock_cook(aioclient_mock, _cook())
    aioclient_mock.get(f"{OPENSTATES}/people.geo", json={"results": PEOPLE})
    aioclient_mock.get(
        f"{OPENSTATES}/bills",
        json={
            "results": [
                {
                    "identifier": "S 1234",
                    "title": "Relates to test procedures",
                    "session": "2025-2026",
                    "from_organization": {"name": "Senate"},
                    "latest_action_description": "REFERRED TO RULES",
                    "latest_action_date": "2026-05-01",
                    "openstates_url": "https://openstates.org/ny/bills/2025-2026/S1234/",
                }
            ]
        },
    )
    aioclient_mock.get(f"{CONGRESS}/member/NY", json={"members": MEMBERS})
    for bioguide in ("A000001", "B000002", "C000003"):
        aioclient_mock.get(
            f"{CONGRESS}/member/{bioguide}/sponsored-legislation",
            json={
                "sponsoredLegislation": [
                    {
                        "type": "HR",
                        "number": "42",
                        "title": f"Bill by {bioguide}",
                        "introducedDate": "2026-10-01",
                    }
                ]
            },
        )
    aioclient_mock.get(
        f"{CONGRESS}/bill/119/hr/1234", json=_bill("Referred to committee.", "2026-02-01")
    )
    aioclient_mock.get(
        f"{GAMMA}/markets", params={"slug": "test-bill-passes"}, json=_market("0.30")
    )
    aioclient_mock.get(f"{GAMMA}/markets", params={"slug": "test-chamber-control"}, json=[])
    aioclient_mock.get(f"{GAMMA}/events", params={"slug": "test-chamber-control"}, json=EVENT)
    aioclient_mock.get(
        FEED,
        text=_rss(("First headline", "https://example.com/1", "Tue, 03 Nov 2026 06:00:00 -0500")),
    )
    aioclient_mock.get(
        f"{CIVIC}/elections",
        json={
            "elections": [
                {
                    "id": "9",
                    "name": "New York General Election",
                    "electionDay": "2026-11-03",
                    "ocdDivisionId": "ocd-division/country:us/state:ny",
                }
            ]
        },
    )


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            CONF_CONGRESS_KEY: "c",
            CONF_OPENSTATES_KEY: "o",
            CONF_CIVIC_KEY: "g",
            CONF_COOK_EMAIL: "me@example.com",
            CONF_COOK_PASSWORD: "pw",
        },
        options={
            CONF_FEDERAL_BILLS: "HR 1234",
            CONF_STATE_BILLS: "S1234",
            CONF_MARKETS: "test-bill-passes\nhttps://polymarket.com/event/test-chamber-control",
            CONF_ODDS_THRESHOLD: 5,
            CONF_NEWS_SOURCES: [],
            CONF_CUSTOM_FEEDS: FEED,
        },
    )


@pytest.fixture
def no_sleep(monkeypatch):
    async def _sleep(*_args):
        return None

    monkeypatch.setattr("custom_components.boiler_room.coordinator.asyncio.sleep", _sleep)


@pytest.mark.freeze_time("2026-11-03 15:00:00+00:00")
async def test_full_setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    no_sleep,
) -> None:
    await hass.config.async_set_time_zone("America/New_York")
    _mock_all(aioclient_mock)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    states = {s.entity_id: s for s in hass.states.async_all()}

    senator = states["sensor.boiler_room_representatives_u_s_senator_1"]
    assert senator.state == "Ann Able"
    assert senator.attributes["bioguide_id"] == "A000001"
    assert senator.attributes["latest_sponsored_bill"] == "HR 42"
    assert senator.attributes["entity_picture"] == "https://img/Ann Able.jpg"
    assert states["sensor.boiler_room_representatives_u_s_representative"].state == "Cal Cole"
    assert states["sensor.boiler_room_representatives_state_representative"].state == "Eve Ellis"

    fed = states["sensor.boiler_room_legislation_h_r_1234"]
    assert fed.state == "Referred to committee."
    assert fed.attributes["url"] == "https://www.congress.gov/bill/119th-congress/house-bill/1234"
    assert states["sensor.boiler_room_legislation_ny_s_1234"].state == "REFERRED TO RULES"

    market = states["sensor.boiler_room_prediction_markets_will_the_test_bill_pass_the_house"]
    assert float(market.state) == 30.0
    assert market.attributes["unit_of_measurement"] == "%"
    event = states["sensor.boiler_room_prediction_markets_who_controls_the_test_chamber"]
    assert event.state == "Party A"
    assert event.attributes["leader_probability"] == 58.0

    assert states["sensor.boiler_room_news_latest_headline"].state == "First headline"

    assert states["binary_sensor.boiler_room_elections_election_day"].state == "on"
    assert states["sensor.boiler_room_elections_next_election"].state == "2026-11-03"
    assert states["calendar.boiler_room_elections_elections"].state == "on"

    home_house = states["sensor.boiler_room_race_ratings_cook_rating_your_house_district"]
    assert home_house.state == "Lean R"
    assert home_house.attributes["pvi"] == "R+11"
    assert (
        states["sensor.boiler_room_race_ratings_cook_rating_your_governor_s_race"].state
        == "Likely D"
    )
    assert (
        states["sensor.boiler_room_race_ratings_cook_rating_your_senate_race"].state
        == "No race rated"
    )
    tossups = states["sensor.boiler_room_race_ratings_cook_house_toss_ups"]
    assert tossups.state == "1"
    assert tossups.attributes["ratings"] == {"Toss Up": 1, "Lean R": 1}

    # --- Changes fire bus events -------------------------------------------
    bill_events = async_capture_events(hass, EVENT_BILL_ACTION)
    odds_events = async_capture_events(hass, EVENT_ODDS_MOVED)
    news_events = async_capture_events(hass, EVENT_NEW_HEADLINE)
    rating_events = async_capture_events(hass, EVENT_RATING_CHANGED)

    # The first registered mock that matches wins, so register the changes first.
    aioclient_mock.clear_requests()
    _mock_cook(aioclient_mock, _cook(house_24="Toss Up"))
    aioclient_mock.get(f"{CONGRESS}/bill/119/hr/1234", json=_bill("Passed House.", "2026-11-03"))
    aioclient_mock.get(
        f"{GAMMA}/markets", params={"slug": "test-bill-passes"}, json=_market("0.45")
    )
    aioclient_mock.get(
        FEED,
        text=_rss(
            ("Breaking headline", "https://example.com/2", "Tue, 03 Nov 2026 09:00:00 -0500"),
            ("First headline", "https://example.com/1", "Tue, 03 Nov 2026 06:00:00 -0500"),
        ),
    )
    _mock_all(aioclient_mock)

    data = entry.runtime_data
    for coordinator in (data.federal_bills, data.markets, data.news, data.cook):
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert [e.data["action"] for e in bill_events] == ["Passed House."]
    assert [(e.data["outcome"], e.data["change"]) for e in odds_events] == [
        ("Yes", 15.0),
        ("No", -15.0),
    ]
    assert [e.data["title"] for e in news_events] == ["Breaking headline"]
    assert [(e.data["race"], e.data["from"], e.data["to"]) for e in rating_events] == [
        ("NY-24", "Lean R", "Toss Up")
    ]
    assert hass.states.get("sensor.boiler_room_race_ratings_cook_house_toss_ups").state == "2"
    assert hass.states.get("sensor.boiler_room_legislation_h_r_1234").state == "Passed House."

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_setup_with_no_keys(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Without keys you still get news + the federal election calendar."""
    aioclient_mock.get(FEED, text=_rss(("Hi", "https://x/1", "Tue, 03 Nov 2026 06:00:00 -0500")))
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={},
        options={CONF_NEWS_SOURCES: [], CONF_CUSTOM_FEEDS: FEED},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ids = {
        e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }
    assert ids == {
        "sensor.boiler_room_news_latest_headline",
        "sensor.boiler_room_elections_next_election",
        "binary_sensor.boiler_room_elections_election_day",
        "calendar.boiler_room_elections_elections",
    }


async def test_config_flow_no_keys(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {}
    assert result["options"][CONF_NEWS_SOURCES]


async def test_config_flow_bad_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{CONGRESS}/member", status=403)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONGRESS_KEY: "nope"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CONGRESS_KEY: "invalid_key"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{CONGRESS}/member", json={"members": []})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONGRESS_KEY: " good "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_CONGRESS_KEY: "good"}


async def test_options_flow_validates(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(FEED, text=_rss(("Hi", "https://x/1", "Tue, 03 Nov 2026 06:00:00 -0500")))
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={},
        options={CONF_NEWS_SOURCES: [], CONF_CUSTOM_FEEDS: FEED},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_FEDERAL_BILLS: "HR 1, banana", CONF_NEWS_SOURCES: [], CONF_CUSTOM_FEEDS: FEED},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_FEDERAL_BILLS: "invalid_entries"}
    assert result["description_placeholders"]["invalid"] == "banana"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_FEDERAL_BILLS: "HR 1", CONF_NEWS_SOURCES: [], CONF_CUSTOM_FEEDS: FEED},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_FEDERAL_BILLS] == "HR 1"


async def test_config_flow_cook_login(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COOK_EMAIL: "me@example.com"}
    )
    assert result["errors"] == {CONF_COOK_PASSWORD: "cook_incomplete"}

    aioclient_mock.get(f"{COOK}/senate", status=401)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COOK_EMAIL: "me@example.com", CONF_COOK_PASSWORD: "bad"}
    )
    assert result["errors"] == {CONF_COOK_PASSWORD: "cook_denied"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{COOK}/senate", json=[])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COOK_EMAIL: " me@example.com ", CONF_COOK_PASSWORD: " pw "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_COOK_EMAIL: "me@example.com", CONF_COOK_PASSWORD: " pw "}


async def test_dashboard_card_is_served(
    hass: HomeAssistant, hass_client, aioclient_mock: AiohttpClientMocker
) -> None:
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL

    from custom_components.boiler_room.const import FRONTEND_URL

    aioclient_mock.get(FEED, text=_rss(("Hi", "https://x/1", "Tue, 03 Nov 2026 06:00:00 -0500")))
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={},
        options={CONF_NEWS_SOURCES: [], CONF_CUSTOM_FEEDS: FEED},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert any(url.startswith(FRONTEND_URL) for url in hass.data[DATA_EXTRA_MODULE_URL].urls)
    client = await hass_client()
    resp = await client.get(FRONTEND_URL)
    assert resp.status == 200
    assert "ll-strategy-dashboard-boiler-room" in await resp.text()
