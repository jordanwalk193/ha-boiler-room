"""Pure parsing helpers. No Home Assistant imports, so they're easy to test."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
import json
import re
from typing import Any
import xml.etree.ElementTree as ET

# --------------------------------------------------------------------------
# Congress
# --------------------------------------------------------------------------

BILL_TYPES: dict[str, str] = {
    "hr": "house-bill",
    "s": "senate-bill",
    "hjres": "house-joint-resolution",
    "sjres": "senate-joint-resolution",
    "hconres": "house-concurrent-resolution",
    "sconres": "senate-concurrent-resolution",
    "hres": "house-resolution",
    "sres": "senate-resolution",
}
_SLUG_TO_TYPE = {v: k for k, v in BILL_TYPES.items()}
BILL_LABELS: dict[str, str] = {
    "hr": "H.R.",
    "s": "S.",
    "hjres": "H.J.Res.",
    "sjres": "S.J.Res.",
    "hconres": "H.Con.Res.",
    "sconres": "S.Con.Res.",
    "hres": "H.Res.",
    "sres": "S.Res.",
}

_BILL_URL_RE = re.compile(r"congress\.gov/bill/(\d+)[a-z]*-congress/([a-z-]+)/(\d+)")
_BILL_RE = re.compile(
    r"^(?:(\d{2,3})[/-])?(hconres|hjres|hres|hr|sconres|sjres|sres|s)[/-]?(\d+)(?:[/-](\d{2,3}))?$"
)


def current_congress(today: date) -> int:
    """Return the Congress in session on a date. Each one starts Jan 3 of an odd year."""
    year = today.year
    if year % 2 == 1 and (today.month, today.day) < (1, 3):
        year -= 1
    return (year - 1789) // 2 + 1


@dataclass(frozen=True)
class FederalBillRef:
    """A federal bill reference."""

    congress: int
    bill_type: str
    number: int

    @property
    def key(self) -> str:
        return f"{self.congress}-{self.bill_type}-{self.number}"

    @property
    def label(self) -> str:
        return f"{BILL_LABELS[self.bill_type]} {self.number}"

    @property
    def public_url(self) -> str:
        return (
            f"https://www.congress.gov/bill/{_ordinal(self.congress)}-congress/"
            f"{BILL_TYPES[self.bill_type]}/{self.number}"
        )


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def parse_federal_bill(text: str, default_congress: int) -> FederalBillRef | None:
    """Parse 'HR 1234', 'H.R.1234', '119/hr/1234', 'hr1234-118' or a congress.gov URL."""
    raw = text.strip().lower()
    if not raw:
        return None
    if match := _BILL_URL_RE.search(raw):
        congress, slug, number = match.groups()
        if slug not in _SLUG_TO_TYPE:
            return None
        return FederalBillRef(int(congress), _SLUG_TO_TYPE[slug], int(number))
    compact = re.sub(r"[\s.]", "", raw)
    if not (match := _BILL_RE.match(compact)):
        return None
    lead, bill_type, number, trail = match.groups()
    congress = int(lead or trail or default_congress)
    return FederalBillRef(congress, bill_type, int(number))


# --------------------------------------------------------------------------
# State bills (Open States)
# --------------------------------------------------------------------------

_STATE_BILL_RE = re.compile(r"^(?:([a-z]{2})\s*:\s*)?([a-z]+)\s*\.?\s*(\d+)$", re.I)


@dataclass(frozen=True)
class StateBillRef:
    """A state bill reference. `state` may be None (use the configured state)."""

    state: str | None
    identifier: str

    @property
    def key(self) -> str:
        return f"{self.state or 'home'}-{self.identifier.replace(' ', '').lower()}"


def parse_state_bill(text: str) -> StateBillRef | None:
    """Parse 'S1234', 'A 5678', 'ny:S1234' into a normalized reference."""
    match = _STATE_BILL_RE.match(text.strip())
    if not match:
        return None
    state, letters, number = match.groups()
    return StateBillRef(state.lower() if state else None, f"{letters.upper()} {int(number)}")


def split_list(text: str | None) -> list[str]:
    """Split a comma- or newline-separated option into clean items."""
    if not text:
        return []
    return [item.strip() for item in re.split(r"[,\n]", text) if item.strip()]


# --------------------------------------------------------------------------
# Representatives (Open States people.geo)
# --------------------------------------------------------------------------

_DIVISION_STATE_RE = re.compile(r"state:([a-z]{2})")
_DIVISION_CD_RE = re.compile(r"/cd:(\d+)")


def state_from_division(division_id: str | None) -> str | None:
    if division_id and (match := _DIVISION_STATE_RE.search(division_id)):
        return match.group(1)
    return None


def assign_rep_slots(people: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Sort Open States people.geo results into fixed slots."""
    slots: dict[str, dict[str, Any]] = {}
    senators: list[dict[str, Any]] = []
    for person in people:
        role = person.get("current_role") or {}
        jurisdiction = person.get("jurisdiction") or {}
        chamber = role.get("org_classification")
        division = role.get("division_id") or ""
        rep = {
            "name": person.get("name"),
            "party": person.get("party"),
            "title": role.get("title"),
            "district": role.get("district"),
            "division_id": division,
            "state": state_from_division(division),
            "email": person.get("email") or None,
            "image": person.get("image") or None,
            "openstates_url": person.get("openstates_url"),
            "openstates_id": person.get("id"),
        }
        federal = jurisdiction.get("classification") == "country"
        if federal and chamber == "upper":
            senators.append(rep)
        elif federal and chamber == "lower":
            match = _DIVISION_CD_RE.search(division)
            rep["congressional_district"] = int(match.group(1)) if match else 0
            slots["us_representative"] = rep
        elif chamber == "upper":
            slots["state_senator"] = rep
        elif chamber == "lower":
            slots["state_representative"] = rep
    for idx, senator in enumerate(sorted(senators, key=lambda s: s["name"] or "")[:2]):
        slots[f"us_senator_{idx + 1}"] = senator
    return slots


# --------------------------------------------------------------------------
# Polymarket
# --------------------------------------------------------------------------

_POLY_URL_RE = re.compile(r"polymarket\.com/(?:[a-z-]+/)?(event|market)/([a-z0-9-]+)", re.I)


def parse_market_ref(text: str) -> str | None:
    """Return a Polymarket slug from a slug or a polymarket.com URL."""
    raw = text.strip()
    if match := _POLY_URL_RE.search(raw):
        return match.group(2).lower()
    if re.fullmatch(r"[a-z0-9-]+", raw, re.I):
        return raw.lower()
    return None


def _decode(value: Any) -> list[Any]:
    """Gamma returns outcomes/outcomePrices as JSON-encoded strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def _pct(price: Any) -> float | None:
    try:
        return round(float(price) * 100, 1)
    except (TypeError, ValueError):
        return None


def parse_market(market: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single Gamma market."""
    names = [str(n) for n in _decode(market.get("outcomes"))]
    prices = [_pct(p) for p in _decode(market.get("outcomePrices"))]
    outcomes = {n: p for n, p in zip(names, prices, strict=False) if p is not None}
    yes = outcomes.get("Yes")
    if yes is not None:
        leader: str | None = "Yes" if yes >= 50 else "No"
        probability = yes
    elif outcomes:
        leader = max(outcomes, key=outcomes.__getitem__)
        probability = outcomes[leader]
    else:
        leader, probability = None, None
    return {
        "kind": "market",
        "slug": market.get("slug"),
        "title": market.get("question"),
        "probability": probability,
        "leader": leader,
        "outcomes": outcomes,
        "volume": _float(market.get("volume")),
        "end_date": market.get("endDate"),
        "closed": bool(market.get("closed")),
        "url": f"https://polymarket.com/market/{market.get('slug')}",
    }


def parse_event(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Gamma event made of several Yes/No markets (one per candidate)."""
    outcomes: dict[str, float] = {}
    for market in event.get("markets") or []:
        if market.get("closed") and not event.get("closed"):
            continue
        parsed = parse_market(market)
        label = market.get("groupItemTitle") or parsed["title"]
        yes = parsed["outcomes"].get("Yes")
        if label and yes is not None:
            outcomes[label] = yes
    ranked = dict(sorted(outcomes.items(), key=lambda kv: kv[1], reverse=True))
    leader = next(iter(ranked), None)
    return {
        "kind": "event",
        "slug": event.get("slug"),
        "title": event.get("title"),
        "probability": ranked.get(leader) if leader else None,
        "leader": leader,
        "outcomes": ranked,
        "volume": _float(event.get("volume")),
        "end_date": event.get("endDate"),
        "closed": bool(event.get("closed")),
        "url": f"https://polymarket.com/event/{event.get('slug')}",
    }


def _float(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# News (RSS 2.0 and Atom)
# --------------------------------------------------------------------------

_ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass
class Headline:
    title: str
    link: str
    source: str
    published: datetime | None = None
    summary: str | None = field(default=None, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "link": self.link,
            "source": self.source,
            "published": self.published.isoformat() if self.published else None,
        }


def _parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_feed(body: str, source: str) -> list[Headline]:
    """Parse an RSS 2.0 or Atom document into headlines."""
    root = ET.fromstring(body)
    items: list[Headline] = []
    for item in root.iter("item"):
        title = _clean(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        if title and link:
            items.append(Headline(title, link, source, _parse_date(item.findtext("pubDate"))))
    for entry in root.iter(f"{_ATOM}entry"):
        title = _clean(entry.findtext(f"{_ATOM}title"))
        link_el = entry.find(f"{_ATOM}link[@rel='alternate']")
        if link_el is None:
            link_el = entry.find(f"{_ATOM}link")
        link = link_el.get("href", "").strip() if link_el is not None else ""
        published = entry.findtext(f"{_ATOM}published") or entry.findtext(f"{_ATOM}updated")
        if title and link:
            items.append(Headline(title, link, source, _parse_date(published)))
    return items


def merge_headlines(
    groups: list[list[Headline]], keywords: list[str], limit: int
) -> list[Headline]:
    """Merge feeds newest-first, drop duplicates, apply an optional keyword filter."""
    seen: set[str] = set()
    merged: list[Headline] = []
    words = [w.lower() for w in keywords]
    epoch = datetime.min.replace(tzinfo=UTC)
    for headline in sorted(
        (h for group in groups for h in group),
        key=lambda h: h.published or epoch,
        reverse=True,
    ):
        dedupe = headline.title.lower()
        if headline.link in seen or dedupe in seen:
            continue
        if words and not any(w in headline.title.lower() for w in words):
            continue
        seen.update((headline.link, dedupe))
        merged.append(headline)
        if len(merged) >= limit:
            break
    return merged


# --------------------------------------------------------------------------
# Elections
# --------------------------------------------------------------------------


def federal_election_day(year: int) -> date:
    """The Tuesday after the first Monday in November (2 U.S.C. § 7)."""
    first = date(year, 11, 1)
    first_monday = first + timedelta(days=(0 - first.weekday()) % 7)
    return first_monday + timedelta(days=1)


def computed_federal_elections(today: date, years_ahead: int = 4) -> list[dict[str, Any]]:
    """Federal general elections (even years) from this year forward."""
    elections = []
    for year in range(today.year, today.year + years_ahead + 1):
        if year % 2:
            continue
        kind = "Presidential" if year % 4 == 0 else "Midterm"
        elections.append(
            {
                "name": f"{year} {kind} General Election",
                "date": federal_election_day(year),
                "scope": "federal",
                "source": "computed",
            }
        )
    return elections


def parse_civic_elections(payload: dict[str, Any], state: str | None) -> list[dict[str, Any]]:
    """Filter Google Civic electionQuery results to national + the home state."""
    wanted = {"ocd-division/country:us"}
    if state:
        wanted.add(f"ocd-division/country:us/state:{state}")
    out = []
    for election in payload.get("elections") or []:
        if str(election.get("id")) == "2000":  # Google's permanent test election
            continue
        division = election.get("ocdDivisionId") or ""
        if division not in wanted:
            continue
        try:
            day = date.fromisoformat(election.get("electionDay", ""))
        except ValueError:
            continue
        out.append(
            {
                "name": election.get("name"),
                "date": day,
                "scope": "state" if "/state:" in division else "federal",
                "source": "google_civic",
            }
        )
    return out


def merge_elections(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Combine sources; a real listing beats a computed one on the same day."""
    by_key: dict[tuple[date, str], dict[str, Any]] = {}
    for group in groups:
        for election in group:
            key = (election["date"], election["scope"])
            current = by_key.get(key)
            if current is None or current["source"] == "computed":
                by_key[key] = election
    return sorted(by_key.values(), key=lambda e: (e["date"], e["name"] or ""))


# --------------------------------------------------------------------------
# Cook Political Report ratings
# --------------------------------------------------------------------------
# Cook documents the fields (Title, State, Incumbent, Rating, Cook PVI) but not
# their exact JSON spelling, so keys are matched loosely.

_COOK_LIST_KEYS = ("data", "races", "results", "items", "ratings", "nodes")
_DISTRICT_RE = re.compile(r"\b([A-Z]{2})[-\s]?(\d{1,2}|AL)\b")


def cook_records(payload: Any) -> list[dict[str, Any]]:
    """Pull the list of race records out of whatever envelope Cook returns."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in _COOK_LIST_KEYS and isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
        # Some APIs return {"0": {...}, "1": {...}}
        if payload and all(isinstance(v, dict) for v in payload.values()):
            return list(payload.values())
    return []


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z]", "", key.lower())


def _pick(record: dict[str, Any], *names: str) -> Any:
    wanted = {_norm_key(n) for n in names}
    for key, value in record.items():
        if _norm_key(key) in wanted and value not in (None, ""):
            return value.get("name") if isinstance(value, dict) and "name" in value else value
    return None


def normalize_rating(text: Any) -> str | None:
    """'toss-up' / 'TOSSUP' / 'Toss Up' -> 'Toss Up'; 'lean r' -> 'Lean R'."""
    if text in (None, ""):
        return None
    raw = re.sub(r"[\s_-]+", " ", str(text).strip())
    if re.fullmatch(r"(?i)toss ?up", raw):
        return "Toss Up"
    parts = raw.split(" ")
    return " ".join(p.upper() if len(p) == 1 else p.capitalize() for p in parts)


def parse_cook_race(record: dict[str, Any], chamber: str) -> dict[str, Any]:
    title = _clean(str(_pick(record, "title", "race", "name") or ""))
    state = _pick(record, "state", "statecode", "stateabbr", "stateabbreviation")
    district = _pick(record, "district", "cd", "districtnumber")
    if (match := _DISTRICT_RE.search(title)) and chamber == "house":
        state = state or match.group(1)
        district = district if district not in (None, "") else match.group(2)
    if isinstance(district, str) and district.upper() in ("AL", "AT LARGE", "AT-LARGE"):
        district = 0
    try:
        district = int(district) if district not in (None, "") else None
    except (TypeError, ValueError):
        district = None
    state_code = None
    if isinstance(state, str):
        state_code = state.lower() if len(state) == 2 else _STATE_NAMES.get(state.lower())
    return {
        "chamber": chamber,
        "title": title or None,
        "state": state_code,
        "district": district if chamber == "house" else None,
        "incumbent": _clean(str(_pick(record, "incumbent", "member") or "")) or None,
        "rating": normalize_rating(_pick(record, "rating", "cookrating", "racerating")),
        "pvi": _pick(record, "pvi", "cookpvi", "cookpoliticalreportpvi"),
    }


def cook_race_key(race: dict[str, Any]) -> str:
    if race["chamber"] == "house" and race["state"] and race["district"] is not None:
        return f"house-{race['state']}-{race['district']}"
    return f"{race['chamber']}-{race['state'] or ''}-{(race['title'] or '').lower()}"


def summarize_ratings(races: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for race in races:
        if race["rating"]:
            counts[race["rating"]] = counts.get(race["rating"], 0) + 1
    toss_ups = [r for r in races if r["rating"] == "Toss Up"]
    return {
        "toss_ups": len(toss_ups),
        "counts": dict(sorted(counts.items(), key=lambda kv: _rating_order(kv[0]))),
        "toss_up_races": [
            {"race": r["title"], "incumbent": r["incumbent"], "pvi": r["pvi"]} for r in toss_ups
        ],
        "total_rated": sum(counts.values()),
    }


_RATING_ORDER = ["Solid D", "Likely D", "Lean D", "Toss Up", "Lean R", "Likely R", "Solid R"]


def _rating_order(rating: str) -> int:
    return _RATING_ORDER.index(rating) if rating in _RATING_ORDER else len(_RATING_ORDER)


_STATE_NAMES = {
    "alabama": "al",
    "alaska": "ak",
    "arizona": "az",
    "arkansas": "ar",
    "california": "ca",
    "colorado": "co",
    "connecticut": "ct",
    "delaware": "de",
    "district of columbia": "dc",
    "florida": "fl",
    "georgia": "ga",
    "hawaii": "hi",
    "idaho": "id",
    "illinois": "il",
    "indiana": "in",
    "iowa": "ia",
    "kansas": "ks",
    "kentucky": "ky",
    "louisiana": "la",
    "maine": "me",
    "maryland": "md",
    "massachusetts": "ma",
    "michigan": "mi",
    "minnesota": "mn",
    "mississippi": "ms",
    "missouri": "mo",
    "montana": "mt",
    "nebraska": "ne",
    "nevada": "nv",
    "new hampshire": "nh",
    "new jersey": "nj",
    "new mexico": "nm",
    "new york": "ny",
    "north carolina": "nc",
    "north dakota": "nd",
    "ohio": "oh",
    "oklahoma": "ok",
    "oregon": "or",
    "pennsylvania": "pa",
    "rhode island": "ri",
    "south carolina": "sc",
    "south dakota": "sd",
    "tennessee": "tn",
    "texas": "tx",
    "utah": "ut",
    "vermont": "vt",
    "virginia": "va",
    "washington": "wa",
    "west virginia": "wv",
    "wisconsin": "wi",
    "wyoming": "wy",
}
