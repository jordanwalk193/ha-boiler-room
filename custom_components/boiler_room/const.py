"""Constants for Boiler Room."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "boiler_room"
FRONTEND_URL: Final = "/boiler_room/boiler-room.js"

# Config entry data (API keys)
CONF_CONGRESS_KEY: Final = "congress_api_key"
CONF_OPENSTATES_KEY: Final = "openstates_api_key"
CONF_CIVIC_KEY: Final = "civic_api_key"
CONF_COOK_EMAIL: Final = "cook_email"
CONF_COOK_PASSWORD: Final = "cook_password"

# Options
CONF_STATE: Final = "state"
CONF_DISTRICT: Final = "congressional_district"
CONF_FEDERAL_BILLS: Final = "federal_bills"
CONF_STATE_BILLS: Final = "state_bills"
CONF_MARKETS: Final = "markets"
CONF_ODDS_THRESHOLD: Final = "odds_alert_threshold"
CONF_NEWS_SOURCES: Final = "news_sources"
CONF_CUSTOM_FEEDS: Final = "custom_feeds"
CONF_HEADLINE_COUNT: Final = "headline_count"
CONF_NEWS_KEYWORDS: Final = "news_keywords"

STATE_AUTO: Final = "auto"

DEFAULT_HEADLINE_COUNT: Final = 10
DEFAULT_ODDS_THRESHOLD: Final = 5.0

# Update intervals. Open States' free tier is small, so it polls gently.
INTERVAL_REPS: Final = timedelta(hours=24)
INTERVAL_FEDERAL_BILLS: Final = timedelta(minutes=30)
INTERVAL_STATE_BILLS: Final = timedelta(hours=1)
INTERVAL_MARKETS: Final = timedelta(minutes=5)
INTERVAL_NEWS: Final = timedelta(minutes=10)
INTERVAL_ELECTIONS: Final = timedelta(hours=12)
# Cook asks API users to pull at most once a day.
INTERVAL_COOK: Final = timedelta(hours=24)
COOK_CHAMBERS: Final = ("house", "senate", "governor")

# Bus events for automations
EVENT_BILL_ACTION: Final = "boiler_room_bill_action"
EVENT_NEW_HEADLINE: Final = "boiler_room_new_headline"
EVENT_ODDS_MOVED: Final = "boiler_room_odds_moved"
EVENT_RATING_CHANGED: Final = "boiler_room_rating_changed"

USER_AGENT: Final = (
    "HomeAssistant-BoilerRoom/0.2 (+https://github.com/jordanwalk193/ha-boiler-room)"
)

# Established outlets with working RSS feeds, chosen to span the spectrum.
# Anything else (Reddit, local papers, Substacks) can go in custom feeds.
NEWS_SOURCES: Final[dict[str, tuple[str, str]]] = {
    "npr": ("NPR Politics", "https://feeds.npr.org/1014/rss.xml"),
    "pbs": ("PBS News Hour Politics", "https://www.pbs.org/newshour/feeds/rss/politics"),
    "the_hill": ("The Hill", "https://thehill.com/homenews/feed/"),
    "fox": ("Fox News Politics", "https://moxie.foxnews.com/google-publisher/politics.xml"),
    "bbc_us": (
        "BBC News US & Canada",
        "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml",
    ),
    "politico": ("Politico", "https://rss.politico.com/politics-news.xml"),
}
DEFAULT_NEWS_SOURCES: Final = ["npr", "pbs", "the_hill", "fox"]

US_STATES: Final[dict[str, str]] = {
    "al": "Alabama",
    "ak": "Alaska",
    "az": "Arizona",
    "ar": "Arkansas",
    "ca": "California",
    "co": "Colorado",
    "ct": "Connecticut",
    "de": "Delaware",
    "dc": "District of Columbia",
    "fl": "Florida",
    "ga": "Georgia",
    "hi": "Hawaii",
    "id": "Idaho",
    "il": "Illinois",
    "in": "Indiana",
    "ia": "Iowa",
    "ks": "Kansas",
    "ky": "Kentucky",
    "la": "Louisiana",
    "me": "Maine",
    "md": "Maryland",
    "ma": "Massachusetts",
    "mi": "Michigan",
    "mn": "Minnesota",
    "ms": "Mississippi",
    "mo": "Missouri",
    "mt": "Montana",
    "ne": "Nebraska",
    "nv": "Nevada",
    "nh": "New Hampshire",
    "nj": "New Jersey",
    "nm": "New Mexico",
    "ny": "New York",
    "nc": "North Carolina",
    "nd": "North Dakota",
    "oh": "Ohio",
    "ok": "Oklahoma",
    "or": "Oregon",
    "pa": "Pennsylvania",
    "pr": "Puerto Rico",
    "ri": "Rhode Island",
    "sc": "South Carolina",
    "sd": "South Dakota",
    "tn": "Tennessee",
    "tx": "Texas",
    "ut": "Utah",
    "vt": "Vermont",
    "va": "Virginia",
    "wa": "Washington",
    "wv": "West Virginia",
    "wi": "Wisconsin",
    "wy": "Wyoming",
}

# Representative slots. Fixed keys keep entity IDs stable across redistricting.
REP_SLOTS: Final = (
    "us_senator_1",
    "us_senator_2",
    "us_representative",
    "state_senator",
    "state_representative",
)
