"""Config flow, options flow and full setup with every source mocked."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er, issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.boiler_room.const import (
    CONF_CAMPAIGN_FINANCE,
    CONF_CIVIC_KEY,
    CONF_CONGRESS_KEY,
    CONF_COOK_EMAIL,
    CONF_COOK_PASSWORD,
    CONF_CRYSTAL_BALL,
    CONF_CUSTOM_FEEDS,
    CONF_EXECUTIVE,
    CONF_FEDERAL_BILLS,
    CONF_KALSHI,
    CONF_MARKETS,
    CONF_NEWS_SOURCES,
    CONF_ODDS_THRESHOLD,
    CONF_OPENSTATES_KEY,
    CONF_POLITICOS,
    CONF_STATE_BILLS,
    CRYSTAL_BALL_FEED,
    DOMAIN,
    EVENT_BILL_ACTION,
    EVENT_CRYSTAL_BALL_POST,
    EVENT_EXECUTIVE_ACTION,
    EVENT_FEC_REPORT,
    EVENT_NEW_HEADLINE,
    EVENT_ODDS_MOVED,
    EVENT_POLITICO_POST,
    EVENT_RATING_CHANGED,
    EVENT_REP_VOTE,
)

CONGRESS = "https://api.congress.gov/v3"
OPENSTATES = "https://v3.openstates.org"
GAMMA = "https://gamma-api.polymarket.com"
CIVIC = "https://www.googleapis.com/civicinfo/v2"
FEED = "https://example.com/politics.rss"
COOK = "https://www.cookpolitical.com/api/race"
FEC = "https://api.open.fec.gov/v1"
FR = "https://www.federalregister.gov/api/v1/documents.json"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
BSKY = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
CHALLENGE = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
    "<body><script>window._cf_chl_opt={}</script></body></html>"
)


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


def _roll_list(*rolls: tuple[int, str]) -> dict[str, Any]:
    return {
        "houseRollCallVotes": [
            {"rollCallNumber": n, "startDate": d, "congress": 119, "sessionNumber": 2}
            for n, d in rolls
        ],
        "pagination": {"count": len(rolls)},
    }


def _roll_detail(roll: int, bill: str) -> dict[str, Any]:
    return {
        "houseRollCallVote": {
            "congress": 119,
            "sessionNumber": 2,
            "rollCallNumber": roll,
            "startDate": f"2026-10-{roll - 280:02d}T12:00:00-04:00",
            "voteQuestion": "On Passage",
            "result": "Passed",
            "voteType": "Yea-and-Nay",
            "legislationType": "HR",
            "legislationNumber": bill,
            "legislationUrl": f"https://www.congress.gov/bill/119/house-bill/{bill}",
            "sourceDataURL": f"https://clerk.house.gov/evs/2026/roll{roll}.xml",
            "votePartyTotal": [
                {"voteParty": "R", "yeaTotal": 200, "nayTotal": 10, "notVotingTotal": 2},
                {"voteParty": "I", "yeaTotal": 3, "nayTotal": 1, "notVotingTotal": 0},
                {"voteParty": "D", "yeaTotal": 5, "nayTotal": 200, "notVotingTotal": 3},
            ],
        }
    }


def _roll_members(cast: str) -> dict[str, Any]:
    return {
        "houseRollCallVoteMemberVotes": {
            "results": [
                {"bioguideID": "C000003", "voteCast": cast, "voteParty": "I", "voteState": "NY"},
                {"bioguideID": "Z000009", "voteCast": "Nay", "voteParty": "D", "voteState": "CA"},
            ]
        }
    }


def _mock_votes(aioclient_mock: AiohttpClientMocker, rolls: list[tuple[int, str, str]]) -> None:
    aioclient_mock.get(
        f"{CONGRESS}/house-vote/119/2",
        json=_roll_list(*((n, f"2026-10-{n - 280:02d}T12:00:00-04:00") for n, _, _ in rolls)),
    )
    for roll, bill, cast in rolls:
        aioclient_mock.get(f"{CONGRESS}/house-vote/119/2/{roll}", json=_roll_detail(roll, bill))
        aioclient_mock.get(f"{CONGRESS}/house-vote/119/2/{roll}/members", json=_roll_members(cast))


def _fec(through: str = "2026-10-14", raised: float = 3240826.28) -> dict[str, Any]:
    return {
        "results": [
            {
                "candidate_id": "H4NY24001",
                "candidate_name": "COLE, CAL",
                "party_full": "INDEPENDENT",
                "incumbent_challenge_full": "Incumbent",
                "total_receipts": raised,
                "total_disbursements": 2352696.07,
                "cash_on_hand_end_period": 1016371.51,
                "coverage_end_date": through,
            },
            {
                "candidate_id": "H6NY24002",
                "candidate_name": "SMITH, JANE Q MRS",
                "party_full": "DEMOCRATIC PARTY",
                "incumbent_challenge_full": "Challenger",
                "total_receipts": 464688.44,
                "total_disbursements": 304550.35,
                "cash_on_hand_end_period": 160138.09,
                "coverage_end_date": through,
            },
            {
                "candidate_id": "H6NY24003",
                "candidate_name": "NOBODY, NEVER RAISED",
                "party_full": "REPUBLICAN PARTY",
                "incumbent_challenge_full": "Challenger",
                "total_receipts": 0,
                "cash_on_hand_end_period": 0,
            },
        ]
    }


def _fr(*docs: tuple[str, str, str]) -> dict[str, Any]:
    return {
        "count": len(docs),
        "results": [
            {
                "title": title,
                "document_number": number,
                "subtype": kind,
                "html_url": f"https://www.federalregister.gov/d/{number}",
                "publication_date": "2026-11-02",
                "signing_date": "2026-10-28",
                "executive_order_number": "14400" if kind == "Executive Order" else None,
                "president": {"name": "Test President"},
            }
            for title, number, kind in docs
        ],
    }


def _mock_fr(aioclient_mock: AiohttpClientMocker, *docs: tuple[str, str, str]) -> None:
    # The count query is the narrower match, so it goes first.
    aioclient_mock.get(
        FR,
        params={"conditions[presidential_document_type][]": "executive_order"},
        json={"count": 37, "results": []},
    )
    aioclient_mock.get(
        FR, json=_fr(*(docs or (("Protecting Test Data", "2026-1", "Executive Order"),)))
    )


def _kalshi(lead: str = "0.6100") -> dict[str, Any]:
    return {
        "event": {
            "event_ticker": "KXTEST-26",
            "series_ticker": "KXTEST",
            "title": "Who wins the test seat?",
            "mutually_exclusive": True,
            "markets": [
                {
                    "yes_sub_title": "Cal Cole",
                    "last_price_dollars": lead,
                    "status": "active",
                    "volume_fp": "1000",
                    "close_time": "2026-11-04T00:00:00Z",
                },
                {
                    "yes_sub_title": "Jane Smith",
                    "last_price_dollars": "0.3900",
                    "status": "active",
                    "volume_fp": "500",
                    "close_time": "2026-11-04T00:00:00Z",
                },
            ],
        }
    }


def _bsky_post(handle: str, name: str, rkey: str, text: str, when: str, repost: bool = False):
    item = {
        "post": {
            "uri": f"at://did:plc:{handle}/app.bsky.feed.post/{rkey}",
            "author": {
                "handle": handle,
                "displayName": name,
                "avatar": f"https://cdn/{handle}.jpg",
            },
            "record": {"text": text, "createdAt": when},
            "likeCount": 7,
            "embed": {"external": {"uri": "https://example.com/a", "title": "An analysis"}},
        }
    }
    if repost:
        item["reason"] = {"$type": "app.bsky.feed.defs#reasonRepost"}
    return item


def _mock_bsky(aioclient_mock: AiohttpClientMocker, extra: list | None = None) -> None:
    aioclient_mock.get(
        BSKY,
        params={"actor": "cookpolitical.com"},
        json={
            "feed": (extra or [])
            + [
                _bsky_post(
                    "cookpolitical.com",
                    "The Cook Political Report",
                    "c1",
                    "New ratings",
                    "2026-11-03T10:00:00Z",
                ),
                _bsky_post(
                    "someone.else",
                    "Someone",
                    "r1",
                    "Reposted thing",
                    "2026-11-03T11:00:00Z",
                    repost=True,
                ),
            ]
        },
    )
    aioclient_mock.get(
        BSKY,
        params={"actor": "kkondik.bsky.social"},
        json={
            "feed": [
                _bsky_post(
                    "kkondik.bsky.social",
                    "Kyle Kondik",
                    "k1",
                    "Crystal Ball update",
                    "2026-11-03T12:00:00Z",
                )
            ]
        },
    )
    aioclient_mock.get(
        BSKY, params={"actor": "gone.bsky.social"}, status=400, json={"error": "InvalidRequest"}
    )


def _mock_all(aioclient_mock: AiohttpClientMocker) -> None:
    _mock_bsky(aioclient_mock)
    _mock_votes(aioclient_mock, [(300, "100", "Yea"), (301, "101", "Yea")])
    aioclient_mock.get(f"{FEC}/elections/", params={"office": "house"}, json=_fec())
    aioclient_mock.get(f"{FEC}/elections/", params={"office": "senate"}, json={"results": []})
    _mock_fr(aioclient_mock)
    aioclient_mock.get(f"{KALSHI}/events/KXTEST-26", json=_kalshi())
    aioclient_mock.get(CRYSTAL_BALL_FEED, status=403, text=CHALLENGE)
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
            CONF_KALSHI: "https://kalshi.com/markets/kxtest/test-seat/kxtest-26",
            CONF_CRYSTAL_BALL: True,
            CONF_POLITICOS: "cookpolitical.com\n@kkondik.bsky.social\nhttps://bsky.app/profile/gone.bsky.social",
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

    # --- 0.3 sources ----------------------------------------------------------
    latest = states["sensor.boiler_room_floor_votes_latest_house_vote"]
    assert latest.state == "Passed: H.R. 101"
    assert latest.attributes["totals"]["yea"] == 208
    assert [r["roll"] for r in latest.attributes["recent"]] == [301, 300]
    assert latest.attributes["recent"][0]["rep_vote"] == "Yea"
    my_vote = states["sensor.boiler_room_floor_votes_your_representative_s_last_vote"]
    assert my_vote.state == "Yea"
    assert my_vote.attributes["representative"] == "Cal Cole"
    # Independents split 3-1 Yea, so Yea is with the party.
    assert my_vote.attributes["with_party"] is True
    unity = states["sensor.boiler_room_floor_votes_your_representative_s_party_line_votes"]
    assert float(unity.state) == 100.0

    money = states["sensor.boiler_room_campaign_finance_top_fundraiser_your_house_race"]
    assert money.state == "Cal Cole"
    assert money.attributes["race"] == "NY-24"
    assert [c["name"] for c in money.attributes["candidates"]] == ["Cal Cole", "Jane Q Smith"]
    assert money.attributes["candidates"][1]["party"] == "Democratic"
    senate_money = states["sensor.boiler_room_campaign_finance_top_fundraiser_your_senate_race"]
    assert senate_money.state == "No candidates filed"
    assert "sensor.boiler_room_campaign_finance_top_fundraiser_presidential_race" not in states

    exec_latest = states["sensor.boiler_room_executive_actions_latest_executive_action"]
    assert exec_latest.state == "Protecting Test Data"
    assert exec_latest.attributes["number"] == "14400"
    assert states["sensor.boiler_room_executive_actions_executive_orders_this_year"].state == "37"

    kalshi = states["sensor.boiler_room_prediction_markets_who_wins_the_test_seat"]
    assert kalshi.state == "Cal Cole"
    assert kalshi.attributes["source"] == "Kalshi"
    assert kalshi.attributes["outcomes"] == {"Cal Cole": 61.0, "Jane Smith": 39.0}
    assert kalshi.attributes["volume_contracts"] == 1500.0

    crystal = states["sensor.boiler_room_race_ratings_crystal_ball_latest"]
    assert crystal.state == "Blocked by the site"
    assert crystal.attributes["blocked"] is True
    assert ir.async_get(hass).async_get_issue(DOMAIN, "crystal_ball_blocked")

    politico = states["sensor.boiler_room_news_latest_politico_post"]
    assert politico.state == "Kyle Kondik: Crystal Ball update"
    assert politico.attributes["entity_picture"] == "https://cdn/kkondik.bsky.social.jpg"
    # Reposts of other accounts are dropped; the unknown handle is reported, not fatal.
    assert [p["author"] for p in politico.attributes["posts"]] == [
        "Kyle Kondik",
        "The Cook Political Report",
    ]
    assert (
        politico.attributes["posts"][0]["url"]
        == "https://bsky.app/profile/kkondik.bsky.social/post/k1"
    )
    assert politico.attributes["posts"][0]["link_title"] == "An analysis"
    assert politico.attributes["failed_accounts"] == ["gone.bsky.social"]

    # --- Changes fire bus events -------------------------------------------
    bill_events = async_capture_events(hass, EVENT_BILL_ACTION)
    odds_events = async_capture_events(hass, EVENT_ODDS_MOVED)
    news_events = async_capture_events(hass, EVENT_NEW_HEADLINE)
    rating_events = async_capture_events(hass, EVENT_RATING_CHANGED)
    vote_events = async_capture_events(hass, EVENT_REP_VOTE)
    fec_events = async_capture_events(hass, EVENT_FEC_REPORT)
    exec_events = async_capture_events(hass, EVENT_EXECUTIVE_ACTION)
    politico_events = async_capture_events(hass, EVENT_POLITICO_POST)

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
    # A new roll call where the rep breaks with the Independents (3 Yea, 1 Nay).
    _mock_votes(aioclient_mock, [(300, "100", "Yea"), (301, "101", "Yea"), (302, "102", "Nay")])
    aioclient_mock.get(
        f"{FEC}/elections/",
        params={"office": "house"},
        json=_fec(through="2026-11-23", raised=3500000.28),
    )
    _mock_fr(
        aioclient_mock,
        ("Second Order", "2026-2", "Executive Order"),
        ("Protecting Test Data", "2026-1", "Executive Order"),
    )
    aioclient_mock.get(f"{KALSHI}/events/KXTEST-26", json=_kalshi(lead="0.5000"))
    _mock_bsky(
        aioclient_mock,
        [
            _bsky_post(
                "cookpolitical.com",
                "The Cook Political Report",
                "c2",
                "Rating change: NY-22",
                "2026-11-03T14:00:00Z",
            )
        ],
    )
    _mock_all(aioclient_mock)

    data = entry.runtime_data
    for coordinator in (
        data.federal_bills,
        data.markets,
        data.news,
        data.cook,
        data.floor_votes,
        data.finance,
        data.executive,
        data.politicos,
    ):
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert [e.data["action"] for e in bill_events] == ["Passed House."]
    assert sorted((e.data["outcome"], e.data["change"]) for e in odds_events) == [
        ("Cal Cole", -11.0),
        ("No", -15.0),
        ("Yes", 15.0),
    ]
    assert [(e.data["roll"], e.data["vote"], e.data["with_party"]) for e in vote_events] == [
        (302, "Nay", False)
    ]
    assert [(e.data["candidate"], e.data["raised_since_last"]) for e in fec_events] == [
        ("Cal Cole", 259174.0),
        ("Jane Q Smith", 0.0),
    ]
    assert [e.data["title"] for e in exec_events] == ["Second Order"]
    assert [(e.data["author"], e.data["text"]) for e in politico_events] == [
        ("The Cook Political Report", "Rating change: NY-22")
    ]
    unity = hass.states.get("sensor.boiler_room_floor_votes_your_representative_s_party_line_votes")
    assert float(unity.state) == 66.7
    assert [v["roll"] for v in unity.attributes["broke_with_party"]] == [302]
    assert [e.data["title"] for e in news_events] == ["Breaking headline"]
    assert [(e.data["race"], e.data["from"], e.data["to"]) for e in rating_events] == [
        ("NY-24", "Lean R", "Toss Up")
    ]
    assert hass.states.get("sensor.boiler_room_race_ratings_cook_house_toss_ups").state == "2"
    assert hass.states.get("sensor.boiler_room_legislation_h_r_1234").state == "Passed House."

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_setup_with_no_keys(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Without keys you still get news, executive actions + the federal election calendar."""
    aioclient_mock.get(FEED, text=_rss(("Hi", "https://x/1", "Tue, 03 Nov 2026 06:00:00 -0500")))
    _mock_fr(aioclient_mock)
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
        "sensor.boiler_room_executive_actions_latest_executive_action",
        "sensor.boiler_room_executive_actions_executive_orders_this_year",
        "sensor.boiler_room_news_latest_politico_post",
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
    _mock_fr(aioclient_mock)
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
    _mock_fr(aioclient_mock)
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


async def test_new_sources_fail_soft(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A dead Federal Register doesn't stop the rest of Boiler Room from loading."""
    aioclient_mock.get(FEED, text=_rss(("Hi", "https://x/1", "Tue, 03 Nov 2026 06:00:00 -0500")))
    aioclient_mock.get(FR, status=500)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={},
        options={CONF_NEWS_SOURCES: [], CONF_CUSTOM_FEEDS: FEED},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.boiler_room_news_latest_headline").state == "Hi"
    assert (
        hass.states.get("sensor.boiler_room_executive_actions_latest_executive_action").state
        == "unavailable"
    )


async def test_crystal_ball_feed_when_allowed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """When the site serves the feed, posts show up, rating posts are flagged, and the
    repair issue clears."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        "crystal_ball_blocked",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="crystal_ball_blocked",
    )
    aioclient_mock.get(
        CRYSTAL_BALL_FEED,
        text=_rss(
            ("Four Rating Changes in the House", "https://cb/2", "Mon, 02 Nov 2026 10:00:00 -0500"),
            (
                "The Importance of Not Being Hated",
                "https://cb/1",
                "Thu, 29 Oct 2026 10:00:00 -0500",
            ),
        ),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={},
        options={
            CONF_NEWS_SOURCES: [],
            CONF_CRYSTAL_BALL: True,
            CONF_EXECUTIVE: False,
            CONF_CAMPAIGN_FINANCE: False,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    crystal = hass.states.get("sensor.boiler_room_race_ratings_crystal_ball_latest")
    assert crystal.state == "Four Rating Changes in the House"
    assert [p["rating_change"] for p in crystal.attributes["posts"]] == [True, False]
    assert ir.async_get(hass).async_get_issue(DOMAIN, "crystal_ball_blocked") is None

    posts = async_capture_events(hass, EVENT_CRYSTAL_BALL_POST)
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        CRYSTAL_BALL_FEED,
        text=_rss(
            ("Senate Rating Change: Maine", "https://cb/3", "Wed, 04 Nov 2026 10:00:00 -0500"),
            ("Four Rating Changes in the House", "https://cb/2", "Mon, 02 Nov 2026 10:00:00 -0500"),
        ),
    )
    await entry.runtime_data.crystal_ball.async_refresh()
    await hass.async_block_till_done()
    assert [(e.data["title"], e.data["rating_change"]) for e in posts] == [
        ("Senate Rating Change: Maine", True)
    ]


async def test_politicos_can_be_turned_off(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(FEED, text=_rss(("Hi", "https://x/1", "Tue, 03 Nov 2026 06:00:00 -0500")))
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={},
        options={
            CONF_NEWS_SOURCES: [],
            CONF_CUSTOM_FEEDS: FEED,
            CONF_POLITICOS: "None",
            CONF_EXECUTIVE: False,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.politicos is None
    assert hass.states.get("sensor.boiler_room_news_latest_politico_post") is None


async def test_per_feed_keywords_only_filter_that_feed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    magazine = "https://magazine.example/feed"
    aioclient_mock.get(
        FEED, text=_rss(("Budget talks stall", "https://x/1", "Tue, 03 Nov 2026 06:00:00 -0500"))
    )
    aioclient_mock.get(
        magazine,
        text=_rss(
            ("Midterm model update", "https://m/1", "Tue, 03 Nov 2026 07:00:00 -0500"),
            ("Hannah Arendt hates you", "https://m/2", "Tue, 03 Nov 2026 08:00:00 -0500"),
        ),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={},
        options={
            CONF_NEWS_SOURCES: [],
            CONF_CUSTOM_FEEDS: f"{FEED}\n{magazine} | midterm, election",
            CONF_POLITICOS: "none",
            CONF_EXECUTIVE: False,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    titles = [
        h["title"]
        for h in hass.states.get("sensor.boiler_room_news_latest_headline").attributes["headlines"]
    ]
    # The magazine's off-topic essay is dropped; the unfiltered feed keeps everything.
    assert titles == ["Midterm model update", "Budget talks stall"]
