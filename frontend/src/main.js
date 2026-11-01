import './styles.css';
import { api, ApiClientError, newKey } from './api.js';
import { h, clear, km, mins, when, pct } from './dom.js';
import { MapView, RISK_COLOR } from './map.js';

const $ = (id) => document.getElementById(id);
const S = { health: null, rr: null, routes: [], selected: null, assessment: null, ai: { status: 'idle', data: null, error: null },
  busy: false, lastBody: null, keys: {}, districtDetail: null, step: '' };

const STATUS_LABEL = { connected: 'Connected', unavailable: 'Unavailable', not_configured: 'Not configured', partially_available: 'Partially available',
  partial: 'Partial', stale: 'Stale' };
const RISK_TEXT = { green: 'No applicable warning', yellow: 'Yellow warning', orange: 'Orange warning', red: 'Red warning', unknown: 'Unknown (not assessed)' };
const COV_TEXT = { complete: 'Complete coverage', partial: 'Partial coverage', unavailable: 'Coverage unavailable' };
const risk = (r) => h('span', { class: `badge b-${r}`, title: 'Unknown is not safe' }, RISK_TEXT[r] ?? r);
const cov = (c) => h('span', { class: `badge b-${c}` }, COV_TEXT[c] ?? c);
const errText = (e) => (e instanceof ApiClientError ? e.message : 'Something went wrong. Please try again.');

let map;

// ---------------------------------------------------------------- header status pills (F1)
function renderPills() {
  const box = clear($('pills'));
  const ints = S.health?.integrations;
  if (!ints) { box.append(h('span', { class: 'pill' }, h('i', { class: 'dot unavailable' }), 'Backend unreachable')); return; }
  const names = { routing: 'Routing', geocoding: 'Geocoding', imd: 'IMD weather', gemini: 'Gemini AI', geography: 'Boundaries' };
  for (const [k, label] of Object.entries(names)) {
    const i = ints[k]; if (!i) continue;
    box.append(h('span', { class: 'pill', title: i.detail || '' }, h('i', { class: `dot ${i.status}` }), `${label}: ${STATUS_LABEL[i.status] ?? i.status}`));
  }
}
async function loadHealth() {
  try { S.health = await api.health(); } catch { S.health = null; }
  renderPills(); renderBanner(); renderAi();
}

function renderBanner() {
  const box = clear($('map-banner'));
  const ints = S.health?.integrations;
  if (!ints) { box.append(h('div', { class: 'banner bad' }, 'The ResQRoute backend is not reachable. Start it with: uvicorn backend.main:app --port 8000')); return; }
  if (ints.imd.status !== 'connected') box.append(h('div', { class: 'banner warn' }, h('b', {}, 'Weather unavailable. '), ints.imd.detail, ' Routes are compared geographically only; risk is shown as Unknown, which is NOT the same as safe.'));
  if (ints.geography.status === 'partially_available') box.append(h('div', { class: 'banner info' }, 'Boundary data covers 22 of 23 Haryana districts (Hansi missing). Road sections outside verified districts are marked unknown.'));
  if (ints.geography.status === 'unavailable') box.append(h('div', { class: 'banner bad' }, 'District boundary data is unavailable; routes cannot be mapped to districts.'));
  if (S.tilesFailed) box.append(h('div', { class: 'banner warn' }, 'Map tiles could not load. All route and risk information is still shown in the panels below.'));
}

// ---------------------------------------------------------------- form (F3)
function localToIso(v) {
  if (!v) return null;
  const d = new Date(v); if (isNaN(d)) return null;
  const off = -d.getTimezoneOffset(), a = Math.abs(off), p = (n) => String(n).padStart(2, '0');
  return `${v.length === 16 ? v + ':00' : v}${off >= 0 ? '+' : '-'}${p(Math.floor(a / 60))}:${p(a % 60)}`;
}
function defaultDeparture() {
  const d = new Date(Date.now() + 30 * 60000); d.setSeconds(0, 0);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
function setErrors(fields = {}) {
  for (const k of ['origin', 'destination', 'departure']) $(`err-${k}`).textContent = '';
  const m = { origin: 'origin', destination: 'destination', departure_time: 'departure' };
  for (const [k, v] of Object.entries(fields)) if (m[k]) $(`err-${m[k]}`).textContent = v;
}
function setStatus(msg, kind = '') {
  const el = clear($('form-status'));
  if (!msg) return;
  el.append(kind === 'busy' ? h('span', { class: 'spin' }) : null, h('span', { class: kind === 'error' ? 'err' : '' }, msg));
}

async function onSubmit(ev) {
  ev.preventDefault();
  if (S.busy) return;
  setErrors();
  const body = { origin: { text: $('origin').value.trim() }, destination: { text: $('destination').value.trim() }, departure_time: localToIso($('departure').value) };
  const local = {};
  if (!body.origin.text) local.origin = 'Please enter an origin.';
  if (!body.destination.text) local.destination = 'Please enter a destination.';
  if (!body.departure_time) local.departure_time = 'Please choose a departure date and time.';
  if (Object.keys(local).length) return setErrors(local);

  const sig = JSON.stringify(body);
  if (sig !== S.lastBody) { S.lastBody = sig; S.keys = { routes: newKey(), assess: newKey() }; }   // same inputs on retry => same keys
  S.busy = true; $('submit').disabled = true;
  S.assessment = null; S.ai = { status: 'idle', data: null, error: null }; renderAll();
  try {
    setStatus('Finding real route alternatives and mapping them to districts…', 'busy');
    const rr = await api.routes(body, S.keys.routes);
    S.rr = rr; S.routes = rr.routes; S.selected = rr.routes[0]?.route_id ?? null;
    if (!rr.routes.length) { setStatus(rr.message || 'No route was found between these locations.', 'error'); renderAll(); return; }
    renderAll();
    await assess();
  } catch (e) {
    if (e instanceof ApiClientError && e.details?.fields) setErrors(e.details.fields);
    setStatus(errText(e), 'error');
  } finally { S.busy = false; $('submit').disabled = false; }
}

async function assess() {
  setStatus('Evaluating weather evidence and risk…', 'busy');
  try {
    S.assessment = await api.assess({ route_ids: S.routes.map((r) => r.route_id), departure_time: S.rr.departure_time }, S.keys.assess);
    setStatus('');
    S.lastBody = null;   // success: the next search gets fresh idempotency keys
    renderAll();
    if (S.health?.integrations?.gemini?.status === 'connected') await runAi();
  } catch (e) {
    setStatus(`${errText(e)} `, 'error');
    $('form-status').append(h('button', { onclick: assess }, 'Retry assessment'));
  }
}

// ---------------------------------------------------------------- routes + details (F9)
function renderRoutes() {
  const box = clear($('routes'));
  if (!S.routes.length) { box.append(h('p', { class: 'muted' }, S.rr?.message || 'No routes yet.')); return; }
  const comp = S.assessment?.comparison;
  for (const r of S.routes) {
    const res = S.assessment?.results.find((x) => x.route_id === r.route_id);
    const tags = [];
    if (comp?.fastest_route_id === r.route_id) tags.push('Fastest');
    if (comp?.shortest_route_id === r.route_id) tags.push('Shortest');
    box.append(h('div', { class: `route ${r.route_id === S.selected ? 'sel' : ''}`, tabindex: 0, onclick: () => select(r.route_id), onkeydown: (e) => { if (e.key === 'Enter') select(r.route_id); } },
      h('div', { class: 'row' }, h('strong', {}, r.label), h('span', {}, res ? [risk(res.risk_level), ' ', cov(res.coverage_status)] : h('span', { class: 'muted small' }, 'assessing…'))),
      h('div', { class: 'row' }, h('span', { class: 'metric' }, h('b', {}, km(r.distance_m)), ' · ', h('b', {}, mins(r.duration_s))),
        h('span', { class: 'metric' }, `${r.districts.length} districts · ${pct(r.coverage_ratio)} verified`)),
      tags.length ? h('div', { class: 'chips' }, tags.map((t) => h('span', { class: 'chip' }, `${t} (geographic)`))) : null,
      r.districts.length ? h('div', { class: 'small muted' }, r.districts.map((d) => d.district_name).join(' → ')) : h('div', { class: 'small muted' }, 'No verified Haryana district on this route.')));
  }
  if (comp) box.append(h('div', { class: 'banner info' }, h('b', {}, comp.basis === 'weather' ? 'Weather-based comparison. ' : 'Geographic comparison only. '), comp.notes.join(' ')));
}
function select(id) { S.selected = id; S.districtDetail = null; renderAll(); }

function renderDetails() {
  const box = clear($('details'));
  const r = S.routes.find((x) => x.route_id === S.selected);
  if (!r) { box.append(h('p', { class: 'muted' }, 'Search for a route to see its assessment.')); return; }
  const a = S.assessment, res = a?.results.find((x) => x.route_id === r.route_id);
  if (!res) { box.append(h('p', { class: 'muted' }, h('span', { class: 'spin' }), 'Waiting for the assessment…')); return; }
  const w = a.weather;
  box.append(h('div', { class: 'row' }, h('strong', {}, `${r.label}: `), risk(res.risk_level), ' ', cov(res.coverage_status)),
    h('p', { class: 'small muted' }, `Weather source: ${w.source_name} — ${STATUS_LABEL[w.status] ?? w.status}${w.retrieved_at ? `, retrieved ${when(w.retrieved_at)}` : ', no data retrieved'}. Travel window: ${when(res.travel_window.start)} → ${when(res.travel_window.end)}.`),
    h('h3', {}, 'Districts crossed (in order)'),
    r.districts.length ? h('table', {}, h('thead', {}, h('tr', {}, ['District', 'On route', 'Weather data', 'Warnings'].map((t) => h('th', {}, t)))),
      h('tbody', {}, r.districts.map((d) => {
        const has = res.weather_coverage.districts_with_data.includes(d.district_id);
        const fs = res.findings.filter((f) => f.district_id === d.district_id);
        return h('tr', {}, h('td', {}, h('button', { class: 'chip', onclick: () => openDistrict(d.district_id, d.district_name) }, d.district_name), d.boundary_uncertain ? h('div', { class: 'small muted' }, 'boundary may predate Hansi') : null),
          h('td', {}, km(d.length_m)), h('td', {}, has ? 'Available' : 'Unavailable'),
          h('td', {}, fs.length ? fs.map((f) => h('div', {}, h('span', { class: `badge b-${f.severity}` }, f.severity), ` ${f.hazard_type.replace(/_/g, ' ')} `, h('button', { class: 'chip', onclick: () => showEvidence(f.evidence_ref) }, f.evidence_ref))) : (has ? 'None found' : 'Not assessed')));
      }))) : h('p', { class: 'muted' }, 'No verified district intersects this route.'),
    r.unknown_length_m > 0 ? h('p', { class: 'small' }, `${km(r.unknown_length_m)} (${pct(1 - r.coverage_ratio)}) of this route lies outside verified Haryana boundaries (shown dotted on the map).`) : null,
    h('h3', {}, 'Data limitations'),
    h('ul', {}, res.limitations.length ? res.limitations.map((t) => h('li', {}, t)) : h('li', { class: 'muted' }, 'None recorded.')),
    h('div', { id: 'evidence-box' }), S.districtDetail ? districtPanel() : null);
}

async function openDistrict(id, name) {
  S.districtDetail = { id, name, loading: true }; renderDetails();
  try { S.districtDetail = { id, name, data: await api.weather(id) }; } catch (e) { S.districtDetail = { id, name, error: errText(e) }; }
  renderDetails();
}
function districtPanel() {
  const d = S.districtDetail;
  const body = d.loading ? h('p', {}, h('span', { class: 'spin' }), 'Loading…') : d.error ? h('p', { class: 'err' }, d.error)
    : [h('p', {}, d.data.message), h('p', { class: 'small muted' }, `Source: ${d.data.source_name} (${STATUS_LABEL[d.data.source_status] ?? d.data.source_status})`),
      d.data.records.length ? h('ul', {}, d.data.records.map((r) => h('li', {}, `${r.hazard_type} ${r.severity} — ${r.display_state}, valid ${when(r.valid_from)} → ${when(r.valid_until)}`))) : null];
  return h('div', {}, h('h3', {}, `District detail: ${d.name}`), body);
}
function showEvidence(ref) {
  const item = S.assessment?.evidence_index?.[ref]; const box = $('evidence-box'); if (!box) return;
  clear(box).append(h('h3', {}, `Evidence ${ref}`), h('pre', {}, JSON.stringify(item ?? { error: 'not found' }, null, 2)));
  box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ---------------------------------------------------------------- AI agent (F10) + trace
async function runAi() {
  if (!S.assessment) return;
  S.ai = { status: 'running', data: null, error: null }; renderAi();
  S.keys.ai = S.keys.ai || newKey();
  try { S.ai = { status: 'done', data: await api.ai(S.assessment.assessment_id, S.keys.ai), error: null }; }
  catch (e) { S.ai = { status: 'error', data: null, error: e }; S.keys.ai = null; }
  renderAi();
  try { S.assessment = { ...S.assessment, trace: (await (await fetch(`/api/route-assessments/${S.assessment.assessment_id}`)).json()).trace }; } catch { /* keep old trace */ }
  renderTrace();
}

function traceView(trace) {
  return h('ol', { class: 'timeline' }, (trace || []).map((t) => {
    if (t.type === 'tool_call') {
      return h('li', { class: `t-tool ${t.status !== 'ok' ? 't-rejected' : ''}` },
        h('div', {}, h('strong', {}, `Step ${t.step}: ${t.tool}`), `(${Object.keys(t.arguments || {}).length ? JSON.stringify(t.arguments) : ''}) → `, h('span', { class: `badge ${t.status === 'ok' ? 'b-complete' : 'b-red'}` }, t.status), ` ${t.duration_ms ?? 0} ms`),
        t.model_message ? h('div', { class: 'small muted' }, `Model said: ${t.model_message}`) : null,
        t.error ? h('div', { class: 'err' }, t.error) : null,
        h('details', {}, h('summary', {}, 'Returned evidence (actual tool output)'), h('pre', {}, JSON.stringify(t.result, null, 2))));
    }
    if (t.type === 'validation_rejected') return h('li', { class: 't-rejected' }, h('strong', {}, `Step ${t.step}: answer rejected by validator`), h('div', { class: 'small' }, t.reason), h('div', { class: 'small muted' }, 'The agent was asked to self-correct once.'));
    return h('li', { class: 't-ai' }, h('strong', {}, `Step ${t.step}: final answer validated`), h('div', { class: 'small muted' }, 'Route IDs and evidence refs were checked against the stored assessment.'));
  }));
}

function renderAi() {
  const box = clear($('ai'));
  const g = S.health?.integrations?.gemini;
  if (!S.assessment) { box.append(h('p', { class: 'muted' }, 'Available after an assessment.')); return; }
  const btn = h('button', { onclick: runAi, disabled: S.ai.status === 'running' || g?.status === 'not_configured' }, S.ai.data ? 'Re-run analysis' : 'Run AI analysis');
  if (g && g.status !== 'connected') box.append(h('div', { class: 'banner warn' }, `AI analysis ${STATUS_LABEL[g.status]?.toLowerCase() ?? g.status}: ${g.detail} The deterministic assessment above is unaffected.`));
  box.append(btn);
  if (S.ai.status === 'running') box.append(h('p', {}, h('span', { class: 'spin' }), 'The agent is calling tools and gathering evidence…'));
  if (S.ai.status === 'error') {
    const e = S.ai.error;
    box.append(h('div', { class: 'banner bad' }, errText(e), ' Your deterministic risk assessment is unchanged.'));
    if (e?.details?.tool_trace?.length) box.append(h('h3', {}, 'Agent steps before failure'), traceView(e.details.tool_trace));
  }
  const d = S.ai.data;
  if (d) {
    const rec = d.recommended_route_id ? S.routes.find((r) => r.route_id === d.recommended_route_id)?.label : null;
    const pref = d.geographic_preference_route_id ? S.routes.find((r) => r.route_id === d.geographic_preference_route_id)?.label : null;
    box.append(h('div', { class: 'ai-note' }, `AI-generated (${d.model}) — validated against backend evidence; not an official observation`),
      h('p', {}, d.summary),
      h('div', { class: 'chips' }, h('span', { class: 'chip' }, `Basis: ${d.analysis_basis.replace('_', ' ')}`), h('span', { class: 'chip' }, `${d.steps_used} tool calls`), d.retries_used ? h('span', { class: 'chip' }, 'self-corrected once') : null),
      h('h3', {}, 'Weather-based recommendation'),
      h('p', {}, rec ? `${rec}. ` : 'No route recommended on weather grounds. ', d.recommendation_reason),
      h('h3', {}, 'Geographic comparison (not a safety judgment)'),
      h('p', {}, pref ? `Geographic preference: ${pref}. ` : '', d.geographic_comparison),
      h('h3', {}, 'Cited evidence'),
      h('div', { class: 'chips' }, d.evidence_refs.map((r) => h('button', { class: 'chip', onclick: () => showEvidence(r), title: S.assessment.evidence_index[r]?.label || '' }, r))),
      h('h3', {}, 'Limitations acknowledged'), h('ul', {}, d.limitations.map((t) => h('li', {}, t))),
      h('h3', {}, 'Agent execution trace (real tool calls)'), traceView(d.tool_trace));
  }
}

function renderTrace() {
  const box = clear($('trace'));
  const a = S.assessment;
  if (!a) { box.append(h('p', { class: 'muted' }, 'Every processing step and its source is listed here.')); return; }
  box.append(h('p', { class: 'small muted' }, `Assessment ${a.assessment_id} · rules ${a.results[0]?.rule_version} · status: ${a.status}`),
    h('ol', { class: 'timeline' }, a.trace.map((t) => h('li', { class: `t-${t.type}` }, h('span', { class: 'small muted' }, `${when(t.ts)} `), h('span', { class: 'chip' }, t.type), ` ${t.message}`,
      t.data?.errors?.length ? h('div', { class: 'small muted' }, t.data.errors.join(' ')) : null))));
}

// ---------------------------------------------------------------- orchestration
function renderAll() {
  renderRoutes(); renderDetails(); renderAi(); renderTrace();
  const r = S.routes.find((x) => x.route_id === S.selected);
  const res = S.assessment?.results.find((x) => x.route_id === S.selected);
  map.setHighlight(r?.district_ids ?? [], Object.fromEntries((res?.findings ?? []).map((f) => [f.district_id, f.severity])));
  map.drawRoutes(S.routes, S.selected, S.assessment);
}

function legend() {
  const item = (c, t, dash) => h('span', {}, h('i', { style: `background:${dash ? 'repeating-linear-gradient(90deg,#475569 0 3px,transparent 3px 8px)' : c}` }), t);
  clear($('legend')).append(item('#2563eb', 'Selected route'), item('#94a3b8', 'Alternative'), item('', 'Outside verified districts (unknown)', true),
    item(RISK_COLOR.yellow, 'Warning (when IMD data exists)'), item('#bfdbfe', 'Districts crossed'));
}

async function init() {
  $('departure').value = defaultDeparture();
  $('route-form').addEventListener('submit', onSubmit);
  map = new MapView($('map'), {
    onDistrict: (id, name) => openDistrict(id, name), onRoute: select,
    onTilesFailed: () => { S.tilesFailed = true; renderBanner(); },
  });
  legend(); renderAll();
  await loadHealth();
  setInterval(loadHealth, 60000);
  try { map.setDistricts(await api.districts()); } catch (e) { $('map-banner').append(h('div', { class: 'banner bad' }, `Boundary layer unavailable: ${errText(e)}`)); }
}
init();
