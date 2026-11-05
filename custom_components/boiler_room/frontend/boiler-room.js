/* Boiler Room dashboard card and dashboard strategy.
 *
 * One card type, `custom:boiler-room-card`, draws any of the panels below
 * (`panel: election`, `panel: reps`, `panel: ratings`, ...). The strategy
 * `custom:boiler-room` lays them out as a ready-made dashboard and leaves out
 * tabs for sources that aren't set up.
 *
 * Everything is read from the integration's own entities, found by device and
 * translation key rather than entity id, so renamed entities keep working.
 */
const DOMAIN = 'boiler_room';
const VERSION = '0.2.0';

// Cook's seven-step scale, Solid D to Solid R. Toss Up sits in brass.
const SCALE = ['Solid D', 'Likely D', 'Lean D', 'Toss Up', 'Lean R', 'Likely R', 'Solid R'];
const SCALE_COLOR = {
  'Solid D': '#1f4e8c', 'Likely D': '#4f7fbf', 'Lean D': '#9bb8de',
  'Toss Up': '#b08d3c',
  'Lean R': '#e3a19a', 'Likely R': '#c9564b', 'Solid R': '#8e2a23',
};
const DARK_TEXT = new Set(['Lean D', 'Lean R']);
const CHAMBER = {house: 'House', senate: 'Senate', governor: 'Governors'};
const MAJORITY = {house: 218};

// ------------------------------------------------------------------ helpers
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const safeUrl = u => /^https?:\/\//i.test(String(u || '')) ? esc(u) : '';
const ok = st => st && st.state !== 'unavailable' && st.state !== 'unknown';
const pct = v => (v == null || isNaN(v)) ? '?' : (Math.round(Number(v) * 10) / 10) + '%';

const parseDay = iso => {
  const [y, m, d] = String(iso).split('-').map(Number);
  return new Date(y, m - 1, d);
};
const fmtDay = iso => iso ? parseDay(iso).toLocaleDateString([], {weekday: 'short', month: 'short', day: 'numeric', year: 'numeric'}) : '';
const daysUntil = iso => {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.round((parseDay(iso) - today) / 864e5);
};
const ago = iso => {
  if (!iso) return '';
  const m = Math.floor((Date.now() - new Date(iso)) / 60000);
  if (isNaN(m)) return '';
  if (m < 1) return 'just now';
  if (m < 60) return m + ' min ago';
  if (m < 1440) return Math.floor(m / 60) + 'h ago';
  return Math.floor(m / 1440) + 'd ago';
};
const shortDate = iso => {
  if (!iso) return '';
  const d = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? parseDay(iso) : new Date(iso);
  return isNaN(d) ? esc(iso) : d.toLocaleDateString([], {month: 'short', day: 'numeric', year: 'numeric'});
};

// ------------------------------------------------------- entity discovery
// Each source is its own device; the device model names the group.
const index = hass => {
  const out = {byKey: {}, byGroup: {}};
  for (const e of Object.values(hass.entities || {})) {
    if (e.platform !== DOMAIN) continue;
    const group = (hass.devices?.[e.device_id] || {}).model || '';
    (out.byGroup[group] = out.byGroup[group] || []).push(e.entity_id);
    if (e.translation_key) out.byKey[e.translation_key] = e.entity_id;
  }
  for (const list of Object.values(out.byGroup)) list.sort();
  return out;
};
const REP_KEYS = [
  ['us_senator_1', 'U.S. Senate'], ['us_senator_2', 'U.S. Senate'], ['us_representative', 'U.S. House'],
  ['state_senator', 'State Senate'], ['state_representative', 'State House'],
];

// ------------------------------------------------------------------ styles
const STYLE = `
  :host { display: block; }
  ha-card { padding: 16px; overflow: hidden; }
  .empty { color: var(--secondary-text-color); font-size: 14px; line-height: 1.5; }
  .row { display: flex; gap: 12px; align-items: flex-start; padding: 10px 0; border-top: 1px solid var(--divider-color); cursor: pointer; }
  .row:first-of-type { border-top: 0; padding-top: 0; }
  .row:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; border-radius: 4px; }
  .grow { flex: 1; min-width: 0; }
  .name { font-size: 15px; font-weight: 500; color: var(--primary-text-color); line-height: 1.3; }
  .sub { font-size: 13px; color: var(--secondary-text-color); line-height: 1.4; margin-top: 2px; }
  .clip { overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
  a { color: var(--primary-color); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .chip { display: inline-block; font-size: 12px; font-weight: 600; line-height: 1; padding: 5px 8px; border-radius: 999px; white-space: nowrap; color: #fff; font-variant-numeric: tabular-nums; }
  .chip.dark { color: #1b1b1b; }
  .chip.quiet { background: var(--secondary-background-color, rgba(127,127,127,.15)); color: var(--secondary-text-color); font-weight: 500; }

  /* election */
  .ballot { display: grid; grid-template-columns: auto 1fr; gap: 4px 16px; align-items: center; }
  .stub { grid-row: span 2; width: 64px; height: 72px; border-radius: 6px; border: 2px solid var(--br-brass); display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--br-brass); font-variant-numeric: tabular-nums; }
  .stub b { font-size: 26px; line-height: 1; font-weight: 700; }
  .stub span { font-size: 11px; margin-top: 4px; }
  .live .stub { background: var(--br-brass); color: #fff; }
  .headline { font-size: 20px; font-weight: 600; color: var(--primary-text-color); line-height: 1.25; }
  .upcoming { margin-top: 14px; display: grid; gap: 6px; }
  .upcoming div { display: flex; justify-content: space-between; gap: 12px; font-size: 13px; color: var(--secondary-text-color); }
  .upcoming div span:last-child { white-space: nowrap; font-variant-numeric: tabular-nums; }

  /* reps */
  .face { width: 44px; height: 44px; border-radius: 50%; object-fit: cover; object-position: top; background: var(--secondary-background-color, rgba(127,127,127,.15)); flex: none; display: flex; align-items: center; justify-content: center; color: var(--secondary-text-color); font-weight: 600; }

  /* ratings: the signature strip */
  .strip { position: relative; display: flex; height: 34px; border-radius: 4px; overflow: hidden; margin: 6px 0 8px; }
  .strip div { display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 600; color: #fff; min-width: 0; font-variant-numeric: tabular-nums; }
  .strip div.dark { color: #1b1b1b; }
  .strip div.toss { box-shadow: inset 0 0 0 2px rgba(255,255,255,.55); }
  .tick { position: absolute; top: -4px; bottom: -4px; width: 2px; background: var(--primary-text-color); }
  .tickwrap { position: relative; }
  .ticklabel { position: absolute; top: -18px; transform: translateX(-50%); font-size: 11px; color: var(--secondary-text-color); white-space: nowrap; }
  .tally { display: flex; justify-content: space-between; font-size: 13px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
  .tally b { color: var(--primary-text-color); font-weight: 600; }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 12px; margin: 10px 0 4px; font-size: 12px; color: var(--secondary-text-color); }
  .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
  .tosslist { margin-top: 12px; display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 6px; }
  .tosslist div { border-left: 3px solid var(--br-brass); padding: 4px 8px; font-size: 13px; line-height: 1.35; }
  .tosslist small { display: block; color: var(--secondary-text-color); }
  h3 { font-size: 14px; font-weight: 600; margin: 0 0 4px; color: var(--primary-text-color); }

  /* odds */
  .market { padding: 12px 0; border-top: 1px solid var(--divider-color); cursor: pointer; }
  .market:first-of-type { border-top: 0; padding-top: 0; }
  .bar { display: grid; grid-template-columns: minmax(70px, 34%) 1fr 48px; gap: 8px; align-items: center; font-size: 13px; margin-top: 6px; }
  .bar .label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--primary-text-color); }
  .bar .track { height: 10px; border-radius: 5px; background: var(--secondary-background-color, rgba(127,127,127,.15)); overflow: hidden; }
  .bar .fill { height: 100%; border-radius: 5px; background: var(--secondary-text-color); opacity: .45; }
  .bar .fill.lead { opacity: 1; }
  .bar .fill.lead { background: var(--br-brass); }
  .bar .num { text-align: right; font-variant-numeric: tabular-nums; color: var(--primary-text-color); }
  .note { font-size: 12px; color: var(--secondary-text-color); margin-top: 6px; }

  /* headlines */
  .news { padding: 9px 0; border-top: 1px solid var(--divider-color); }
  .news:first-of-type { border-top: 0; padding-top: 0; }
  .news a { color: var(--primary-text-color); font-size: 15px; line-height: 1.35; }
`;

// ---------------------------------------------------------------- panels
const PANELS = {
  election(hass, idx) {
    const next = hass.states[idx.byKey.next_election];
    const today = hass.states[idx.byKey.election_day];
    if (!ok(next)) return `<div class="empty">No upcoming elections are listed yet.</div>`;
    const a = next.attributes;
    const live = today && today.state === 'on';
    const days = daysUntil(next.state);
    const stub = live
      ? `<div class="stub"><b>✓</b><span>today</span></div>`
      : `<div class="stub"><b>${days}</b><span>${days === 1 ? 'day' : 'days'}</span></div>`;
    const title = live
      ? `${esc((today.attributes.elections || [])[0] || a.name)} is today`
      : esc(a.name);
    const sub = live ? 'Polls are open. Check your county board of elections for hours and your polling place.'
      : fmtDay(next.state);
    const rest = (a.upcoming || []).filter(e => e.date !== next.state).slice(0, 3);
    return `<div class="ballot ${live ? 'live' : ''}" data-entity="${esc(next.entity_id)}" role="button" tabindex="0">
        ${stub}<div class="headline">${title}</div><div class="sub">${esc(sub)}</div>
      </div>
      ${rest.length ? `<div class="upcoming">${rest.map(e => `<div><span>${esc(e.name)}</span><span>${fmtDay(e.date)}</span></div>`).join('')}</div>` : ''}`;
  },

  reps(hass, idx) {
    const rows = REP_KEYS.map(([key, office]) => [hass.states[idx.byKey[key]], office]).filter(([st]) => ok(st));
    if (!rows.length) return `<div class="empty">Add an Open States key (or a Congress.gov key with your state) to see who represents you.</div>`;
    return rows.map(([st, office]) => {
      const a = st.attributes;
      const pic = a.entity_picture
        ? `<img class="face" src="${safeUrl(a.entity_picture)}" alt="" loading="lazy">`
        : `<div class="face">${esc(st.state.split(' ').map(w => w[0]).slice(0, 2).join(''))}</div>`;
      const district = a.district && !/senate/i.test(office) ? `, district ${esc(String(a.district).replace(/^[A-Z]{2}-/, ''))}` : '';
      const latest = a.latest_sponsored_bill
        ? `<div class="sub clip">Newest bill: ${esc(a.latest_sponsored_bill)}${a.latest_sponsored_title ? ', ' + esc(a.latest_sponsored_title) : ''}</div>` : '';
      return `<div class="row" data-entity="${esc(st.entity_id)}" role="button" tabindex="0">${pic}
        <div class="grow"><div class="name">${esc(st.state)}</div>
        <div class="sub">${esc(office)}${district}${a.party ? ', ' + esc(a.party) : ''}</div>${latest}</div></div>`;
    }).join('');
  },

  ratings(hass, idx, config) {
    const chamber = config.chamber || 'house';
    const st = hass.states[idx.byKey[`cook_${chamber}_toss_ups`]];
    if (!ok(st)) return `<div class="empty">Sign in with a Cook Political Report account that includes API access to see race ratings.</div>`;
    const counts = st.attributes.ratings || {};
    const total = SCALE.reduce((n, r) => n + (counts[r] || 0), 0);
    if (!total) return `<div class="empty">Cook hasn't published ${esc(CHAMBER[chamber])} ratings yet.</div>`;
    const dem = ['Solid D', 'Likely D', 'Lean D'].reduce((n, r) => n + (counts[r] || 0), 0);
    const rep = ['Lean R', 'Likely R', 'Solid R'].reduce((n, r) => n + (counts[r] || 0), 0);
    const segs = SCALE.filter(r => counts[r]).map(r => {
      const w = counts[r] / total * 100;
      return `<div class="${DARK_TEXT.has(r) ? 'dark' : ''} ${r === 'Toss Up' ? 'toss' : ''}" style="width:${w}%;background:${SCALE_COLOR[r]}" title="${esc(r)}: ${counts[r]}">${w > 4 ? counts[r] : ''}</div>`;
    }).join('');
    const majority = MAJORITY[chamber] && total === 435
      ? `<div class="tick" style="left:calc(${MAJORITY[chamber] / total * 100}% - 1px)"></div><div class="ticklabel" style="left:${MAJORITY[chamber] / total * 100}%">${MAJORITY[chamber]} to control</div>`
      : '';
    const tosses = st.attributes.toss_up_races || [];
    return `<div class="tickwrap" style="${majority ? 'margin-top:18px' : ''}"><div class="strip">${segs}</div>${majority}</div>
      <div class="tally"><span><b>${dem}</b> leaning D</span><span><b>${counts['Toss Up'] || 0}</b> toss up</span><span><b>${rep}</b> leaning R</span></div>
      <div class="legend">${SCALE.map(r => `<span><i style="background:${SCALE_COLOR[r]}"></i>${esc(r)}</span>`).join('')}</div>
      ${tosses.length && (config.limit ?? 40) > 0 ? `<div class="tosslist">${tosses.slice(0, config.limit ?? 40).map(t =>
        `<div>${esc(t.race)}<small>${esc([t.incumbent, t.pvi].filter(Boolean).join(', '))}</small></div>`).join('')}</div>` : ''}
      <div class="note">Cook Political Report ratings, refreshed daily.</div>`;
  },

  home_races(hass, idx) {
    const rows = [['cook_home_house', 'Your House district'], ['cook_home_senate', 'Your Senate race'], ['cook_home_governor', "Your governor's race"]]
      .map(([k, label]) => [hass.states[idx.byKey[k]], label]).filter(([st]) => ok(st));
    if (!rows.length) return `<div class="empty">Sign in with a Cook Political Report account that includes API access to see your races.</div>`;
    return rows.map(([st, label]) => {
      const a = st.attributes;
      const rated = SCALE_COLOR[st.state];
      const chip = rated
        ? `<span class="chip ${DARK_TEXT.has(st.state) ? 'dark' : ''}" style="background:${rated}">${esc(st.state)}</span>`
        : `<span class="chip quiet">${esc(st.state)}</span>`;
      const detail = a.race ? [a.race, a.incumbent && `incumbent ${a.incumbent}`, a.pvi && `PVI ${a.pvi}`].filter(Boolean).join(', ') : 'Not on the ballot this cycle';
      return `<div class="row" data-entity="${esc(st.entity_id)}" role="button" tabindex="0">
        <div class="grow"><div class="name">${esc(label)}</div><div class="sub">${esc(detail)}</div></div>${chip}</div>`;
    }).join('');
  },

  bills(hass, idx) {
    const ids = idx.byGroup['Legislation'] || [];
    const rows = ids.map(id => hass.states[id]).filter(Boolean);
    if (!rows.length) return `<div class="empty">Follow bills from the integration's Configure screen.</div>`;
    rows.sort((x, y) => String(y.attributes.latest_action_date || '').localeCompare(String(x.attributes.latest_action_date || '')));
    return rows.map(st => {
      const a = st.attributes;
      if (!ok(st)) return `<div class="row" data-entity="${esc(st.entity_id)}" role="button" tabindex="0"><div class="grow"><div class="name">${esc(a.friendly_name || st.entity_id)}</div><div class="sub">Couldn't find this bill. Check the number in Configure.</div></div></div>`;
      const level = a.level === 'state' ? 'State' : 'Federal';
      return `<div class="row" data-entity="${esc(st.entity_id)}" role="button" tabindex="0">
        <div class="grow"><div class="name">${esc(a.label)} <span class="sub">${esc(level)}</span></div>
        <div class="sub clip">${esc(a.title)}</div>
        <div class="sub"><b style="color:var(--primary-text-color);font-weight:500">${esc(shortDate(a.latest_action_date))}</b> ${esc(st.state)}</div>
        ${safeUrl(a.url) ? `<div class="sub"><a href="${safeUrl(a.url)}" target="_blank" rel="noopener">Read the bill</a></div>` : ''}</div></div>`;
    }).join('');
  },

  odds(hass, idx, config) {
    const ids = idx.byGroup['Prediction Markets'] || [];
    const rows = ids.map(id => hass.states[id]).filter(ok);
    if (!rows.length) return `<div class="empty">Paste a Polymarket link into the integration's Configure screen to track its odds.</div>`;
    const show = config.outcomes || 4;
    return rows.map(st => {
      const a = st.attributes;
      const all = Object.entries(a.outcomes || {});
      // Keep Yes above No for binary markets; rank candidates otherwise.
      const binary = all.length === 2 && all.some(([n]) => n === 'Yes') && all.some(([n]) => n === 'No');
      const entries = binary ? all.sort(([n]) => (n === 'Yes' ? -1 : 1)) : all.sort((x, y) => y[1] - x[1]).slice(0, show);
      const top = Math.max(0, ...entries.map(([, v]) => v));
      return `<div class="market" data-entity="${esc(st.entity_id)}" role="button" tabindex="0">
        <div class="name">${esc(a.question || a.friendly_name)}</div>
        ${entries.map(([name, v]) => `<div class="bar"><span class="label">${esc(name)}</span>
          <span class="track"><span class="fill ${v === top ? 'lead' : ''}" style="display:block;width:${Math.max(0, Math.min(100, v))}%"></span></span>
          <span class="num">${pct(v)}</span></div>`).join('')}
        <div class="note">${a.closed ? 'Market closed. ' : ''}${a.end_date ? 'Resolves ' + shortDate(a.end_date) + '. ' : ''}${safeUrl(a.url) ? `<a href="${safeUrl(a.url)}" target="_blank" rel="noopener">Polymarket</a>` : ''}</div>
      </div>`;
    }).join('');
  },

  headlines(hass, idx, config) {
    const st = hass.states[idx.byKey.latest_headline];
    if (!st) return `<div class="empty">Pick news outlets in the integration's Configure screen.</div>`;
    const list = (st.attributes.headlines || []).slice(0, config.limit || 10);
    const failed = st.attributes.failed_sources || [];
    if (!list.length) return `<div class="empty">No headlines yet${failed.length ? '. These feeds didn\'t load: ' + esc(failed.join(', ')) : ''}.</div>`;
    return list.map(h => `<div class="news">${safeUrl(h.link) ? `<a href="${safeUrl(h.link)}" target="_blank" rel="noopener">${esc(h.title)}</a>` : esc(h.title)}
        <div class="sub">${esc(h.source)}${h.published ? ', ' + esc(ago(h.published)) : ''}</div></div>`).join('')
      + (failed.length ? `<div class="note">Didn't load: ${esc(failed.join(', '))}</div>` : '');
  },
};

// ------------------------------------------------------------------- card
class BoilerRoomCard extends HTMLElement {
  setConfig(config) {
    if (!config || !PANELS[config.panel]) {
      throw new Error(`Set panel to one of: ${Object.keys(PANELS).join(', ')}`);
    }
    this._config = config;
    this._sig = null;
    if (this._hass) this.draw();
  }

  set hass(hass) {
    this._hass = hass;
    // Redraw only when one of our entities changed.
    const idx = index(hass);
    const ids = Object.values(idx.byGroup).flat();
    const sig = ids.map(id => hass.states[id]).filter(Boolean).map(s => s.last_updated + s.state).join('|') + '#' + ids.length;
    if (sig === this._sig) return;
    this._sig = sig;
    this._idx = idx;
    this.draw();
  }

  draw() {
    if (!this._config || !this._hass) return;
    if (!this.shadowRoot) {
      this.attachShadow({mode: 'open'});
      this.shadowRoot.addEventListener('click', e => this._open(e));
      this.shadowRoot.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') this._open(e); });
    }
    const idx = this._idx || index(this._hass);
    let body;
    try {
      body = PANELS[this._config.panel](this._hass, idx, this._config);
    } catch (err) {
      body = `<div class="empty">This panel couldn't be drawn: ${esc(err.message)}</div>`;
    }
    const title = this._config.title ? `<h3>${esc(this._config.title)}</h3>` : '';
    this.shadowRoot.innerHTML = `<style>:host{--br-brass:#a8842c}${STYLE}</style><ha-card>${title}${body}</ha-card>`;
  }

  _open(e) {
    if (e.target.closest('a')) return;
    const el = e.target.closest('[data-entity]');
    if (!el) return;
    if (e.type === 'keydown') e.preventDefault();
    this.dispatchEvent(new CustomEvent('hass-more-info', {detail: {entityId: el.dataset.entity}, bubbles: true, composed: true}));
  }

  getCardSize() { return {election: 3, reps: 5, ratings: 4, home_races: 3, bills: 6, odds: 6, headlines: 8}[this._config?.panel] || 4; }
  getGridOptions() { return {columns: 'full', min_columns: 6}; }
  static getStubConfig() { return {panel: 'election'}; }
}

// --------------------------------------------------------------- strategy
const card = (panel, extra = {}) => ({type: 'custom:boiler-room-card', panel, grid_options: {columns: 'full'}, ...extra});
const heading = (text, icon) => ({type: 'heading', heading: text, icon});
const section = (...cards) => ({type: 'grid', cards: cards.filter(Boolean)});

class BoilerRoomStrategy {
  static async generate(config, hass) {
    const idx = index(hass);
    const has = key => !!idx.byKey[key];
    const group = name => (idx.byGroup[name] || []).length > 0;
    const cook = has('cook_house_toss_ups');
    const numericMarkets = (idx.byGroup['Prediction Markets'] || [])
      .filter(id => hass.states[id]?.attributes?.unit_of_measurement === '%');

    const views = [];
    views.push({
      title: 'Overview', path: 'overview', icon: 'mdi:bank', max_columns: 3, sections: [
        section(heading('Next election', 'mdi:ballot-outline'), card('election'),
          cook ? heading('Your races', 'mdi:flag-checkered') : null, cook ? card('home_races') : null),
        section(heading('Who represents you', 'mdi:account-tie'), card('reps')),
        group('News') ? section(heading('Headlines', 'mdi:newspaper-variant-outline'), card('headlines', {limit: 6})) : null,
        cook ? {...section(heading('House ratings', 'mdi:scale-balance'), card('ratings', {chamber: 'house', limit: 0})), column_span: 2} : null,
        group('Prediction Markets') ? section(heading('Odds', 'mdi:chart-line-variant'), card('odds', {outcomes: 3})) : null,
      ].filter(Boolean),
    });
    if (cook) {
      views.push({
        title: 'Race ratings', path: 'ratings', icon: 'mdi:scale-balance', max_columns: 3, sections: [
          {...section(heading('House', 'mdi:home-group'), card('ratings', {chamber: 'house'})), column_span: 3},
          section(heading('Senate', 'mdi:pillar'), card('ratings', {chamber: 'senate'})),
          section(heading('Governors', 'mdi:flag-variant'), card('ratings', {chamber: 'governor'})),
          section(heading('Your races', 'mdi:flag-checkered'), card('home_races')),
        ],
      });
    }
    if (group('Legislation')) {
      views.push({
        title: 'Bills', path: 'bills', icon: 'mdi:file-document-outline', max_columns: 2, sections: [
          {...section(heading('Bills you follow', 'mdi:file-document-outline'), card('bills')), column_span: 2},
        ],
      });
    }
    if (group('Prediction Markets')) {
      views.push({
        title: 'Odds', path: 'odds', icon: 'mdi:chart-line-variant', max_columns: 2, sections: [
          section(heading('Prediction markets', 'mdi:chart-line-variant'), card('odds', {outcomes: 6})),
          numericMarkets.length ? section(heading('Last 7 days', 'mdi:chart-timeline-variant'),
            {type: 'history-graph', hours_to_show: 168, entities: numericMarkets, grid_options: {columns: 'full'}}) : null,
        ].filter(Boolean),
      });
    }
    if (group('News')) {
      views.push({
        title: 'News', path: 'news', icon: 'mdi:newspaper-variant-outline', max_columns: 2, sections: [
          {...section(heading('Headlines', 'mdi:newspaper-variant-outline'), card('headlines', {limit: 25})), column_span: 2},
        ],
      });
    }
    views.push({
      title: 'Elections', path: 'elections', icon: 'mdi:calendar-star', max_columns: 2, sections: [
        section(heading('Next election', 'mdi:ballot-outline'), card('election')),
        has('elections') ? section(heading('Calendar', 'mdi:calendar-star'),
          {type: 'calendar', entities: [idx.byKey.elections], initial_view: 'listWeek', grid_options: {columns: 'full'}}) : null,
      ].filter(Boolean),
    });
    return {title: config.title || 'Boiler Room', views: views.map(v => ({type: 'sections', ...v}))};
  }
}

class BoilerRoomStrategyElement extends HTMLElement {
  static async generate(config, hass) { return BoilerRoomStrategy.generate(config, hass); }
}

if (!customElements.get('boiler-room-card')) customElements.define('boiler-room-card', BoilerRoomCard);
if (!customElements.get('ll-strategy-dashboard-boiler-room')) customElements.define('ll-strategy-dashboard-boiler-room', BoilerRoomStrategyElement);

window.customCards = window.customCards || [];
if (!window.customCards.some(c => c.type === 'boiler-room-card')) {
  window.customCards.push({
    type: 'boiler-room-card',
    name: 'Boiler Room',
    description: 'Elections, representatives, race ratings, bills, odds and headlines from Boiler Room.',
    preview: false,
    documentationURL: 'https://github.com/jordanwalk193/ha-boiler-room',
  });
}
console.info(`%c BOILER-ROOM %c ${VERSION} `, 'background:#a8842c;color:#fff;font-weight:600', '');
