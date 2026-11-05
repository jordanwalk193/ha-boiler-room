"""Config and options flows for Boiler Room."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import (
    AuthError,
    BoilerRoomError,
    CivicClient,
    CongressClient,
    CookClient,
    FecClient,
    OpenStatesClient,
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
    CONF_STATE,
    CONF_STATE_BILLS,
    DEFAULT_HEADLINE_COUNT,
    DEFAULT_NEWS_SOURCES,
    DEFAULT_ODDS_THRESHOLD,
    DOMAIN,
    NEWS_SOURCES,
    STATE_AUTO,
    US_STATES,
)
from .parsers import (
    current_congress,
    parse_federal_bill,
    parse_kalshi_ref,
    parse_market_ref,
    parse_state_bill,
    split_list,
)

_SECRET = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_MULTILINE = TextSelector(TextSelectorConfig(multiline=True))

KEYS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_CONGRESS_KEY): _SECRET,
        vol.Optional(CONF_OPENSTATES_KEY): _SECRET,
        vol.Optional(CONF_CIVIC_KEY): _SECRET,
        vol.Optional(CONF_FEC_KEY): _SECRET,
        vol.Optional(CONF_COOK_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Optional(CONF_COOK_PASSWORD): _SECRET,
    }
)

DEFAULT_OPTIONS: dict[str, Any] = {
    CONF_STATE: STATE_AUTO,
    CONF_NEWS_SOURCES: DEFAULT_NEWS_SOURCES,
    CONF_HEADLINE_COUNT: DEFAULT_HEADLINE_COUNT,
    CONF_ODDS_THRESHOLD: DEFAULT_ODDS_THRESHOLD,
    CONF_FLOOR_VOTES: True,
    CONF_CAMPAIGN_FINANCE: True,
    CONF_EXECUTIVE: True,
    CONF_CRYSTAL_BALL: False,
}

LINKS = {
    "congress_url": "https://api.congress.gov/sign-up/",
    "openstates_url": "https://open.pluralpolicy.com/accounts/profile/",
    "civic_url": "https://console.cloud.google.com/apis/library/civicinfo.googleapis.com",
    "cook_url": "https://www.cookpolitical.com/api",
    "fec_url": "https://api.data.gov/signup/",
}


async def _validate_keys(hass: HomeAssistant, data: Mapping[str, Any]) -> dict[str, str]:
    """Try each key that was entered. Returns field -> error code."""
    session = async_get_clientsession(hass)
    errors: dict[str, str] = {}
    checks = (
        (CONF_CONGRESS_KEY, lambda k: CongressClient(session, k).validate()),
        (CONF_OPENSTATES_KEY, lambda k: OpenStatesClient(session, k).validate()),
        (CONF_CIVIC_KEY, lambda k: CivicClient(session, k).validate()),
        (CONF_FEC_KEY, lambda k: FecClient(session, k).validate()),
    )
    for field, check in checks:
        if not (key := (data.get(field) or "").strip()):
            continue
        try:
            await check(key)
        except AuthError:
            errors[field] = "invalid_key"
        except BoilerRoomError:
            errors[field] = "cannot_connect"

    email = (data.get(CONF_COOK_EMAIL) or "").strip()
    password = data.get(CONF_COOK_PASSWORD) or ""
    if email or password:
        if not (email and password):
            errors[CONF_COOK_PASSWORD if email else CONF_COOK_EMAIL] = "cook_incomplete"
        else:
            try:
                await CookClient(session, email, password).validate()
            except AuthError:
                errors[CONF_COOK_PASSWORD] = "cook_denied"
            except BoilerRoomError:
                errors[CONF_COOK_PASSWORD] = "cannot_connect"
    return errors


def _clean_keys(data: Mapping[str, Any]) -> dict[str, str]:
    """Trim keys and emails; passwords are kept exactly as typed."""
    return {
        k: (v if k == CONF_COOK_PASSWORD else v.strip())
        for k, v in data.items()
        if isinstance(v, str) and v.strip()
    }


class BoilerRoomConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Boiler Room. Every key is optional; each one unlocks more entities."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _validate_keys(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    title="Boiler Room",
                    data=_clean_keys(user_input),
                    options=DEFAULT_OPTIONS,
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(KEYS_SCHEMA, user_input or {}),
            errors=errors,
            description_placeholders=LINKS,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._keys_step("reconfigure", user_input)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._keys_step("reauth_confirm", user_input)

    async def _keys_step(self, step_id: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        entry = (
            self._get_reconfigure_entry() if step_id == "reconfigure" else self._get_reauth_entry()
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _validate_keys(self.hass, user_input)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=_clean_keys(user_input))
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                KEYS_SCHEMA, user_input or dict(entry.data)
            ),
            errors=errors,
            description_placeholders=LINKS,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> BoilerRoomOptionsFlow:
        return BoilerRoomOptionsFlow()


def _options_schema() -> vol.Schema:
    states = [SelectOptionDict(value=STATE_AUTO, label="Detect from home location")] + [
        SelectOptionDict(value=code, label=name)
        for code, name in sorted(US_STATES.items(), key=lambda kv: kv[1])
    ]
    sources = [SelectOptionDict(value=key, label=label) for key, (label, _) in NEWS_SOURCES.items()]
    return vol.Schema(
        {
            vol.Optional(CONF_STATE, default=STATE_AUTO): SelectSelector(
                SelectSelectorConfig(options=states, mode=SelectSelectorMode.DROPDOWN)
            ),
            vol.Optional(CONF_DISTRICT): NumberSelector(
                NumberSelectorConfig(min=0, max=60, mode=NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_FEDERAL_BILLS): _MULTILINE,
            vol.Optional(CONF_STATE_BILLS): _MULTILINE,
            vol.Optional(CONF_MARKETS): _MULTILINE,
            vol.Optional(CONF_KALSHI): _MULTILINE,
            vol.Optional(CONF_ODDS_THRESHOLD, default=DEFAULT_ODDS_THRESHOLD): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=50,
                    step=0.5,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="pts",
                )
            ),
            vol.Optional(CONF_NEWS_SOURCES, default=DEFAULT_NEWS_SOURCES): SelectSelector(
                SelectSelectorConfig(options=sources, multiple=True, mode=SelectSelectorMode.LIST)
            ),
            vol.Optional(CONF_CUSTOM_FEEDS): _MULTILINE,
            vol.Optional(CONF_NEWS_KEYWORDS): TextSelector(),
            vol.Optional(CONF_HEADLINE_COUNT, default=DEFAULT_HEADLINE_COUNT): NumberSelector(
                NumberSelectorConfig(min=3, max=25, mode=NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_FLOOR_VOTES, default=True): BooleanSelector(),
            vol.Optional(CONF_CAMPAIGN_FINANCE, default=True): BooleanSelector(),
            vol.Optional(CONF_EXECUTIVE, default=True): BooleanSelector(),
            vol.Optional(CONF_CRYSTAL_BALL, default=False): BooleanSelector(),
        }
    )


def validate_options(options: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Check list-style options. Returns (field errors, unrecognized entries)."""
    errors: dict[str, str] = {}
    bad: list[str] = []
    congress = current_congress(dt_util.now().date())
    for field, parser in (
        (CONF_FEDERAL_BILLS, lambda t: parse_federal_bill(t, congress)),
        (CONF_STATE_BILLS, parse_state_bill),
        (CONF_MARKETS, parse_market_ref),
        (CONF_KALSHI, parse_kalshi_ref),
    ):
        wrong = [t for t in split_list(options.get(field)) if parser(t) is None]
        if wrong:
            errors[field] = "invalid_entries"
            bad += wrong
    for url in split_list(options.get(CONF_CUSTOM_FEEDS)):
        if not url.startswith(("http://", "https://")):
            errors[CONF_CUSTOM_FEEDS] = "invalid_entries"
            bad.append(url)
    return errors, bad


class BoilerRoomOptionsFlow(OptionsFlow):
    """What to follow."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        bad: list[str] = []
        if user_input is not None:
            errors, bad = validate_options(user_input)
            if not errors:
                if CONF_DISTRICT in user_input:
                    user_input[CONF_DISTRICT] = int(user_input[CONF_DISTRICT])
                return self.async_create_entry(data=user_input)
        current = user_input or dict(self.config_entry.options)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(_options_schema(), current),
            errors=errors,
            description_placeholders={"invalid": ", ".join(bad) or "-"},
        )
