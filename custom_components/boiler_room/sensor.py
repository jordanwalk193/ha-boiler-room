"""Sensors: representatives, bills, prediction markets, news and elections."""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BoilerRoomConfigEntry
from .const import COOK_CHAMBERS, CRYSTAL_BALL_URL, REP_SLOTS, US_STATES
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
    RepsCoordinator,
    StateBillsCoordinator,
)
from .entity import BoilerRoomEntity, MidnightRefresh, truncate
from .parsers import is_rating_post, party_unity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoilerRoomConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = entry.runtime_data
    entities: list[SensorEntity] = []

    if data.reps:
        entities += [
            RepSensor(data.reps, entry, slot) for slot in REP_SLOTS if slot in data.reps.data
        ]

    if data.federal_bills:
        entities += [
            BillSensor(data.federal_bills, entry, ref.key, ref.label)
            for ref in data.federal_bills.bills
        ]
    if data.state_bills:
        home = data.reps.home_state if data.reps else None
        entities += [
            BillSensor(
                data.state_bills,
                entry,
                ref.key,
                f"{(ref.state or home or '').upper()} {ref.identifier}".strip(),
            )
            for ref in data.state_bills.bills
        ]

    if data.markets:
        for slug in data.markets.slugs:
            item = data.markets.data.get(slug)
            if item and item["kind"] == "event":
                entities.append(EventOddsSensor(data.markets, entry, slug))
            else:
                entities.append(MarketOddsSensor(data.markets, entry, slug))

    if data.news:
        entities.append(HeadlineSensor(data.news, entry))

    entities.append(NextElectionSensor(data.elections, entry))

    if data.cook:
        for chamber in COOK_CHAMBERS:
            entities.append(CookTossUpSensor(data.cook, entry, chamber))
            entities.append(
                CookHomeRaceSensor(
                    data.cook,
                    entry,
                    chamber,
                    data.home_state,
                    data.home_district if chamber == "house" else None,
                )
            )

    if data.crystal_ball:
        entities.append(CrystalBallSensor(data.crystal_ball, entry))

    if data.floor_votes:
        entities.append(LatestHouseVoteSensor(data.floor_votes, entry))
        house = (data.reps.data or {}).get("us_representative") if data.reps else None
        if (house or {}).get("bioguide_id"):
            entities += [
                RepLastVoteSensor(data.floor_votes, entry),
                RepPartyUnitySensor(data.floor_votes, entry),
            ]

    if data.finance:
        entities += [
            FundraisingSensor(data.finance, entry, office) for office in data.finance.races()
        ]

    if data.executive:
        entities += [
            LatestExecutiveActionSensor(data.executive, entry),
            ExecutiveOrderCountSensor(data.executive, entry),
        ]
    async_add_entities(entities)


# --------------------------------------------------------------------------


class RepSensor(BoilerRoomEntity, SensorEntity):
    """Name of whoever holds a seat that represents home."""

    coordinator: RepsCoordinator

    def __init__(self, coordinator: RepsCoordinator, entry, slot: str) -> None:
        super().__init__(coordinator, entry, "reps", f"rep_{slot}")
        self._slot = slot
        self._attr_translation_key = slot

    @property
    def _rep(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get(self._slot)

    @property
    def available(self) -> bool:
        return super().available and self._rep is not None

    @property
    def native_value(self) -> str | None:
        return self._rep["name"] if self._rep else None

    @property
    def entity_picture(self) -> str | None:
        return self._rep.get("image") if self._rep else None

    @property
    def icon(self) -> str | None:
        return None if self.entity_picture else "mdi:account-tie"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        rep = self._rep
        if not rep:
            return None
        attrs = {
            k: rep.get(k)
            for k in (
                "party",
                "title",
                "district",
                "state",
                "email",
                "openstates_url",
                "congress_url",
                "bioguide_id",
            )
            if rep.get(k) not in (None, "")
        }
        if latest := rep.get("latest_sponsored"):
            attrs["latest_sponsored_bill"] = latest.get("bill")
            attrs["latest_sponsored_title"] = latest.get("title")
            attrs["latest_sponsored_introduced"] = latest.get("introduced")
        return attrs


class BillSensor(BoilerRoomEntity, SensorEntity):
    """Latest action on a followed bill."""

    _attr_icon = "mdi:file-document-outline"

    def __init__(
        self,
        coordinator: FederalBillsCoordinator | StateBillsCoordinator,
        entry,
        key: str,
        label: str,
    ) -> None:
        super().__init__(coordinator, entry, "legislation", f"bill_{key}")
        self._key = key
        self._attr_name = label

    @property
    def _bill(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get(self._key)

    @property
    def available(self) -> bool:
        return super().available and self._bill is not None

    @property
    def native_value(self) -> str | None:
        return truncate(self._bill.get("latest_action")) if self._bill else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self._bill:
            return None
        return {
            "level": self.coordinator.level,
            **{k: v for k, v in self._bill.items() if v is not None},
        }


class _OddsBase(BoilerRoomEntity, SensorEntity):
    coordinator: MarketsCoordinator
    _attr_icon = "mdi:chart-line-variant"

    def __init__(self, coordinator: MarketsCoordinator, entry, slug: str) -> None:
        super().__init__(coordinator, entry, "markets", f"market_{slug.replace(':', '_').lower()}")
        self._slug = slug
        item = (coordinator.data or {}).get(slug)
        self._attr_name = truncate(item["title"], 80) if item else slug

    @property
    def _item(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get(self._slug)

    @property
    def available(self) -> bool:
        return super().available and self._item is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        item = self._item
        if not item:
            return None
        source = item.get("source") or "Polymarket"
        return {
            "question": item["title"],
            "leader": item["leader"],
            "outcomes": item["outcomes"],
            # Polymarket reports dollars traded; Kalshi reports contracts.
            ("volume_usd" if source == "Polymarket" else "volume_contracts"): item["volume"],
            "end_date": item["end_date"],
            "closed": item["closed"],
            "url": item["url"],
            "source": source,
        }


class MarketOddsSensor(_OddsBase):
    """Implied probability (%) of 'Yes' (or the leading outcome) for one market."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        return self._item["probability"] if self._item else None


class EventOddsSensor(_OddsBase):
    """Leading outcome of a multi-candidate event; full odds in attributes."""

    @property
    def native_value(self) -> str | None:
        return self._item["leader"] if self._item else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs = super().extra_state_attributes
        if attrs and self._item:
            attrs["leader_probability"] = self._item["probability"]
        return attrs


class HeadlineSensor(BoilerRoomEntity, SensorEntity):
    """Newest headline across the selected outlets; the list lives in attributes."""

    coordinator: NewsCoordinator
    _attr_translation_key = "latest_headline"
    _attr_icon = "mdi:newspaper-variant-outline"

    def __init__(self, coordinator: NewsCoordinator, entry) -> None:
        super().__init__(coordinator, entry, "news", "latest_headline")

    @property
    def native_value(self) -> str | None:
        headlines = self.coordinator.data.headlines if self.coordinator.data else []
        return truncate(headlines[0].title) if headlines else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {
            "headlines": [h.as_dict() for h in data.headlines] if data else [],
            "sources": [name for name, _ in self.coordinator.feeds],
            "failed_sources": data.failed_sources if data else [],
        }


class NextElectionSensor(MidnightRefresh, SensorEntity):
    """Date of the next election that covers home."""

    coordinator: ElectionsCoordinator
    _attr_translation_key = "next_election"
    _attr_device_class = SensorDeviceClass.DATE
    _attr_icon = "mdi:ballot-outline"

    def __init__(self, coordinator: ElectionsCoordinator, entry) -> None:
        super().__init__(coordinator, entry, "elections", "next_election")

    @property
    def _next(self) -> dict[str, Any] | None:
        today = dt_util.now().date()
        return next((e for e in self.coordinator.data or [] if e["date"] >= today), None)

    @property
    def native_value(self) -> date | None:
        return self._next["date"] if self._next else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        nxt = self._next
        if not nxt:
            return None
        today = dt_util.now().date()
        return {
            "name": nxt["name"],
            "days_until": (nxt["date"] - today).days,
            "scope": nxt["scope"],
            "upcoming": [
                {"name": e["name"], "date": e["date"].isoformat(), "scope": e["scope"]}
                for e in (self.coordinator.data or [])[:6]
            ],
            "google_civic": self.coordinator.civic_ok,
        }


class CookTossUpSensor(BoilerRoomEntity, SensorEntity):
    """How many races Cook rates Toss Up; the full rating breakdown in attributes."""

    coordinator: CookCoordinator
    _attr_icon = "mdi:scale-balance"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "races"

    def __init__(self, coordinator: CookCoordinator, entry, chamber: str) -> None:
        super().__init__(coordinator, entry, "ratings", f"cook_{chamber}_toss_ups")
        self._chamber = chamber
        self._attr_translation_key = f"cook_{chamber}_toss_ups"

    @property
    def _summary(self) -> dict[str, Any] | None:
        return self.coordinator.data.summaries.get(self._chamber) if self.coordinator.data else None

    @property
    def available(self) -> bool:
        return super().available and self._summary is not None

    @property
    def native_value(self) -> int | None:
        return self._summary["toss_ups"] if self._summary else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self._summary:
            return None
        return {
            "ratings": self._summary["counts"],
            "toss_up_races": self._summary["toss_up_races"],
            "total_rated": self._summary["total_rated"],
            "source": "Cook Political Report",
        }


class CookHomeRaceSensor(BoilerRoomEntity, SensorEntity):
    """Cook's rating for the race that covers home (House district, Senate, governor)."""

    coordinator: CookCoordinator
    _attr_icon = "mdi:flag-checkered"

    def __init__(
        self,
        coordinator: CookCoordinator,
        entry,
        chamber: str,
        state: str | None,
        district: int | None,
    ) -> None:
        super().__init__(coordinator, entry, "ratings", f"cook_home_{chamber}")
        self._chamber = chamber
        self._state = state
        self._district = district
        self._attr_translation_key = f"cook_home_{chamber}"

    @property
    def _race(self) -> dict[str, Any] | None:
        return self.coordinator.home_race(self._chamber, self._state, self._district)

    @property
    def native_value(self) -> str | None:
        if race := self._race:
            return race["rating"]
        # Not every seat is up every cycle; say so rather than going unavailable.
        return "No race rated" if self.coordinator.data and self._state else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        race = self._race
        if not race:
            return {"state": self._state, "district": self._district}
        return {
            "race": race["title"],
            "incumbent": race["incumbent"],
            "pvi": race["pvi"],
            "state": race["state"],
            "district": race["district"],
            "source": "Cook Political Report",
        }


class CrystalBallSensor(BoilerRoomEntity, SensorEntity):
    """Newest Sabato's Crystal Ball article, when the site lets Boiler Room read its feed."""

    coordinator: CrystalBallCoordinator
    _attr_translation_key = "crystal_ball_latest"
    _attr_icon = "mdi:crystal-ball"

    def __init__(self, coordinator: CrystalBallCoordinator, entry) -> None:
        super().__init__(coordinator, entry, "ratings", "crystal_ball_latest")

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        if data and data.posts:
            return truncate(data.posts[0].title)
        if data and data.blocked:
            return "Blocked by the site"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        posts = data.posts if data else []
        return {
            "posts": [{**p.as_dict(), "rating_change": is_rating_post(p.title)} for p in posts],
            "blocked": data.blocked if data else None,
            "last_success": data.last_success if data else None,
            "ratings_pages": {
                "senate": f"{CRYSTAL_BALL_URL}2026-senate/",
                "house": f"{CRYSTAL_BALL_URL}2026-house/",
                "governor": f"{CRYSTAL_BALL_URL}2026-governor/",
                "president": f"{CRYSTAL_BALL_URL}2028-president/",
                "rating_changes": f"{CRYSTAL_BALL_URL}2026-rating-changes/",
            },
            "source": "Sabato's Crystal Ball, UVA Center for Politics",
        }


# --------------------------------------------------------------------------
# Floor votes
# --------------------------------------------------------------------------


def _vote_row(vote: dict[str, Any], bioguide: str | None) -> dict[str, Any]:
    mine = vote["members"].get(bioguide) if bioguide else None
    return {
        "roll": vote["roll"],
        "date": vote["date"],
        "question": vote["question"],
        "bill": vote["bill"],
        "result": vote["result"],
        "yea": vote["totals"]["yea"],
        "nay": vote["totals"]["nay"],
        "rep_vote": mine["vote"] if mine else None,
        "with_party": mine["with_party"] if mine else None,
        "url": vote["bill_url"] or vote["clerk_url"],
    }


class _VotesBase(BoilerRoomEntity, SensorEntity):
    coordinator: FloorVotesCoordinator

    @property
    def _votes(self) -> list[dict[str, Any]]:
        return self.coordinator.data.votes if self.coordinator.data else []

    @property
    def _bioguide(self) -> str | None:
        return self.coordinator.data.rep_bioguide if self.coordinator.data else None


class LatestHouseVoteSensor(_VotesBase):
    """The newest House roll call; the last several, with your rep's vote, in attributes."""

    _attr_translation_key = "latest_house_vote"
    _attr_icon = "mdi:gavel"

    def __init__(self, coordinator: FloorVotesCoordinator, entry) -> None:
        super().__init__(coordinator, entry, "votes", "latest_house_vote")

    @property
    def native_value(self) -> str | None:
        if not self._votes:
            return None
        vote = self._votes[0]
        what = vote["bill"] or vote["question"] or f"Roll call {vote['roll']}"
        return truncate(f"{vote['result']}: {what}" if vote["result"] else what)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self._votes:
            return None
        vote = self._votes[0]
        return {
            "roll": vote["roll"],
            "date": vote["date"],
            "question": vote["question"],
            "bill": vote["bill"],
            "vote_type": vote["vote_type"],
            "totals": vote["totals"],
            "parties": vote["parties"],
            "url": vote["bill_url"] or vote["clerk_url"],
            "clerk_url": vote["clerk_url"],
            "session": self.coordinator.data.session_label,
            "representative": self.coordinator.data.rep_name,
            "recent": [_vote_row(v, self._bioguide) for v in self._votes],
            "source": "Congress.gov (Clerk of the House)",
        }


class RepLastVoteSensor(_VotesBase):
    """How your U.S. representative voted on the newest roll call."""

    _attr_translation_key = "rep_last_vote"
    _attr_icon = "mdi:hand-back-right-outline"

    def __init__(self, coordinator: FloorVotesCoordinator, entry) -> None:
        super().__init__(coordinator, entry, "votes", "rep_last_vote")

    @property
    def _pair(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        bid = self._bioguide
        for vote in self._votes:
            if bid and (mine := vote["members"].get(bid)):
                return vote, mine
        return None

    @property
    def native_value(self) -> str | None:
        pair = self._pair
        return pair[1]["vote"] if pair else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        pair = self._pair
        if not pair:
            return {
                "representative": self.coordinator.data.rep_name if self.coordinator.data else None
            }
        vote, mine = pair
        return {
            "representative": self.coordinator.data.rep_name,
            "with_party": mine["with_party"],
            "party": mine["party"],
            "roll": vote["roll"],
            "question": vote["question"],
            "bill": vote["bill"],
            "result": vote["result"],
            "date": vote["date"],
            "url": vote["bill_url"] or vote["clerk_url"],
        }


class RepPartyUnitySensor(_VotesBase):
    """Share of recent roll calls where your rep voted with their party's majority."""

    _attr_translation_key = "rep_party_unity"
    _attr_icon = "mdi:account-group"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: FloorVotesCoordinator, entry) -> None:
        super().__init__(coordinator, entry, "votes", "rep_party_unity")

    @property
    def native_value(self) -> float | None:
        return party_unity(self._votes, self._bioguide) if self._bioguide else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        bid = self._bioguide
        counted = [
            v
            for v in self._votes
            if bid and v["members"].get(bid, {}).get("with_party") is not None
        ]
        broke = [_vote_row(v, bid) for v in counted if v["members"][bid]["with_party"] is False]
        return {
            "representative": self.coordinator.data.rep_name if self.coordinator.data else None,
            "votes_counted": len(counted),
            "broke_with_party": broke,
        }


# --------------------------------------------------------------------------
# Campaign finance
# --------------------------------------------------------------------------


class FundraisingSensor(BoilerRoomEntity, SensorEntity):
    """Top fundraiser in a race that covers home; every candidate's money in attributes."""

    coordinator: CampaignFinanceCoordinator
    _attr_icon = "mdi:cash-multiple"

    def __init__(self, coordinator: CampaignFinanceCoordinator, entry, office: str) -> None:
        super().__init__(coordinator, entry, "finance", f"fec_{office}")
        self._office = office
        self._attr_translation_key = f"fec_{office}"

    @property
    def _race(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get(self._office)

    @property
    def available(self) -> bool:
        return super().available and self._race is not None

    @property
    def native_value(self) -> str | None:
        race = self._race
        if not race:
            return None
        cands = race["candidates"]
        return cands[0]["name"] if cands else "No candidates filed"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        race = self._race
        if not race:
            return None
        cands = race["candidates"]
        state = race.get("state")
        if self._office == "house":
            label = (
                f"{(state or '').upper()}-{race.get('district') if race.get('district') else 'AL'}"
            )
        elif self._office == "senate":
            label = f"{US_STATES.get(state or '', (state or '').upper())} Senate"
        else:
            label = "President"
        top = cands[0] if cands else None
        return {
            "race": label,
            "cycle": race["cycle"],
            "candidates": cands,
            "total_raised": round(sum(c["raised"] for c in cands), 2),
            "leader_party": top["party"] if top else None,
            "leader_raised": top["raised"] if top else None,
            "leader_cash_on_hand": top["cash_on_hand"] if top else None,
            "source": "Federal Election Commission",
        }


# --------------------------------------------------------------------------
# Executive actions
# --------------------------------------------------------------------------


class LatestExecutiveActionSensor(BoilerRoomEntity, SensorEntity):
    """Newest presidential document in the Federal Register."""

    coordinator: ExecutiveCoordinator
    _attr_translation_key = "latest_executive_action"
    _attr_icon = "mdi:draw-pen"

    def __init__(self, coordinator: ExecutiveCoordinator, entry) -> None:
        super().__init__(coordinator, entry, "executive", "latest_executive_action")

    @property
    def _docs(self) -> list[dict[str, Any]]:
        return self.coordinator.data.documents if self.coordinator.data else []

    @property
    def native_value(self) -> str | None:
        return truncate(self._docs[0]["title"]) if self._docs else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self._docs:
            return None
        doc = self._docs[0]
        return {
            **{
                k: doc[k]
                for k in ("kind", "number", "signed", "published", "president", "url", "pdf_url")
            },
            "recent": [
                {k: d[k] for k in ("title", "kind", "number", "signed", "published", "url")}
                for d in self._docs
            ],
            "source": "Federal Register",
        }


class ExecutiveOrderCountSensor(BoilerRoomEntity, SensorEntity):
    """Executive orders published so far this calendar year."""

    coordinator: ExecutiveCoordinator
    _attr_translation_key = "executive_orders_this_year"
    _attr_icon = "mdi:file-sign"
    _attr_native_unit_of_measurement = "orders"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: ExecutiveCoordinator, entry) -> None:
        super().__init__(coordinator, entry, "executive", "executive_orders_this_year")

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.executive_orders_this_year if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"year": dt_util.now().year, "source": "Federal Register"}
