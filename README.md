# Boiler Room

US politics for Home Assistant. Who represents your house, what's happening to the bills you care about, what the prediction markets think, the day's headlines, and when the next election is — as ordinary sensors, a calendar and automation events.

Boiler Room doesn't editorialize. It reads public data sources and shows you what they say.

## What you get

| Device | Entities | Needs |
|---|---|---|
| **Representatives** | Your two U.S. senators, U.S. representative, state senator and state representative/assembly member — name as the state, photo as the entity picture, party, district, email and links in attributes. With a Congress.gov key, each member of Congress also shows their newest sponsored bill. | Open States key (finds everyone from your home location). Congress.gov key alone works too if you pick your state and district. |
| **Legislation** | One sensor per followed bill. State = latest action; title, sponsor, dates and link in attributes. | Congress.gov key (federal), Open States key (state) |
| **Race Ratings** | Cook Political Report ratings for your House district, Senate race and governor's race (`Lean R`, `Toss Up`, …, with Cook PVI and incumbent), plus House/Senate/governor toss-up counts with the full rating breakdown and the list of toss-up races. | Your own Cook subscription with API access |
| **Prediction Markets** | One sensor per Polymarket market or event. Yes/No markets report the implied probability in %; multi-candidate events report the leader with every outcome's odds in attributes. | Nothing |
| **News** | `Latest headline`, with the most recent headlines across your chosen outlets in attributes. | Nothing |
| **Elections** | `Election day` (on today if there's an election), `Next election` (date + days until), and an `Elections` calendar. Federal general elections are always included; a Google Civic key adds official listings for your state. | Nothing (Google Civic key optional) |

### Automation events

| Event | Fired when | Data |
|---|---|---|
| `boiler_room_bill_action` | A followed bill gets a new latest action | `level`, `bill`, `title`, `action`, `date`, `url` |
| `boiler_room_odds_moved` | An outcome moves at least the threshold (default 5 points) between checks | `market`, `outcome`, `from`, `to`, `change`, `url` |
| `boiler_room_rating_changed` | Cook moves a race's rating | `chamber`, `race`, `state`, `district`, `from`, `to` |
| `boiler_room_new_headline` | A new headline shows up | `title`, `link`, `source`, `published` |

## Install

1. In HACS, open the menu → **Custom repositories**, add `https://github.com/jordanwalk193/ha-boiler-room` as an **Integration**.
2. Download **Boiler Room** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Boiler Room.**
4. Paste whichever keys you have (all optional, all free), then hit **Configure** to choose what to follow.

### Free API keys

| Key | Unlocks | Get one |
|---|---|---|
| Congress.gov | Federal bills, members' sponsored bills | [api.congress.gov/sign-up](https://api.congress.gov/sign-up/) — arrives by email in a minute |
| Open States | Auto-detects every legislator for your home location; state bills | [open.pluralpolicy.com](https://open.pluralpolicy.com/accounts/profile/) |
| Google Civic Information | Official election listings for your state | Enable the Civic Information API in [Google Cloud Console](https://console.cloud.google.com/apis/library/civicinfo.googleapis.com) and create an API key |

### Cook Political Report

Cook offers an official ratings API for private, non-profit and academic use. It needs a Cook subscription plan that includes API access — see [cookpolitical.com/api](https://www.cookpolitical.com/api) and use **Request access** there if your plan doesn't have it yet. Boiler Room signs in with your own Cook email and password, pulls ratings once a day (Cook's request), and never shares them anywhere. It does not scrape cookpolitical.com.

If a Cook sensor looks off, download the integration's diagnostics: the `cook_fields` section lists the field names Cook returned, which makes parser fixes quick.

Keys can be changed later from the integration's **Reconfigure** menu.

## Configure

| Option | Example |
|---|---|
| Federal bills | `HR 1234`, `S.55`, `118/hr/815`, or a congress.gov bill link. Bare numbers mean the current Congress. |
| State bills | `S1234`, `A 5678` (your state), or `ca:AB12` for another state |
| Polymarket | Paste the polymarket.com link for a market or event, or just its slug |
| News outlets | NPR, PBS News Hour, The Hill, Fox News (on by default), plus BBC US and Politico |
| Extra feeds | Any RSS/Atom URL — a local paper, a Substack, or a subreddit like `https://www.reddit.com/r/NeutralPolitics/.rss` |
| Headline keywords | `Syracuse, Hochul, budget` — keep only headlines that mention one of these |

## Dashboard

![Boiler Room dashboard](docs/dashboard.png)

The integration ships its own dashboard. Go to **Settings → Dashboards → Add dashboard → New dashboard from scratch**, open it, choose **Edit → ⋮ → Raw configuration editor**, replace everything with:

```yaml
strategy:
  type: custom:boiler-room
```

and save. You get an **Overview** tab plus **Race ratings**, **Bills**, **Odds**, **News** and **Elections** tabs; tabs for sources you haven't set up are left out. On election day the countdown turns into a polls-open banner.

Each panel is also a card you can drop on any dashboard:

```yaml
type: custom:boiler-room-card
panel: ratings      # election, reps, home_races, ratings, bills, odds, headlines
chamber: house      # ratings only: house, senate or governor
limit: 10           # ratings: toss-up races listed (0 hides the list); headlines: how many
outcomes: 4         # odds: outcomes shown per multi-candidate market
title: Optional heading
```

The cards find Boiler Room's entities on their own, so renaming entity IDs doesn't break them. Colors follow your theme, light or dark.

## More examples

**Phone alert when a bill you follow moves**

```yaml
automation:
  - alias: Bill update
    trigger:
      - platform: event
        event_type: boiler_room_bill_action
    action:
      - service: notify.mobile_app_phone
        data:
          title: "{{ trigger.event.data.bill }}"
          message: "{{ trigger.event.data.action }}"
          data:
            url: "{{ trigger.event.data.url }}"
```

**Porch light reminder on election day**

```yaml
automation:
  - alias: Election day reminder
    trigger:
      - platform: time
        at: "07:00:00"
    condition:
      - condition: state
        entity_id: binary_sensor.boiler_room_elections_election_day
        state: "on"
    action:
      - service: tts.speak
        target:
          entity_id: tts.home_assistant_cloud
        data:
          media_player_entity_id: media_player.kitchen
          message: "Polls are open today."
```

**Headlines card**

```yaml
type: markdown
title: Headlines
content: >
  {% for h in state_attr('sensor.boiler_room_news_latest_headline', 'headlines')[:8] %}
  - [{{ h.title }}]({{ h.link }}) <small>{{ h.source }}</small>
  {% endfor %}
```

**Odds for a multi-candidate event**

```yaml
type: markdown
content: >
  {% set e = 'sensor.boiler_room_prediction_markets_YOUR_EVENT' %}
  **{{ state_attr(e, 'question') }}**
  {% for name, pct in state_attr(e, 'outcomes').items() %}
  - {{ name }}: {{ pct }}%
  {% endfor %}
```

## How often it checks

| Source | Interval |
|---|---|
| Polymarket | 5 minutes |
| News | 10 minutes |
| Federal bills | 30 minutes |
| State bills | 1 hour (Open States' free tier is small; requests are spaced 1 s apart) |
| Representatives | Daily |
| Elections | Every 12 hours |
| Cook ratings | Daily |

## FAQ

**Does it show live election results?**
No. Real-time results come from paid feeds such as the AP's, and there isn't a free, reliable one to build on. For results, check your state or county board of elections.

**Can it read other forecasters (Crystal Ball, Inside Elections, Split Ticket)?**
Not yet. None of them publish a ratings feed or API to build on, and Boiler Room doesn't scrape. If one does, it'll go in.

**Why these news outlets?**
They're established newsrooms with working feeds, picked so the default set isn't all from one side. Turn any of them off, or add your own feeds.

**Are prediction market odds forecasts?**
They're prices traders are paying, read as implied probabilities. They move with money and sentiment, and they're sometimes wrong. Treat them as one signal.

**Where does polling-place info live?**
Not here. Your county board of elections is the authoritative source.

## Development

```bash
pip install pytest-homeassistant-custom-component
pytest -q
```

Data: [Congress.gov API](https://api.congress.gov/) (Library of Congress), [Open States](https://open.pluralpolicy.com/) (Plural), [Polymarket Gamma API](https://docs.polymarket.com/), [Google Civic Information API](https://developers.google.com/civic-information), [Cook Political Report API](https://www.cookpolitical.com/api), and each outlet's public feed. Not affiliated with any of them.

MIT licensed.
