import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [matchesPath, sourceDir, outputPath] = process.argv.slice(2);
if (!matchesPath || !sourceDir || !outputPath) {
  console.error('usage: node fresh_sofascore_all_live.mjs MATCHES SOURCE_DIR OUTPUT');
  process.exit(2);
}

const matches = JSON.parse(fs.readFileSync(matchesPath, 'utf8'));
const moduleUrl = pathToFileURL(path.resolve(sourceDir, 'dist', 'index.js')).href;
const { SofascoreRepository } = await import(moduleUrl);

const headers = {
  'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36',
  'Origin': 'https://www.sofascore.com',
  'Referer': 'https://www.sofascore.com/',
};

function norm(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/&/g, ' and ')
    .replace(/\b(fc|cf|sc|afc|fk|sk|club|football|futbol)\b/g, ' ')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
    .replace(/\s+/g, ' ');
}

function levenshteinRatio(a0, b0) {
  const a = norm(a0);
  const b = norm(b0);
  if (a === b) return 1;
  if (!a || !b) return 0;
  const prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  const cur = new Array(b.length + 1).fill(0);
  for (let i = 1; i <= a.length; i++) {
    cur[0] = i;
    for (let j = 1; j <= b.length; j++) {
      cur[j] = Math.min(
        cur[j - 1] + 1,
        prev[j] + 1,
        prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1),
      );
    }
    for (let j = 0; j <= b.length; j++) prev[j] = cur[j];
  }
  return 1 - prev[b.length] / Math.max(a.length, b.length);
}

function tokenScore(a0, b0) {
  const a = new Set(norm(a0).split(' ').filter(Boolean));
  const b = new Set(norm(b0).split(' ').filter(Boolean));
  if (!a.size || !b.size) return 0;
  let inter = 0;
  for (const x of a) if (b.has(x)) inter++;
  return (2 * inter) / (a.size + b.size);
}

function sideScore(a, b) {
  const na = norm(a), nb = norm(b);
  if (na === nb && na) return 1;
  if (na && nb && (na.includes(nb) || nb.includes(na))) return 0.93;
  return 0.72 * levenshteinRatio(na, nb) + 0.28 * tokenScore(na, nb);
}

function bestEvent(match, events) {
  let best = null;
  for (const event of events) {
    const hs = sideScore(match.home, event?.homeTeam?.name);
    const as = sideScore(match.away, event?.awayTeam?.name);
    const score = (hs + as) / 2;
    if (!best || score > best.score) best = { event, score, hs, as };
  }
  if (!best) return null;
  if (best.score < 0.69 || best.hs < 0.52 || best.as < 0.52) return null;
  return best;
}

function choiceHasPrice(choice) {
  if (!choice || typeof choice !== 'object') return false;
  const candidates = [choice.decimalValue, choice.value, choice.odds, choice.price];
  for (const x of candidates) {
    const n = Number(x);
    if (Number.isFinite(n) && n > 1.0 && n < 10000) return true;
  }
  const fv = String(choice.fractionalValue || '');
  return /^\d+(?:\.\d+)?\/\d+(?:\.\d+)?$/.test(fv);
}

function countPrices(markets) {
  let count = 0;
  for (const market of Array.isArray(markets) ? markets : []) {
    for (const choice of Array.isArray(market?.choices) ? market.choices : []) {
      if (choiceHasPrice(choice)) count++;
    }
  }
  return count;
}

async function fetchJson(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const res = await fetch(url, { headers, signal: controller.signal });
    const text = await res.text();
    let data = null;
    try { data = JSON.parse(text); } catch { data = { raw: text.slice(0, 1000) }; }
    return { status: res.status, ok: res.ok, data };
  } finally {
    clearTimeout(timer);
  }
}

let liveEvents = [];
let liveEndpoint = null;
try {
  liveEndpoint = await fetchJson('https://api.sofascore.com/api/v1/sport/football/events/live');
  liveEvents = Array.isArray(liveEndpoint?.data?.events) ? liveEndpoint.data.events : [];
} catch (err) {
  liveEndpoint = { ok: false, status: 0, error: `${err?.name || 'Error'}: ${err?.message || err}` };
}

console.log(`SOFASCORE_LIVE source_ok=${Boolean(liveEndpoint?.ok)} live_events=${liveEvents.length}`);

const rows = [];
for (let i = 0; i < matches.length; i++) {
  const match = matches[i];
  const mapped = bestEvent(match, liveEvents);
  const row = {
    source: 'DevNeonix/sofascore-api',
    match,
    source_ok: Boolean(liveEndpoint?.ok),
    sofa_live_events: liveEvents.length,
  };
  if (!mapped) {
    row.ok = false;
    row.error = 'match_not_found_in_sofascore_live';
    rows.push(row);
    console.log(`SOFA ${i + 1}/${matches.length} ${match.home} - ${match.away} ok=false map=-`);
    continue;
  }

  const ev = mapped.event;
  row.mapping_score = Number(mapped.score.toFixed(4));
  row.sofascore_event = {
    id: ev.id,
    home: ev?.homeTeam?.name || '',
    away: ev?.awayTeam?.name || '',
    status: ev?.status?.type || ev?.status?.description || '',
    home_score: ev?.homeScore?.current ?? null,
    away_score: ev?.awayScore?.current ?? null,
  };

  let projectOdds = { markets: [] };
  try {
    projectOdds = await SofascoreRepository.getOdds(String(ev.id));
  } catch (err) {
    row.project_error = `${err?.name || 'Error'}: ${err?.message || err}`;
  }
  row.project_market_count = Array.isArray(projectOdds?.markets) ? projectOdds.markets.length : 0;
  row.project_price_count = countPrices(projectOdds?.markets || []);

  let raw = null;
  try {
    raw = await fetchJson(`https://api.sofascore.com/api/v1/event/${ev.id}/odds/1/all`);
  } catch (err) {
    raw = { ok: false, status: 0, error: `${err?.name || 'Error'}: ${err?.message || err}` };
  }
  const rawMarkets = Array.isArray(raw?.data?.markets) ? raw.data.markets : [];
  const liveMarkets = rawMarkets.filter((m) => m?.isLive === true);
  const nonLiveMarkets = rawMarkets.filter((m) => m?.isLive === false);
  row.raw_http_status = raw?.status ?? 0;
  row.raw_market_count = rawMarkets.length;
  row.live_market_count = liveMarkets.length;
  row.live_price_count = countPrices(liveMarkets);
  row.pregame_market_count = nonLiveMarkets.length;
  row.pregame_price_count = countPrices(nonLiveMarkets);
  row.ok = row.live_price_count > 0;
  row.error = row.ok ? undefined : (raw?.ok ? 'no_live_prices' : `raw_odds_http_${raw?.status || 0}`);
  row.market_names = liveMarkets.slice(0, 20).map((m) => m?.marketName || m?.name || m?.marketId || m?.id).filter(Boolean);
  rows.push(row);
  console.log(`SOFA ${i + 1}/${matches.length} ${match.home} - ${match.away} ok=${row.ok} map=${row.mapping_score} live_prices=${row.live_price_count} project_prices=${row.project_price_count}`);
}

const anyOk = rows.some((r) => r.ok);
for (const row of rows) row.source_ok = Boolean(liveEndpoint?.ok) && anyOk;

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, JSON.stringify(rows, null, 2));
const matched = rows.filter((r) => r.ok).length;
console.log(`SOFASCORE_ALL_LIVE matched=${matched}/${rows.length} coverage=${rows.length ? Math.round(1000 * matched / rows.length) / 10 : 0}%`);
