"""Parser tests. No Home Assistant needed."""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.boiler_room.parsers import (
    assign_rep_slots,
    computed_federal_elections,
    current_congress,
    federal_election_day,
    merge_elections,
    merge_headlines,
    parse_civic_elections,
    parse_event,
    parse_federal_bill,
    parse_feed,
    parse_market,
    parse_market_ref,
    parse_state_bill,
    split_list,
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
