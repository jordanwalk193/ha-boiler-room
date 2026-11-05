"""Parser tests. No Home Assistant needed."""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.boiler_room.api import is_bot_challenge
from custom_components.boiler_room.parsers import (
    assign_rep_slots,
    computed_federal_elections,
    current_congress,
    current_session,
    fec_cycle,
    fec_display_name,
    fec_party,
    federal_election_day,
    is_rating_post,
    merge_elections,
    merge_headlines,
    newest_rolls,
    parse_civic_elections,
    parse_event,
    parse_fec_race,
    parse_federal_bill,
    parse_feed,
    parse_house_vote,
    parse_kalshi_event,
    parse_kalshi_ref,
    parse_market,
    parse_market_ref,
    parse_presidential_documents,
    parse_state_bill,
    party_unity,
    split_list,
    vote_bill_label,
    with_party,
)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 11, 3), 119),
        (date(2025, 1, 2), 118),
        (date(2025, 1, 3), 119),
        (date(2027, 1, 3), 120),
    ],
)
def test_current_congress(day, expected):
    assert current_congress(day) == expected


@pytest.mark.parametrize(
    ("text", "key", "label"),
    [
        ("HR 1234", "119-hr-1234", "H.R. 1234"),
        ("H.R.1234", "119-hr-1234", "H.R. 1234"),
        ("s.55", "119-s-55", "S. 55"),
        ("118/hr/815", "118-hr-815", "H.R. 815"),
        ("hjres12-118", "118-hjres-12", "H.J.Res. 12"),
        ("S. Res. 9", "119-sres-9", "S.Res. 9"),
        (
            "https://www.congress.gov/bill/118th-congress/house-bill/815/text",
            "118-hr-815",
            "H.R. 815",
        ),
    ],
)
def test_parse_federal_bill(text, key, label):
    ref = parse_federal_bill(text, 119)
    assert ref is not None
    assert ref.key == key
    assert ref.label == label


def test_parse_federal_bill_rejects_junk():
    assert parse_federal_bill("hello", 119) is None
    assert parse_federal_bill("", 119) is None


def test_federal_bill_url():
    ref = parse_federal_bill("hr 1", 121)
    assert ref.public_url == "https://www.congress.gov/bill/121st-congress/house-bill/1"


@pytest.mark.parametrize(
    ("text", "state", "identifier"),
    [
        ("S1234", None, "S 1234"),
        ("a 5678", None, "A 5678"),
        ("ny:S1234", "ny", "S 1234"),
        ("HB.0042", None, "HB 42"),
    ],
)
def test_parse_state_bill(text, state, identifier):
    ref = parse_state_bill(text)
    assert ref is not None
    assert (ref.state, ref.identifier) == (state, identifier)


def test_split_list():
    assert split_list("a, b\nc,,\n") == ["a", "b", "c"]
    assert split_list(None) == []


def test_parse_market_ref():
    assert parse_market_ref("https://polymarket.com/event/balance-of-power-2026?tid=1") == (
        "balance-of-power-2026"
    )
    assert parse_market_ref("polymarket.com/market/will-x-happen") == "will-x-happen"
    assert parse_market_ref("Some-Slug") == "some-slug"
    assert parse_market_ref("not a slug!") is None


def test_parse_binary_market():
    item = parse_market(
        {
            "question": "Will it pass?",
            "slug": "will-it-pass",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.235", "0.765"]',
            "volume": "12345.678",
        }
    )
    assert item["probability"] == 23.5
    assert item["leader"] == "No"
    assert item["outcomes"] == {"Yes": 23.5, "No": 76.5}
    assert item["volume"] == 12345.68


def test_parse_event_ranks_candidates():
    item = parse_event(
        {
            "title": "Who wins?",
            "slug": "who-wins",
            "markets": [
                {
                    "groupItemTitle": "Alpha",
                    "outcomes": '["Yes","No"]',
                    "outcomePrices": '["0.3","0.7"]',
                },
                {
                    "groupItemTitle": "Beta",
                    "outcomes": '["Yes","No"]',
                    "outcomePrices": '["0.6","0.4"]',
                },
                {
                    "groupItemTitle": "Gone",
                    "closed": True,
                    "outcomes": '["Yes","No"]',
                    "outcomePrices": '["0","1"]',
                },
            ],
        }
    )
    assert item["leader"] == "Beta"
    assert item["probability"] == 60.0
    assert list(item["outcomes"]) == ["Beta", "Alpha"]


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Older &lt;b&gt;story&lt;/b&gt;</title><link>https://a/1</link>
<pubDate>Mon, 02 Nov 2026 18:55:15 -0500</pubDate></item>
<item><title>Newer story</title><link>https://a/2</link>
<pubDate>Tue, 03 Nov 2026 06:43:49 -0500</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Reddit post</title><link href="https://r/1"/>
<updated>2026-11-03T12:00:00+00:00</updated></entry>
<entry><title>Newer story</title><link href="https://dupe"/>
<updated>2026-11-01T12:00:00+00:00</updated></entry></feed>"""


def test_parse_feeds_and_merge():
    rss = parse_feed(RSS, "Outlet")
    atom = parse_feed(ATOM, "reddit r/x")
    assert rss[0].title == "Older story"
    assert atom[0].link == "https://r/1"
    merged = merge_headlines([rss, atom], [], 10)
    assert [h.title for h in merged] == ["Reddit post", "Newer story", "Older story"]
    assert merge_headlines([rss, atom], ["older"], 10)[0].title == "Older story"
    assert len(merge_headlines([rss, atom], [], 1)) == 1


def test_assign_rep_slots():
    people = [
        {
            "name": "Zed Senator",
            "jurisdiction": {"classification": "country"},
            "current_role": {
                "org_classification": "upper",
                "division_id": "ocd-division/country:us/state:ny",
            },
        },
        {
            "name": "Amy Senator",
            "jurisdiction": {"classification": "country"},
            "current_role": {
                "org_classification": "upper",
                "division_id": "ocd-division/country:us/state:ny",
            },
        },
        {
            "name": "House Person",
            "jurisdiction": {"classification": "country"},
            "current_role": {
                "org_classification": "lower",
                "district": "NY-24",
                "division_id": "ocd-division/country:us/state:ny/cd:24",
            },
        },
        {
            "name": "Assembly Person",
            "jurisdiction": {"classification": "state"},
            "current_role": {
                "org_classification": "lower",
                "title": "Assembly Member",
                "division_id": "ocd-division/country:us/state:ny/sldl:126",
            },
        },
    ]
    slots = assign_rep_slots(people)
    assert slots["us_senator_1"]["name"] == "Amy Senator"
    assert slots["us_senator_2"]["name"] == "Zed Senator"
    assert slots["us_representative"]["congressional_district"] == 24
    assert slots["state_representative"]["title"] == "Assembly Member"
    assert slots["state_representative"]["state"] == "ny"
    assert "state_senator" not in slots


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2024, date(2024, 11, 5)),
        (2026, date(2026, 11, 3)),
        (2028, date(2028, 11, 7)),
        (2030, date(2030, 11, 5)),
    ],
)
def test_federal_election_day(year, expected):
    assert federal_election_day(year) == expected


def test_elections_merge():
    computed = computed_federal_elections(date(2026, 6, 1))
    assert [e["name"] for e in computed] == [
        "2026 Midterm General Election",
        "2028 Presidential General Election",
        "2030 Midterm General Election",
    ]
    civic = parse_civic_elections(
        {
            "elections": [
                {
                    "id": "2000",
                    "name": "VIP Test Election",
                    "electionDay": "2030-06-06",
                    "ocdDivisionId": "ocd-division/country:us",
                },
                {
                    "id": "9",
                    "name": "New York General Election",
                    "electionDay": "2026-11-03",
                    "ocdDivisionId": "ocd-division/country:us/state:ny",
                },
                {
                    "id": "10",
                    "name": "Texas Runoff",
                    "electionDay": "2026-12-12",
                    "ocdDivisionId": "ocd-division/country:us/state:tx",
                },
                {
                    "id": "11",
                    "name": "US General Election",
                    "electionDay": "2026-11-03",
                    "ocdDivisionId": "ocd-division/country:us",
                },
            ]
        },
        "ny",
    )
    assert {e["name"] for e in civic} == {"New York General Election", "US General Election"}
    merged = merge_elections(computed, civic)
    names_2026 = [e["name"] for e in merged if e["date"] == date(2026, 11, 3)]
    assert names_2026 == ["New York General Election", "US General Election"]


# --- Cook Political Report ------------------------------------------------

from custom_components.boiler_room.parsers import (  # noqa: E402
    cook_race_key,
    cook_records,
    normalize_rating,
    parse_cook_race,
    summarize_ratings,
)


@pytest.mark.parametrize(
    "payload",
    [
        [{"Title": "NY-24"}],
        {"data": [{"Title": "NY-24"}]},
        {"races": [{"Title": "NY-24"}], "count": 1},
        {"0": {"Title": "NY-24"}},
    ],
)
def test_cook_records_envelopes(payload):
    assert cook_records(payload) == [{"Title": "NY-24"}]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("toss-up", "Toss Up"),
        ("TOSSUP", "Toss Up"),
        ("lean r", "Lean R"),
        ("Solid_D", "Solid D"),
        (None, None),
    ],
)
def test_normalize_rating(raw, expected):
    assert normalize_rating(raw) == expected


def test_parse_cook_house_race_from_title():
    race = parse_cook_race(
        {
            "Title": "NY-24 (Tenney)",
            "Incumbent": "Claudia Tenney",
            "Rating": "Solid R",
            "Cook PVI": "R+11",
        },
        "house",
    )
    assert (race["state"], race["district"], race["rating"], race["pvi"]) == (
        "ny",
        24,
        "Solid R",
        "R+11",
    )
    assert cook_race_key(race) == "house-ny-24"


def test_parse_cook_at_large_and_state_names():
    race = parse_cook_race({"title": "AK-AL", "rating": "Lean R"}, "house")
    assert (race["state"], race["district"]) == ("ak", 0)
    senate = parse_cook_race(
        {"race_title": "Georgia", "state": "Georgia", "race_rating": "Toss Up"}, "senate"
    )
    assert senate["state"] == "ga"
    assert senate["district"] is None


def test_summarize_ratings_orders_scale():
    races = [
        parse_cook_race({"Title": t, "Rating": r}, "house")
        for t, r in [
            ("NY-1", "Lean R"),
            ("NY-2", "Toss Up"),
            ("NY-3", "Solid D"),
            ("NY-4", "Toss Up"),
        ]
    ]
    summary = summarize_ratings(races)
    assert summary["toss_ups"] == 2
    assert list(summary["counts"]) == ["Solid D", "Toss Up", "Lean R"]
    assert [r["race"] for r in summary["toss_up_races"]] == ["NY-2", "NY-4"]


# --------------------------------------------------------------------------
# 0.3: Kalshi, floor votes, FEC, Federal Register, Crystal Ball
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("KXNEXTSPEAKER-31", "KXNEXTSPEAKER-31"),
        ("kxpresparty-2028", "KXPRESPARTY-2028"),
        (
            "https://kalshi.com/markets/kxnextspeaker/next-speaker/kxnextspeaker-31",
            "KXNEXTSPEAKER-31",
        ),
        ("https://kalshi.com/markets/kxsenate/ga/kxsenatega-26?ref=x", "KXSENATEGA-26"),
        ("https://kalshi.com/markets/kxnextspeaker", None),
        ("banana", None),
        ("", None),
    ],
)
def test_parse_kalshi_ref(text, expected):
    assert parse_kalshi_ref(text) == expected


def test_kalshi_binary_uses_midpoint_when_untraded():
    item = parse_kalshi_event(
        {
            "event_ticker": "KXONE-26",
            "title": "Will it happen?",
            "markets": [
                {
                    "last_price_dollars": "0",
                    "yes_bid_dollars": "0.20",
                    "yes_ask_dollars": "0.30",
                    "status": "active",
                }
            ],
        }
    )
    assert item["kind"] == "market"
    assert item["outcomes"] == {"Yes": 25.0, "No": 75.0}
    assert item["leader"] == "No"
    assert item["url"] == "https://kalshi.com/markets/kxone"


def test_kalshi_event_skips_settled_markets():
    item = parse_kalshi_event(
        {
            "event_ticker": "KXRACE-26",
            "mutually_exclusive": True,
            "markets": [
                {"yes_sub_title": "A", "last_price_dollars": "0.55", "status": "active"},
                {"yes_sub_title": "B", "last_price_dollars": "0.40", "status": "active"},
                {"yes_sub_title": "C", "last_price_dollars": "0.01", "status": "finalized"},
            ],
        }
    )
    assert item["outcomes"] == {"A": 55.0, "B": 40.0}
    assert item["closed"] is False


@pytest.mark.parametrize(
    ("day", "session"),
    [(date(2025, 3, 1), 1), (date(2026, 11, 5), 2), (date(2027, 1, 2), 2), (date(2027, 1, 3), 1)],
)
def test_current_session(day, session):
    assert current_session(day) == session


def test_newest_rolls_sorts_unordered_list():
    items = [
        {"rollCallNumber": 74, "startDate": "2026-02-24T10:00:00-05:00"},
        {"rollCallNumber": 306, "startDate": "2026-09-15T18:00:00-04:00"},
        {"rollCallNumber": 305, "startDate": "2026-09-15T18:00:00-04:00"},
        {"noRoll": True},
    ]
    assert [i["rollCallNumber"] for i in newest_rolls(items, 2)] == [306, 305]


@pytest.mark.parametrize(
    ("cast", "totals", "expected"),
    [
        ("Yea", {"yea": 200, "nay": 10}, True),
        ("Nay", {"yea": 200, "nay": 10}, False),
        ("Not Voting", {"yea": 200, "nay": 10}, None),
        ("Yea", {"yea": 5, "nay": 5}, None),
        ("Yea", None, None),
    ],
)
def test_with_party(cast, totals, expected):
    assert with_party(cast, totals) is expected


def test_parse_house_vote_and_unity():
    detail = {
        "rollCallNumber": 12,
        "result": "Failed",
        "voteQuestion": "On Agreeing to the Amendment",
        "legislationType": "HRES",
        "legislationNumber": "9",
        "votePartyTotal": [
            {"voteParty": "D", "yeaTotal": 10, "nayTotal": 200},
            {"voteParty": "R", "yeaTotal": 210, "nayTotal": 2},
        ],
    }
    members = [
        {"bioguideID": "x1", "voteCast": "Aye", "voteParty": "D"},
        {"bioguideID": "X2", "voteCast": "No", "voteParty": "R"},
        {"bioguideID": "X3", "voteCast": "Yea", "voteParty": "R"},
    ]
    vote = parse_house_vote(detail, members, ["X1", "x2"])
    assert vote["bill"] == "H.Res. 9"
    assert vote["totals"]["yea"] == 220
    assert set(vote["members"]) == {"X1", "X2"}  # X3 isn't followed
    assert vote["members"]["X1"] == {"vote": "Yea", "party": "D", "with_party": False}
    assert vote["members"]["X2"]["with_party"] is False
    assert party_unity([vote], "X1") == 0.0
    assert party_unity([vote], "NOPE") is None


def test_vote_bill_label():
    assert vote_bill_label("HR", "3424") == "H.R. 3424"
    assert vote_bill_label("HJRES", 1) == "H.J.Res. 1"
    assert vote_bill_label("QUORUM", 1) == "QUORUM 1"
    assert vote_bill_label(None, None) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MANNION, JOHN W", "John W Mannion"),
        ("OSSOFF, T. JONATHAN", "T. Jonathan Ossoff"),
        ("COLLINS, MICHAEL A JR", "Michael A Collins Jr"),
        ("MCCOLUMN, JONATHAN MR", "Jonathan McColumn"),
        ("O'ROURKE, BETO III", "Beto O'Rourke III"),
        ("OCASIO-CORTEZ, ALEXANDRIA", "Alexandria Ocasio-Cortez"),
        ("PLAIN NAME", "Plain Name"),
        (None, None),
    ],
)
def test_fec_display_name(raw, expected):
    assert fec_display_name(raw) == expected


def test_fec_party_and_cycle():
    assert fec_party("DEMOCRATIC PARTY") == "Democratic"
    assert fec_party("GREEN PARTY") == "Green"
    assert fec_party(None) is None
    assert fec_cycle(date(2026, 11, 5)) == 2026
    assert fec_cycle(date(2027, 2, 1)) == 2028


def test_parse_fec_race_keeps_incumbents_with_no_money():
    race = parse_fec_race(
        [
            {"candidate_name": "A, B", "total_receipts": 10, "candidate_id": "H1"},
            {
                "candidate_name": "C, D",
                "total_receipts": 0,
                "incumbent_challenge_full": "Incumbent",
            },
            {"candidate_name": "E, F", "total_receipts": 0},
            {"candidate_name": "G, H", "total_receipts": 50, "candidate_id": "H4"},
        ]
    )
    assert [c["name"] for c in race] == ["H G", "B A", "D C"]
    assert race[0]["url"] == "https://www.fec.gov/data/candidate/H4/"


def test_parse_presidential_documents():
    docs = parse_presidential_documents(
        {
            "results": [
                {
                    "title": "Doing <b>Things</b>",
                    "subtype": "Executive Order",
                    "executive_order_number": 14400,
                    "document_number": "2026-1",
                    "president": {"name": "P"},
                },
                {"title": "Memo", "document_number": "2026-2"},
            ]
        }
    )
    assert docs[0]["title"] == "Doing Things"
    assert docs[0]["number"] == "14400"
    assert docs[1]["kind"] == "Presidential Document"
    assert parse_presidential_documents(None) == []


def test_bot_challenge_detection():
    assert is_bot_challenge("challenge", "")
    assert is_bot_challenge(None, "<html><head><title>Just a moment...</title>")
    assert not is_bot_challenge(None, "<?xml version='1.0'?><rss></rss>")


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Four Rating Changes Headlined by Alaska Senate", True),
        ("The House: 28 Rating Changes", True),
        ("2026 Final Ratings", True),
        ("The Importance of Not Being Hated", False),
        (None, False),
    ],
)
def test_is_rating_post(title, expected):
    assert is_rating_post(title) is expected
