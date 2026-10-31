import { Scope, echoColor, BANDS } from './radar.js?v=__BUILD__';
import { makeBrief, makePrompt } from './brief.js?v=__BUILD__';
import { UPDATES } from './updates.js?v=__BUILD__';

const LS = 'echo.v1';
const LS_KEY = 'echo.key';
const LS_SEEN = 'echo.seenUpdate';
const BUILD = '__BUILD__';
const PILLAR_ORDER = ['season', 'issue', 'ranking', 'hardware', 'spotlight'];
const KEYS = ['demand', 'timing', 'fit', 'ease'];
const KEY_LABEL = { demand: '需求', timing: '時效', fit: '契合', ease: '好做' };
const KEY_HINT = {
  demand: '會不會有人看：各測站的回波，台灣與全球加權',
  timing: '現在拍是不是剛好：離截止日多遠、話題還新不新',
  fit: '適不適合這個頻道：路線、平台、繁中、做過沒有',
  ease: '好不好做：官方來源、素材、是不是只有傳聞',
};
const STATUS = { idea: '想拍', making: '製作中', live: '已上片', done: '已做過' };

const S = {
  radar: null, cal: null, meta: null, view: 'echo', filter: 'all', q: '', sort: 'score',
  prefs: readPrefs(), list: [], byId: {}, top: [], scope: null, open: null, scanning: false,
};

// ------------------------------------------------------------------ 小工具
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
// 外部來源的連結只接受 http(s)，其他（javascript: 之類）一律不當成連結
const safeUrl = (u) => (/^https?:\/\//i.test(String(u || '')) ? String(u) : '');
const link = (u, html) => (safeUrl(u) ? `<a href="${esc(safeUrl(u))}" target="_blank" rel="noopener">${html}</a>` : html);
const clamp = (x, lo = 0, hi = 100) => Math.max(lo, Math.min(hi, x));
const pad = (n) => String(n).padStart(2, '0');
const WD = '日一二三四五六';
function dayOf(iso) { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); }
function md(iso) { if (!iso) return ''; const d = dayOf(iso); return `${d.getMonth() + 1}/${d.getDate()}`; }
function mdw(iso) { if (!iso) return ''; const d = dayOf(iso); return `${d.getMonth() + 1}/${d.getDate()}（週${WD[d.getDay()]}）`; }
function hm(iso) { const t = new Date(iso); return `${pad(t.getHours())}:${pad(t.getMinutes())}`; }
function todayISO() { const t = new Date(); return `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())}`; }
function daysBetween(a, b) { return Math.round((dayOf(b) - dayOf(a)) / 86400000); }
function addDays(iso, n) { const d = dayOf(iso); d.setDate(d.getDate() + n); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
function ago(iso) {
  const m = Math.round((Date.now() - new Date(iso)) / 60000);
  if (m < 1) return '剛剛';
  if (m < 60) return `${m} 分鐘前`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} 小時前` : `${Math.round(h / 24)} 天前`;
}
function median(a) { const s = [...a].sort((x, y) => x - y); const n = s.length; return n ? (n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2) : 0; }
function fmtViews(n) { if (!n) return '—'; return n >= 10000 ? `${(n / 10000).toFixed(n >= 100000 ? 0 : 1)} 萬` : n.toLocaleString('zh-TW'); }
function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { el.hidden = true; }, 2600);
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (e2) { ok = false; }
    ta.remove();
    return ok;
  }
}
function download(name, text) {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

// ------------------------------------------------------------------ 設定（存在這支手機；有遙控鑰匙就同步到 GitHub）
function readPrefs() { try { return JSON.parse(localStorage.getItem(LS)) || {}; } catch (e) { return {}; } }
function writePrefs(sync = true) {
  S.prefs.updated = new Date().toISOString();
  try { localStorage.setItem(LS, JSON.stringify(S.prefs)); } catch (e) { /* 私密模式存不了，照樣運作 */ }
  if (sync) Remote.schedule();
}
// 刪除紀錄：兩台裝置同步時，才分得出「這台刪掉的」和「那台新加的」
function markDel(kind, id) { S.prefs.del = { ...(S.prefs.del || {}), [`${kind}:${id}`]: new Date().toISOString() }; }
function unDel(kind, id) {
  const k = `${kind}:${id}`;
  if (S.prefs.del && S.prefs.del[k]) { const d = { ...S.prefs.del }; delete d[k]; S.prefs.del = d; }
}
// 合併兩份方向設定：權重這類單一設定用比較新的那份；企劃、觀看數、靜音、角度、筆記照題目逐筆合併
function mergePrefs(a, b) {
  if (!b || typeof b !== 'object') return a;
  if (!a || typeof a !== 'object') return b;
  const aNew = (a.updated || '') >= (b.updated || '');
  const N = aNew ? a : b;
  const O = aNew ? b : a;
  const out = { ...O, ...N };
  for (const k of ['plan', 'videos', 'mute', 'angle', 'notes']) out[k] = { ...(O[k] || {}), ...(N[k] || {}) };
  const maxMerge = (x, y) => { const r = { ...(x || {}) }; for (const [k, v] of Object.entries(y || {})) if (!r[k] || v > r[k]) r[k] = v; return r; };
  out.watchAt = maxMerge(O.watchAt, N.watchAt);
  out.watch = [...new Set([...(N.watch || []), ...(O.watch || [])])];
  const del = maxMerge(O.del, N.del);
  const cut = new Date(Date.now() - 60 * 86400000).toISOString();
  for (const [k, when] of Object.entries(del)) {
    if (when < cut) { delete del[k]; continue; }
    const i = k.indexOf(':');
    const kind = k.slice(0, i);
    const id = k.slice(i + 1);
    // 刪掉之後又重新加回來的（加入時間比刪除晚）就保留
    if (kind === 'plan' && out.plan[id] && (out.plan[id].at || '') <= when) delete out.plan[id];
    else if (kind === 'mute' && out.mute[id] && (out.mute[id].at || '') <= when) delete out.mute[id];
    else if (kind === 'watch' && (out.watchAt[id] || '') <= when) out.watch = out.watch.filter((w) => w !== id);
  }
  out.del = del;
  out.updated = N.updated || O.updated;
  return out;
}
function getKey() { try { return localStorage.getItem(LS_KEY) || ''; } catch (e) { return ''; } }
function setKey(v) { try { if (v) localStorage.setItem(LS_KEY, v); else localStorage.removeItem(LS_KEY); } catch (e) { /* ignore */ } }
const P = {
  get weights() { return S.prefs.weights || S.meta.channel.weights; },
  get share() { return S.prefs.twShare ?? S.meta.channel.taiwan_share; },
  get lead() { return S.prefs.lead ?? S.meta.channel.lead_days; },
  pillar(p) { return (S.prefs.pillar || {})[p] ?? 1; },
  get watch() { return S.prefs.watch || []; },
  get mute() { return S.prefs.mute || {}; },
  get plan() { return S.prefs.plan || {}; },
  angle(id) { return (S.prefs.angle || {})[id]; },
};

// ------------------------------------------------------------------ 頻道成績 → 各路線的倍率
function videos() {
  const own = S.prefs.videos || {};
  const seed = (S.meta.videos || []).map((v) => ({ ...v, ...(own[v.id] || {}) }));
  const fromPlan = Object.entries(P.plan).filter(([, x]) => x.status === 'live' || x.status === 'making')
    .map(([id, x]) => ({ id: `plan:${id}`, title: x.title, pillar: x.pillar, date: x.date, status: x.status, views: x.views, score: x.score, fromPlan: id }));
  return [...seed, ...fromPlan];
}
function learned() {
  const vs = videos().filter((v) => v.views > 0 && (v.status === 'live' || v.status === 'done'));
  const out = { n: vs.length, mult: {}, med: {}, all: 0 };
  if (vs.length < 3) return out;
  out.all = median(vs.map((v) => v.views));
  for (const p of PILLAR_ORDER) {
    const xs = vs.filter((v) => v.pillar === p).map((v) => v.views);
    if (!xs.length) continue;
    out.med[p] = median(xs);
    out.mult[p] = clamp(Math.sqrt(out.med[p] / out.all), 0.75, 1.3);
  }
  return out;
}

// ------------------------------------------------------------------ 評分（跟後台同一套公式，套上你的方向設定）
function grade(s) { return s >= 70 ? '必拍' : s >= 58 ? '可拍' : s >= 46 ? '觀望' : '先跳過'; }
function deadlineTiming(today, open, startBy, publishBy, close) {
  if (close && today > close) return [null, '已經過了'];
  if (open && today < open) {
    const k = daysBetween(today, open);
    if (k > 3) return [22, `${k} 天後才開始熱（${md(open)} 起）`];
    // 再幾天就開始熱：照開工日算，但最多 60（跟後台同一套規則）
    const [tm] = deadlineTiming(today, null, startBy, publishBy, close);
    return [Math.min(60, tm ?? 22), `${k} 天後開始熱（${md(open)} 起），現在可以先準備`];
  }
  const d = startBy ? daysBetween(today, startBy) : 99;
  if (publishBy && today > publishBy) return [45, `最佳上架日 ${md(publishBy)} 已過，現在補拍還來得及到 ${md(close)}`];
  if (d < 0) {
    const left = daysBetween(today, publishBy);
    if (left <= 0) return [72, '今天就是最後上架日，趕不上就換成下一階段的角度'];
    return [88, `時間很趕：最晚 ${mdw(publishBy)}要上架，只剩 ${left} 天`];
  }
  if (d === 0) return [100, `今天就要開工，${mdw(publishBy)}前上架`];
  if (d <= 2) return [92, `${d} 天內要開工（${md(startBy)}），${md(publishBy)} 前上架`];
  if (d <= 6) return [78, `本週開工（${md(startBy)} 前），${md(publishBy)} 前上架`];
  if (d <= 13) return [60, `下週開工就好，${md(publishBy)} 前上架`];
  if (d <= 27) return [42, `還有時間，${md(startBy)} 前開工`];
  return [28, `不急，${md(startBy)} 前開工`];
}
// 比對關注清單時不管大小寫、空白與標點（「現代戰爭4」也對得到「現代戰爭 4」），遊戲的其他叫法（MW4）也算
const nk = (s) => String(s || '').toLowerCase().replace(/[\s\p{P}\p{S}]+/gu, '');
function watchHit(t) {
  const g = t.game || {};
  const hay = nk([t.title, g.zh, g.en, ...(g.aliases || [])].join(' '));
  return P.watch.find((w) => nk(w) && hay.includes(nk(w))) || null;
}
// 「為什麼是現在」的最後一句是時效：App 重算過時效（過了午夜、改了製作天數）就換成重算後的那句，才不會跟「今天開工」這類標籤對不上
function whyNow(t, ttext) {
  const why = t.why || '';
  const t0 = ((t.reasons || {}).timing || [])[0];
  return ttext && t0 && ttext !== t0 && why.endsWith(t0) ? why.slice(0, why.length - t0.length) + ttext : why;
}
function evaluate() {
  const meta = S.meta;
  const today = todayISO();
  const L = learned();
  const w = P.weights;
  const wsum = KEYS.reduce((a, k) => a + (w[k] || 0), 0) || 1;
  const s0 = meta.channel.taiwan_share;
  const out = [];
  for (const t of S.radar.topics) {
    let { demand, timing, fit, ease } = t.scores;
    const reasons = { demand: [...(t.reasons.demand || [])], timing: [...(t.reasons.timing || [])], fit: [...(t.reasons.fit || [])], ease: [...(t.reasons.ease || [])] };
    if (t.demand_parts && t.demand_parts.global != null && P.share !== s0) {
      demand = clamp(demand + (P.share - s0) * (t.demand_parts.tw - t.demand_parts.global));
    }
    const win = t.window || {};
    let startBy = win.start_by;
    let ttext = win.text;
    if (win.kind === 'deadline' && win.publish_by) {
      // 週報最多抓 2 天、年度榜單至少 5 天：跟後台同一套規則
      const lead = Math.min(Math.max(P.lead, win.lead_min || 0), win.lead_max || 99);
      startBy = addDays(win.publish_by, -lead);
      const [tm, tx] = deadlineTiming(today, win.open, startBy, win.publish_by, win.close);
      if (tm == null) continue;
      timing = tm;
      ttext = tx;
      reasons.timing = [tx];
    } else if (win.publish_by) {
      startBy = addDays(win.publish_by, -Math.min(P.lead, 2));
    }
    const pm = P.pillar(t.pillar) * (L.mult[t.pillar] || 1);
    if (pm !== 1) {
      fit = clamp(fit * pm);
      if (L.mult[t.pillar] && L.mult[t.pillar] !== 1) reasons.fit.push(`依頻道成績，${t.pillar_label}路線 ×${L.mult[t.pillar].toFixed(2)}`);
      if (P.pillar(t.pillar) !== 1) reasons.fit.push(`你的路線偏好 ×${P.pillar(t.pillar).toFixed(1)}`);
    }
    const wh = watchHit(t);
    if (wh) { fit = clamp(fit + 15); reasons.fit.push(`符合你的關注清單「${wh}」`); }
    const sc = { demand, timing, fit, ease };
    const base = KEYS.reduce((a, k) => a + sc[k] * (w[k] || 0), 0) / wsum;
    const total = base * (0.6 + 0.4 * demand / 100);
    const raw = startBy ? daysBetween(today, startBy) : 99;
    const days = Math.max(0, raw);
    // 等級照畫面上顯示的整數分數判斷（跟後台一樣），不會出現「58 分卻是觀望」
    const disp = Math.round(total);
    out.push({ ...t, _why: whyNow(t, ttext), _s: sc, _score: total, _disp: disp, _grade: grade(disp), _startBy: startBy, _ttext: ttext, _days: days, _raw: raw,
      _reasons: reasons, _watch: wh, _muted: !!P.mute[t.id], _plan: P.plan[t.id] || null });
  }
  out.sort((a, b) => b._score - a._score);
  S.list = out;
  S.byId = Object.fromEntries(out.map((t) => [t.id, t]));
  // TOP 5（跟後台 pick_top 同一套規則）：已經在做或做過的、靜音的不算。照分數分幾輪挑：
  // 1. 必拍（70 以上）同一條路線、同一種題目最多 3 個；2. 可拍（58 以上）最多 2 個（含已選的）；
  // 3. 不滿 5 個：可拍放寬到 3 個、再不限路線（寧可多一個同路線的高分題，也不拿觀望題湊多樣性）；
  // 4. 可拍以上都用完了，才用分數較低的補。
  const pool = out.filter((t) => !(t._muted || (t.done && ['done', 'making'].includes(t.done.status)) || (t._plan && ['making', 'live'].includes(t._plan.status))));
  const top = [];
  const bp = {};
  const bt = {};
  for (const [floor, cap] of [[70, 3], [58, 2], [58, 3], [58, 99], [0, 2], [0, 99]]) {
    for (const t of pool) {
      if (top.length >= 5) break;
      if (top.includes(t) || t._disp < floor) continue;
      if ((bp[t.pillar] || 0) >= cap || (bt[t.type] || 0) >= cap) continue;
      top.push(t);
      bp[t.pillar] = (bp[t.pillar] || 0) + 1;
      bt[t.type] = (bt[t.type] || 0) + 1;
    }
  }
  top.sort((a, b) => b._score - a._score);
  S.top = top;
}

// ------------------------------------------------------------------ 共用片段
function pillarChip(t) { return `<span class="chip p-${t.pillar}">${esc(S.meta.channel.pillars[t.pillar].label)}</span>`; }
function dueText(t) {
  const today = todayISO();
  if (!t._startBy) return '<span class="due">不急</span>';
  const d = daysBetween(today, t._startBy);
  const pub = t.window && t.window.publish_by;
  if (pub && today > pub) return t.window.close && today > t.window.close ? '<span class="due">保鮮期已過</span>' : `<span class="due">補拍到 ${md(t.window.close)}</span>`;
  if (d <= 0) return `<span class="due hot">${pub && daysBetween(today, pub) <= 0 ? '今天要上架' : '今天開工'}</span>`;
  if (d <= 3) return `<span class="due warm">最晚 ${md(t._startBy)} 開工</span>`;
  return `<span class="due">${md(t._startBy)} 前開工</span>`;
}
function meter(t) {
  return `<span class="meter" aria-hidden="true">${KEYS.map((k) => `<span style="--h:${Math.round(t._s[k])}%"></span>`).join('')}</span>`;
}
function stationLabel(id) { const s = (S.meta.stations || []).find((x) => x.id === id); return s ? s.label : id; }

// ------------------------------------------------------------------ 回波頁
function renderObs() {
  const g = S.radar.generated;
  const next = new Date(new Date(g).getTime() + 60 * 60000);
  $('#obs').innerHTML = `<b>${hm(g)}</b> 觀測，${ago(g)}`;
  $('#obs').title = `約 ${hm(next.toISOString())} 自動再掃一次`;
}
function renderEcho() {
  renderObs();
  const items = S.list.filter((t) => !t._muted && t._score >= 34).slice(0, 70).map((t) => ({
    id: t.id, score: t._score, pillar: t.pillar, days: t._days, title: t.title,
    rank: S.top.indexOf(t) >= 0 ? S.top.indexOf(t) + 1 : null,
  }));
  if (!S.scope) {
    S.scope = new Scope($('#scope'), {
      onPick: (id) => openTopic(id),
      onHover: (b) => {
        const tip = $('#scope-tip');
        if (!b) { tip.hidden = true; return; }
        const t = S.byId[b.id];
        tip.innerHTML = `<b>${Math.round(t._score)}</b> ${esc(t.title)}`;
        tip.style.left = `${Math.min(b.sx + 12, $('#scope').clientWidth - 250)}px`;
        tip.style.top = `${b.sy + 12}px`;
        tip.hidden = false;
      },
    });
  }
  S.scope.setLabels(Object.fromEntries(PILLAR_ORDER.map((p) => [p, S.meta.channel.pillars[p].short])));
  S.scope.draw(items);

  const today = todayISO();
  const free = (t) => !t._muted && t._disp >= 58 && !(t.done && ['done', 'making'].includes(t.done.status))
    && !(t._plan && ['making', 'live'].includes(t._plan.status));
  const urgent = S.list.filter((t) => free(t) && t._raw === 0 && t.window && t.window.publish_by > today);
  const late = S.list.filter((t) => free(t) && t._raw < 0 && t.window && t.window.publish_by >= today);
  const al = $('#alert');
  const short = (t) => esc(t.game ? `《${t.game.name}》` : t.title.split('：')[0]);
  if (urgent.length || late.length) {
    al.hidden = false;
    const a = urgent.length ? `<b>今天要開工 ${urgent.length} 題</b>` : '';
    const b = late.length ? `${urgent.length ? '，另外 ' : ''}<b>${late.length} 題時間很趕</b>` : '';
    const names = [...urgent, ...late].slice(0, 2).map(short).join('、');
    al.innerHTML = `<span aria-hidden="true">⚠</span><span>${a}${b}。${names} 等。<button type="button" class="linkbtn" data-urgent>看是哪些</button></span>`;
  } else al.hidden = true;

  $('#top').innerHTML = S.top.map((t, i) => `
    <li class="trow" style="--c:${echoColor(t._score)}">
      <button class="tbtn" type="button" data-topic="${esc(t.id)}">
        <span class="rk">${i + 1}</span>
        <span class="tmain">
          <span class="tt">${esc(t.title)}</span>
          <span class="tmeta">${pillarChip(t)}${t.new ? '<span class="chip new">新上榜</span>' : ''}${t._watch ? '<span class="chip new">關注</span>' : ''}${dueText(t)}</span>
          <span class="why">${esc(t._why)}</span>
        </span>
        <span class="tscore"><b>${Math.round(t._score)}</b><i>${t._grade}</i>${meter(t)}</span>
      </button>
    </li>`).join('') || '<li class="empty">目前沒有夠強的回波。等下一輪掃描，或到「方向」調整權重。</li>';
  const h = S.meta.health || {};
  const okN = Object.values(h).filter((x) => x.ok).length;
  $('#echo-foot').textContent = `題庫共 ${S.list.length} 題，測站 ${okN}/${Object.keys(h).length} 正常。分數旁的四條是需求、時效、契合、好做。資料來自 Steam、PTT、台灣 Google 新聞與搜尋趨勢、Wikipedia、Reddit，以及《玩真的》整理的新聞。`;
}

// ------------------------------------------------------------------ 題庫
const FILTERS = [['all', '全部'], ['hot', '必拍與可拍'], ['season', '檔期推薦'], ['issue', '產業爭議'], ['ranking', '期待榜單'],
  ['hardware', '硬體科技'], ['spotlight', '新作焦點'], ['watch', '關注'], ['muted', '已靜音']];
function renderList() {
  $('#filters').innerHTML = FILTERS.map(([k, l]) => `<button type="button" class="fbtn" data-filter="${k}" aria-pressed="${S.filter === k}">${l}</button>`).join('');
  let xs = S.list.filter((t) => (S.filter === 'muted' ? t._muted : !t._muted));
  if (S.filter === 'hot') xs = xs.filter((t) => t._disp >= 58);
  else if (PILLAR_ORDER.includes(S.filter)) xs = xs.filter((t) => t.pillar === S.filter);
  else if (S.filter === 'watch') xs = xs.filter((t) => t._watch);
  if (S.q) {
    const q = S.q.toLowerCase();
    xs = xs.filter((t) => `${t.title} ${t.game ? `${t.game.zh || ''} ${t.game.en || ''}` : ''} ${t._why}`.toLowerCase().includes(q));
  }
  if (S.sort === 'due') xs = [...xs].sort((a, b) => a._days - b._days || b._score - a._score);
  if (S.sort === 'new') xs = [...xs].sort((a, b) => (b.first_seen || '').localeCompare(a.first_seen || ''));
  if (S.sort === 'demand') xs = [...xs].sort((a, b) => b._s.demand - a._s.demand);
  $('#rows').innerHTML = xs.map((t) => `
    <li class="row" style="--c:${echoColor(t._score)}">
      <button class="rbtn" type="button" data-topic="${esc(t.id)}">
        <span class="dot" aria-hidden="true"></span>
        <span class="rmain"><span class="rt">${esc(t.title)}</span>
          <span class="tmeta">${pillarChip(t)}<span class="chip">${esc(t.type_label)}</span>${t.done ? `<span class="chip flag">${esc(STATUS[t.done.status] || '做過')}</span>` : ''}${(t.flags || []).map((f) => `<span class="chip flag">${esc(f)}</span>`).join('')}${dueText(t)}</span></span>
        <span class="rs">${Math.round(t._score)}</span>
      </button>
    </li>`).join('') || `<li class="empty">${S.filter === 'muted' ? '沒有靜音的題目。' : '沒有符合的題目，換個篩選或關鍵字試試。'}</li>`;
}

// ------------------------------------------------------------------ 預報
const CAL_KIND = { steam_sale: 'Steam 特賣', steam_fest: 'Steam 活動', steam_theme: 'Steam 主題節', show: '大型活動', retail: '購物檔期', release: '發售', deadline: '截止' };
function renderCal() {
  const today = todayISO();
  const items = (S.cal.items || []).filter((x) => (x.end || x.date) >= today);
  // 截止日用你的製作天數重算
  const dl = [];
  for (const t of S.list) {
    if (t._muted || t._disp < 46 || !t.window || !t.window.publish_by) continue;
    if (t._startBy && t._startBy >= today) dl.push({ date: t._startBy, kind: 'deadline', title: `最晚開工：${t.title}`, ref: t.id, score: t._score });
    if (t.window.publish_by >= today) dl.push({ date: t.window.publish_by, kind: 'deadline', title: `最晚上架：${t.title}`, ref: t.id, score: t._score, pub: true });
  }
  const all = [...items.filter((x) => x.kind !== 'deadline'), ...dl];
  const byDay = {};
  for (const x of all) {
    const d = x.date < today ? today : x.date;
    (byDay[d] = byDay[d] || []).push(x);
  }
  const days = Object.keys(byDay).sort().slice(0, 45);
  $('#cal').innerHTML = days.map((d) => {
    const dd = dayOf(d);
    const rel = daysBetween(today, d);
    const lab = rel === 0 ? '今天' : rel === 1 ? '明天' : rel === 2 ? '後天' : `週${WD[dd.getDay()]}`;
    const rows = byDay[d].sort((a, b) => ['steam_sale', 'steam_fest', 'show', 'retail', 'release', 'deadline', 'steam_theme'].indexOf(a.kind)
      - ['steam_sale', 'steam_fest', 'show', 'retail', 'release', 'deadline', 'steam_theme'].indexOf(b.kind)).map((x) => {
      const span = x.end && x.end !== x.date ? `～${md(x.end)}` : '';
      const ref = x.ref && (S.byId[x.ref] || findTopicByGame(x.ref));
      let sub = x.when || x.note || '';
      if (x.kind === 'deadline' && ref) sub = `總分 ${Math.round(ref._score)}，${ref._grade}${ref.done ? `，${STATUS[ref.done.status] || '做過'}` : ''}${ref._plan ? `，${STATUS[ref._plan.status]}` : ''}`;
      else if (x.kind === 'release') sub = ref ? `雷達 ${Math.round(ref._score)} 分` : '';
      else if (!sub) sub = CAL_KIND[x.kind] || '';
      const attr = ref ? `data-topic="${esc(ref.id)}"` : safeUrl(x.u) ? `data-href="${esc(safeUrl(x.u))}"` : '';
      return `<button type="button" class="citem k-${esc(x.kind)}" ${attr}><span class="k" aria-hidden="true"></span><span>${esc(x.title)}${span}${sub ? `<small>${esc(sub)}</small>` : ''}</span></button>`;
    }).join('');
    return `<div class="cal-day"><div class="cal-d ${rel === 0 ? 'today' : ''}"><b>${dd.getMonth() + 1}/${dd.getDate()}</b><span>${lab}</span></div>${rows}</div>`;
  }).join('') || '<p class="empty">接下來沒有排定的檔期。</p>';
}
function findTopicByGame(gid) { return S.list.find((t) => t.game && t.game.gid === gid) || null; }

// ------------------------------------------------------------------ 企劃
function renderPlan() {
  const plan = P.plan;
  const lanes = { idea: [], making: [], live: [] };
  for (const [id, x] of Object.entries(plan)) if (lanes[x.status]) lanes[x.status].push({ id, ...x });
  const L = learned();
  const card = (x) => {
    const t = S.byId[x.id];
    const score = t ? Math.round(t._score) : x.score ? Math.round(x.score) : null;
    return `<div class="card" data-plan="${esc(x.id)}">
      <div class="card-t">${esc(x.title)}</div>
      <div class="card-m">${esc(S.meta.channel.pillars[x.pillar] ? S.meta.channel.pillars[x.pillar].label : '')}${score != null ? `，加入時雷達 ${Math.round(x.score || 0)} 分${t ? `、現在 ${score} 分` : ''}` : ''}${x.angle != null && t && t.angles[x.angle] ? `，角度：${esc(t.angles[x.angle].label)}` : ''}</div>
      <div class="card-row">
        ${['idea', 'making', 'live'].map((s) => `<button type="button" class="mini ${x.status === s ? 'on' : ''}" data-plan-status="${s}">${STATUS[s]}</button>`).join('')}
        ${t ? '<button type="button" class="mini" data-plan-open>打開</button>' : ''}
        <button type="button" class="mini" data-plan-del>移除</button>
      </div>
      ${x.status === 'live' ? `<div class="card-row"><label class="field"><span>上片日期</span><input type="date" value="${esc(x.date || '')}" data-plan-date></label>
        <label class="field"><span>7 天觀看數</span><input type="number" inputmode="numeric" min="0" step="1" value="${esc(x.views || '')}" placeholder="例如 12000" data-plan-views></label></div>` : ''}
    </div>`;
  };
  const seed = videos().filter((v) => !v.fromPlan);
  const vcard = (v) => `<div class="card" data-video="${esc(v.id)}">
      <div class="card-t">${esc(v.title)}</div>
      <div class="card-m">${esc(S.meta.channel.pillars[v.pillar] ? S.meta.channel.pillars[v.pillar].label : '')}，${esc(v.date || '')}，${esc(STATUS[v.status] || v.status)}</div>
      <div class="card-row">
        ${['making', 'live'].map((s) => `<button type="button" class="mini ${v.status === s || (s === 'live' && v.status === 'done') ? 'on' : ''}" data-video-status="${s}">${STATUS[s]}</button>`).join('')}
      </div>
      <div class="card-row"><label class="field"><span>7 天觀看數</span><input type="number" inputmode="numeric" min="0" step="1" value="${esc(v.views || '')}" placeholder="還沒填" data-video-views></label></div>
    </div>`;
  let html = '';
  for (const [k, lab, empty] of [['idea', '想拍', '在題目裡按「想拍」，就會出現在這裡。'], ['making', '製作中', '開始做的題目，點「製作中」移到這裡。'], ['live', '已上片', '上片後點「已上片」，記得回填 7 天觀看數。']]) {
    html += `<div class="lane"><h3>${lab}<span>${lanes[k].length} 個</span></h3>${lanes[k].map(card).join('') || `<p class="lede">${empty}</p>`}</div>`;
  }
  html += `<div class="lane"><h3>頻道做過的影片<span>${seed.length} 支</span></h3><p class="lede">雷達用這份清單判斷「已經做過」。觀看數填越多，下面的路線成績越準。</p>${seed.map(vcard).join('')}</div>`;
  const pillars = PILLAR_ORDER.filter((p) => L.med[p]);
  html += `<div class="lane"><h3>路線成績<span>${L.n} 支有觀看數</span></h3>`;
  if (L.n < 3) html += '<p class="lede">再填 ' + (3 - L.n) + ' 支影片的觀看數，雷達就會開始依各路線的成績調整契合度。</p>';
  else {
    const mx = Math.max(...pillars.map((p) => L.med[p]));
    html += `<div class="bars">${pillars.map((p) => `<div class="brow"><span>${esc(S.meta.channel.pillars[p].short)}</span><span class="b"><i style="--w:${Math.round(100 * L.med[p] / mx)}%"></i></span><span class="v">${fmtViews(L.med[p])}</span></div>`).join('')}</div>`;
    html += `<p class="lede" style="margin-top:10px">全頻道中位數 ${fmtViews(L.all)}。契合度倍率：${pillars.map((p) => `${S.meta.channel.pillars[p].short} ×${L.mult[p].toFixed(2)}`).join('，')}。</p>`;
    const hits = videos().filter((v) => v.views > 0 && v.score != null);
    if (hits.length >= 3) {
      const ok = hits.filter((v) => (v.score >= 58) === (v.views >= L.all)).length;
      html += `<p class="lede">預報命中：${ok}/${hits.length}（雷達說「可拍以上」而且觀看數高於中位數，或反過來，都算命中）。</p>`;
    }
  }
  html += '</div>';
  $('#plan').innerHTML = html;
}

// ------------------------------------------------------------------ 方向
function renderSet() {
  const w = P.weights;
  const L = learned();
  const h = S.meta.health || {};
  const hasKey = !!getKey();
  const sl = (id, label, val, min, max, step, out) => `<div class="slider"><label for="${esc(id)}">${esc(label)}</label><input id="${esc(id)}" type="range" min="${min}" max="${max}" step="${step}" value="${esc(val)}"><output for="${esc(id)}">${esc(out)}</output></div>`;
  const healthNames = { forreal_news: '玩真的（遊戲新聞）', forreal_tech: '玩真的（科技新聞）', steam_wishlist: 'Steam 願望單', steam_upcoming: 'Steam 即將推出', steam_trending: 'Steam 熱門新品',
    steam_topsell: 'Steam 全球熱銷', steam_topsell_tw: 'Steam 台灣熱銷', steam_mostplayed: 'Steam 最多人玩', steam_players: 'Steam 在線人數', steam_details: 'Steam 商店資料',
    steam_reviews: 'Steam 評論', steamspy: 'SteamSpy', wiki_pageviews: 'Wikipedia 瀏覽量', wikidata: 'Wikidata 條目對應', wikidata_titles: 'Wikidata 中文條目',
    gnews: '台灣 Google 新聞', gtrends: 'Google 搜尋趨勢（台灣）', reddit: 'Reddit' };
  const ptt = Object.keys(h).filter((k) => k.startsWith('ptt_'));
  const hrows = [...Object.keys(healthNames).filter((k) => h[k]).map((k) => [healthNames[k], h[k]]),
    ...ptt.map((k) => [`PTT ${k.slice(4)} 板`, h[k]])];
  $('#set').innerHTML = `
  <div class="group"><h3>四項分數的權重</h3><p>總分＝四項加權平均 × 需求係數。需求永遠是乘數：沒人看的題目，再準時、再好做也沒用。</p>
    ${KEYS.map((k) => sl(`w-${k}`, KEY_LABEL[k], w[k], 0, 60, 5, w[k])).join('')}
    <div class="preview" id="preview"></div>
  </div>
  <div class="group"><h3>台灣觀眾比重</h3><p>需求分數裡，台灣測站（PTT、台灣新聞、Google 趨勢、中文維基、Steam 台灣）佔多少。</p>
    ${sl('share', '台灣', Math.round(P.share * 100), 0, 80, 5, `${Math.round(P.share * 100)}%`)}
  </div>
  <div class="group"><h3>製作天數</h3><p>從開工到上架通常要幾天。雷達用它倒推「最晚開工日」。</p>
    ${sl('lead', '天數', P.lead, 1, 7, 1, `${P.lead} 天`)}
  </div>
  <div class="group"><h3>路線偏好</h3><p>想多做哪條路線就調高。${L.n >= 3 ? '頻道成績算出的倍率會自動再乘上去。' : '回填 3 支影片的觀看數後，雷達也會依成績自動調整。'}</p>
    ${PILLAR_ORDER.map((p) => sl(`pl-${p}`, S.meta.channel.pillars[p].short, P.pillar(p), 0.5, 1.5, 0.1, `×${P.pillar(p).toFixed(1)}`)).join('')}
  </div>
  <div class="group"><h3>關注清單</h3><p>你特別想追的遊戲或關鍵字。對到的題目契合度加 15，題庫可以單獨篩出來。</p>
    <div class="inline"><label class="search"><span class="sr">新增關注</span><input id="watch-in" type="text" placeholder="例如：GTA、魂系、Switch 2" autocomplete="off"></label><button type="button" class="btn" id="watch-add">加入</button></div>
    <div class="tags">${P.watch.map((x, i) => `<span class="tag">${esc(x)}<button type="button" data-watch-del="${i}" aria-label="移除 ${esc(x)}">×</button></span>`).join('') || '<span class="lede">還沒有關注任何關鍵字。</span>'}</div>
  </div>
  <div class="group"><h3>已靜音的題目</h3><p>靜音的題目不會出現在雷達與 TOP 5。</p>
    <div class="tags">${Object.entries(P.mute).map(([id, x]) => `<span class="tag">${esc(x.title || id)}<button type="button" data-unmute="${esc(id)}" aria-label="取消靜音">×</button></span>`).join('') || '<span class="lede">沒有靜音的題目。</span>'}</div>
  </div>
  <div class="group" id="keygroup"><h3>遙控鑰匙</h3>
    <p>${hasKey ? '已設定。按「掃描」會直接叫 GitHub 重新抓一輪（約 3 分鐘），這裡的方向設定也會同步到你其他裝置。' : '雷達每小時自動掃描一次。想隨時按「掃描」立刻重抓，並讓筆電與手機共用同一份方向設定，需要一把只能控制這個 repo 的鑰匙。'}</p>
    ${hasKey ? `<div class="btn-row"><button type="button" class="btn" id="key-test">測試鑰匙</button><button type="button" class="btn ghost" id="key-del">移除鑰匙</button></div>`
      : `<ol class="steps">
        <li>用瀏覽器登入 GitHub，打開 <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">建立新鑰匙的頁面</a>（Fine-grained token）。</li>
        <li>名稱填 <code>回波遙控鑰匙</code>，期限選最長。</li>
        <li>Repository access 選「Only select repositories」，只勾 <code>echo-radar</code>。</li>
        <li>Permissions 裡把 <code>Actions</code> 和 <code>Contents</code> 都設成「Read and write」。</li>
        <li>按 Generate token，複製那串字，貼到下面。鑰匙只存在這台裝置，不會上傳。</li>
      </ol>
      <div class="keybox"><label class="field"><span>鑰匙</span><input id="key-in" type="password" autocomplete="off" placeholder="github_pat_…"></label><button type="button" class="btn primary" id="key-save">儲存並測試</button></div>`}
    <p class="lede" style="margin-top:10px">同步的方向設定（權重、關注、企劃與觀看數）會存在公開的 echo-radar repo 的 user 分支；鑰匙本身不會上傳。</p>
  </div>
  <div class="group"><h3>測站狀態</h3><p>每一輪掃描時各資料來源的狀況。PTT 官網會擋掉 GitHub 的伺服器，所以改讀 PTT 網頁版鏡像站，連結一律指回 PTT 原文。</p>
    <ul class="health">${hrows.map(([n, x]) => `<li><span class="${x.ok ? 'ok' : 'bad'}"></span><span>${esc(n)}${x.err ? `<br><small>${esc(x.err)}</small>` : ''}</span><small>${x.n != null ? `${x.n} 筆` : ''}</small></li>`).join('')}</ul>
  </div>
  <div class="group"><h3>備份與還原</h3><p>沒有遙控鑰匙時，可以用這個把設定搬到另一台裝置。</p>
    <div class="btn-row"><button type="button" class="btn" id="exp">複製設定</button><button type="button" class="btn" id="imp">貼上設定</button></div>
    <div class="btn-row"><button type="button" class="btn ghost" id="reset">權重恢復預設</button><a class="btn ghost" href="https://github.com/${esc(S.meta.repo)}/actions" target="_blank" rel="noopener">GitHub 執行紀錄</a></div>
  </div>
  <p class="foot">回波 build ${esc(S.meta.build)}，資料 ${esc(S.radar.generated)}。${UPDATES.length ? '<button type="button" class="linkbtn" data-updates>更新紀錄</button>' : ''}</p>`;
  renderPreview();
}

// ------------------------------------------------------------------ 更新紀錄（App 改版後第一次打開時跳出來一次）
function updatesHtml(list) {
  const entry = (u, i) => `${i ? `<h3 class="upd-h">${esc(u.title)}</h3>` : ''}<p class="upd-d">${esc(u.date.replace(/-/g, '/'))}</p>
    <ul class="upd">${u.items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
  return `<div class="sh-top"><button type="button" class="ibtn" data-close aria-label="關閉">${ICON.close}</button><span class="sp"></span></div>
  <h2 class="sh-title" id="sh-title">${esc(list[0].title)}</h2>
  ${list.map(entry).join('')}
  <button type="button" class="btn primary wide upd-ok" data-close>知道了</button>`;
}
function showUpdates(all = false) {
  const last = UPDATES[0];
  if (!last) return;
  if (!all) {
    let seen = '';
    try { seen = localStorage.getItem(LS_SEEN) || ''; } catch (e) { /* 私密模式讀不到 */ }
    if (seen === last.id) return;
  }
  try { localStorage.setItem(LS_SEEN, last.id); } catch (e) { /* 存不了，下次打開會再看到一次 */ }
  openSheet(updatesHtml(all ? UPDATES : [last]));
  if (!all) {
    // 自己跳出來的時候把焦點放在標題（讀得到內容、按 Tab 就到「知道了」），關閉鈕不會一打開就框著黃框
    const h = $('#sh-title');
    if (h) { h.tabIndex = -1; h.focus({ preventScroll: true }); }
  }
}
function renderPreview() {
  const el = $('#preview');
  if (!el) return;
  el.innerHTML = `照現在的權重，本週 TOP 5 會是：<ol>${S.top.map((t) => `<li>${esc(t.title)}（${Math.round(t._score)}）</li>`).join('')}</ol>`;
}

// ------------------------------------------------------------------ 題目詳情
function openSheet(html, onClose) {
  const sh = $('#sheet');
  $('#sheet-in').innerHTML = html;
  sh.hidden = false;
  document.body.style.overflow = 'hidden';
  S.onClose = onClose || null;
  $('#sheet-in').scrollTop = 0;
  const f = $('#sheet-in [data-close]');
  if (f) f.focus({ preventScroll: true });
}
function closeSheet() {
  $('#sheet').hidden = true;
  document.body.style.overflow = '';
  S.open = null;
  if (S.onClose) S.onClose();
  S.onClose = null;
}
const ICON = {
  close: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  star: '<svg viewBox="0 0 24 24"><path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9 6.8 19.6l1-5.8-4.3-4.1 5.9-.9z"/></svg>',
  mute: '<svg viewBox="0 0 24 24"><path d="M4 9h4l5-4v14l-5-4H4z"/><path d="m17 9 4 6M21 9l-4 6"/></svg>',
  doc: '<svg viewBox="0 0 24 24"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/></svg>',
  chat: '<svg viewBox="0 0 24 24"><path d="M4 5h16v11H9l-5 4z"/></svg>',
  copy: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/></svg>',
  dl: '<svg viewBox="0 0 24 24"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>',
  share: '<svg viewBox="0 0 24 24"><path d="M12 15V4M8 8l4-4 4 4"/><path d="M5 12v8h14v-8"/></svg>',
};
function selectedAngle(t) {
  const i = P.angle(t.id);
  return i != null && t.angles[i] ? i : null;
}
function windowLine(t) {
  const w = t.window || {};
  if (!w.publish_by) return '';
  const today = todayISO();
  const pts = [['開始熱', w.open], ['最晚開工', t._startBy], ['最晚上架', w.publish_by], ['收攤', w.close]].filter((x) => x[1]);
  pts.push(['今天', today]);
  pts.sort((a, b) => a[1].localeCompare(b[1]) || (a[0] === '今天' ? -1 : 1));
  const rel = (d) => { const n = daysBetween(today, d); return n === 0 ? '今天' : n > 0 ? `${n} 天後` : `${-n} 天前`; };
  return `<ol class="miles">${pts.map(([lab, d]) => `<li class="${lab === '今天' ? 'now' : d < today ? 'past' : ''}"><span>${lab}</span><b>${mdw(d)}</b><i>${lab === '今天' ? '' : rel(d)}</i></li>`).join('')}</ol>
    <p class="wtext">${esc(t._ttext || w.text || '')}</p>`;
}
function fmtAt(iso) {
  if (!iso) return '';
  if (/T00:00:00Z$/.test(iso)) return md(iso.slice(0, 10));
  const t = new Date(iso);
  return `${t.getMonth() + 1}/${t.getDate()} ${pad(t.getHours())}:${pad(t.getMinutes())}`;
}
function openTopic(id) {
  const t = S.byId[id];
  if (!t) return;
  S.open = id;
  const c = echoColor(t._score);
  const sel = selectedAngle(t);
  const plan = P.plan[id];
  const g = t.game;
  const regions = { global: [], tw: [] };
  for (const s of t.stations || []) {
    const meta = (S.meta.stations || []).find((x) => x.id === s.id) || { region: 'global' };
    regions[meta.region].push(s);
  }
  const stRow = (s) => `<li class="st" style="--c:${echoColor(s.level)}"><span class="st-n">${esc(stationLabel(s.id))}${s.up ? '<span class="up" title="升溫中">▲</span>' : ''}</span>
      <span class="st-b"><i style="--w:${Math.round(s.level)}%"></i></span>
      <span class="st-t">${link(s.url, esc(s.text))}</span></li>`;
  const html = `
  <div class="sh-top">
    <button type="button" class="ibtn" data-close aria-label="關閉">${ICON.close}</button><span class="sp"></span>
    <button type="button" class="ibtn" data-star aria-pressed="${!!plan}">${ICON.star}<span>${plan ? STATUS[plan.status] : '想拍'}</span></button>
    <button type="button" class="ibtn" data-mute aria-pressed="${t._muted}">${ICON.mute}<span>${t._muted ? '已靜音' : '靜音'}</span></button>
  </div>
  ${g && safeUrl(g.img) ? `<img class="hero-img" src="${esc(safeUrl(g.img))}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''}
  <h2 class="sh-title" id="sh-title">${esc(t.title)}</h2>
  <div class="sh-chips">${pillarChip(t)}<span class="chip">${esc(t.type_label)}</span>${t.new ? '<span class="chip new">新上榜</span>' : ''}${t.done ? `<span class="chip flag">${esc(STATUS[t.done.status] || '做過')}：${esc(t.done.title)}</span>` : ''}${t.covered ? `<span class="chip flag">${esc(t.covered.title)} 會提到</span>` : ''}${(t.flags || []).map((f) => `<span class="chip flag">${esc(f)}</span>`).join('')}</div>
  <div class="gauge" style="--c:${c}"><span class="gauge-n"><b>${Math.round(t._score)}</b></span>
    <span class="gauge-t"><strong>${t._grade}</strong><span>${esc(t._why)}</span></span></div>

  <div class="sec"><h3>四項分數</h3>
    <div class="scores">${KEYS.map((k) => `<button type="button" class="srow" data-reason="${k}" aria-expanded="false" style="--c:${k === 'demand' ? c : 'var(--e2)'}">
      <span class="sl">${KEY_LABEL[k]}</span><span class="sb"><i style="--w:${Math.round(t._s[k])}%"></i></span><span class="sn">${Math.round(t._s[k])}</span></button>
      <ul class="reasons" data-reasons="${k}" hidden><li>${esc(KEY_HINT[k])}</li>${(t._reasons[k] || []).map((r) => `<li>${esc(r)}</li>`).join('')}</ul>`).join('')}</div>
  </div>

  ${(t.stations || []).length ? `<div class="sec"><h3>測站觀測</h3><p class="note">▲ 代表正在升溫。點文字可以看原始資料。</p>
    ${regions.global.length ? `<p class="region">全球</p><ul class="stations">${regions.global.map(stRow).join('')}</ul>` : ''}
    ${regions.tw.length ? `<p class="region">台灣</p><ul class="stations">${regions.tw.map(stRow).join('')}</ul>` : ''}</div>` : ''}

  ${t.window && t.window.publish_by ? `<div class="sec"><h3>保鮮期</h3>${windowLine(t)}</div>` : ''}

  <div class="sec"><h3>角度三選一</h3><p class="note">選一個，企劃包和找 Claude 討論都會用你選的角度。</p>
    <div class="angles" role="radiogroup" aria-label="角度">${(t.angles || []).map((a, i) => `<button type="button" class="ang" role="radio" aria-checked="${sel === i}" data-angle="${i}">
      <span class="ang-k"><span class="kind k-${esc(a.kind)}">${esc(a.label)}</span>${a.kind === 'pz' ? '別人不會做、只有你做得出來' : a.kind === 'contrast' ? '從數據找到的衝突點' : '最安全的切法'}</span>
      <span class="ang-t">${esc(a.title)}</span><span class="ang-h">${esc(a.hook)}</span><span class="ang-w">依據：${esc(a.why)}</span>
      ${a.need ? `<span class="ang-need">${esc(a.need)}</span>` : ''}${a.risk ? `<span class="ang-need">${esc(a.risk)}</span>` : ''}</button>`).join('')}</div>
  </div>

  ${(t.facts || []).length ? `<div class="sec"><h3>事實清單</h3><p class="note">雷達抓到的資料，企劃包會要求開工時再查證一次。</p><ul class="facts">${t.facts.map((f) => `<li class="fact"><span class="fk">${esc(f.k)}</span><span>${esc(f.v)}</span><span class="fs">${safeUrl(f.u) ? link(f.u, esc(f.src || '來源')) : esc(f.src || '')}${f.at ? `，${fmtAt(f.at).split(' ')[0]}` : ''}</span></li>`).join('')}</ul></div>` : ''}

  ${(t.ptt || []).length ? `<div class="sec"><h3>PTT 在聊什麼</h3><ul class="posts">${t.ptt.map((p) => `<li><span class="push ${p.push >= 100 ? 'hot' : p.push >= 30 ? 'warm' : ''}">${p.push >= 100 ? '爆' : esc(p.push)}</span>${link(p.u, esc(p.t))}</li>`).join('')}</ul></div>` : ''}
  ${(t.reddit || []).length ? `<div class="sec"><h3>Reddit 在聊什麼</h3><ul class="posts">${t.reddit.map((p) => `<li><span class="push">#${esc(p.rank)}</span>${link(p.u, esc(p.t))}</li>`).join('')}</ul></div>` : ''}
  ${(t.timeline || []).length > 1 ? `<div class="sec"><h3>時間線</h3><ul class="tl">${t.timeline.map((x) => `<li><time>${fmtAt(x.at)}</time>${link(x.u, esc(x.t))}${x.src ? `（${esc(x.src)}）` : ''}</li>`).join('')}</ul></div>` : ''}
  ${(t.games || []).length ? `<div class="sec"><h3>這個檔期的候選遊戲</h3><ul class="posts">${t.games.map((x) => `<li><span class="push">${x.date ? md(x.date.length === 10 ? x.date : `${x.date}-01`) : ''}</span><span>《${esc(x.name)}》${x.big ? '（大作）' : ''}</span></li>`).join('')}</ul></div>` : ''}

  <div class="sec"><h3>我的想法</h3><label class="field"><span class="sr">寫下你對這題的想法</span><textarea id="note" placeholder="例如：想拿去年的經驗對比、想找朋友一起錄">${esc((P.plan[id] || {}).note || (S.prefs.notes || {})[id] || '')}</textarea></label></div>

  <div class="actions">
    <button type="button" class="btn primary" data-brief>${ICON.doc}產生企劃包</button>
    <button type="button" class="btn" data-claude>${ICON.chat}找 Claude 討論</button>
  </div>`;
  openSheet(html);
  const img = $('#sheet-in .hero-img');
  if (img) img.addEventListener('error', () => img.remove(), { once: true });
}
function topicCtx(t) {
  return { generated: S.radar.generated, score: t._score, grade: t._grade, scores: t._s, startBy: t._startBy, stationLabel,
    note: ($('#note') && $('#note').value) || ((S.prefs.notes || {})[t.id]) || '' };
}
function openBrief(id) {
  const t = S.byId[id];
  const si = selectedAngle(t);
  const angle = si != null ? t.angles[si] : t.angles[0];
  const md_ = makeBrief(t, angle, topicCtx(t));
  const fname = `${todayISO()}_${(angle ? angle.title : t.title).replace(/[\\/:*?"<>|《》「」？！，。、\s]+/g, '_').slice(0, 40)}_brief.md`;
  const html = `
  <div class="sh-top"><button type="button" class="ibtn" data-back="${esc(id)}" aria-label="回到題目">${ICON.close}</button><span class="sp"></span></div>
  <h2 class="sh-title" id="sh-title">企劃包</h2>
  <p class="lede">${si == null ? '還沒選角度，先用「穩」的角度寫。回上一頁可以改選。' : `角度：【${esc(angle.label)}】${esc(angle.title)}`}<br>在筆電 Claude Code 開新對話，整份貼上就會照順序跑：研究 → 查證 → 腳本（停下來給你看）→ 配音 → 剪輯 → 縮圖與上架文案。</p>
  <div class="btn-row"><button type="button" class="btn primary" data-copy-brief>${ICON.copy}複製企劃包</button><button type="button" class="btn" data-dl-brief>${ICON.dl}下載 .md</button></div>
  ${navigator.share ? `<div class="btn-row"><button type="button" class="btn ghost" data-share-brief>${ICON.share}分享到其他 App</button><button type="button" class="btn ghost" data-star-making>${ICON.star}標成製作中</button></div>` : `<div class="btn-row"><button type="button" class="btn ghost" data-star-making>${ICON.star}標成製作中</button></div>`}
  <pre class="brief" id="brief">${esc(md_)}</pre>`;
  openSheet(html);
  S.briefText = md_;
  S.briefName = fname;
  S.open = id;
}
async function openClaude(id) {
  const t = S.byId[id];
  const si = selectedAngle(t);
  const text = makePrompt(t, si != null ? t.angles[si] : null, topicCtx(t));
  const ok = await copy(text);
  toast(ok ? '已複製討論內容，到 Claude 貼上就好' : '複製失敗，請長按企劃包手動複製');
  window.open('https://claude.ai/new', '_blank', 'noopener');
}
function setPlan(id, status) {
  const t = S.byId[id];
  const plan = { ...(S.prefs.plan || {}) };
  if (!status) { delete plan[id]; markDel('plan', id); } else {
    unDel('plan', id);
    const old = plan[id] || {};
    plan[id] = { ...old, status, title: old.title || t.title, pillar: t ? t.pillar : old.pillar, type: t ? t.type : old.type,
      score: old.score ?? (t ? Math.round(t._score) : null), at: old.at || new Date().toISOString(), angle: t ? selectedAngle(t) : old.angle };
    if (status === 'live' && !plan[id].date) plan[id].date = todayISO();
  }
  S.prefs.plan = plan;
  writePrefs();
}

// ------------------------------------------------------------------ 掃描（遙控鑰匙）
const GH = 'https://api.github.com';
async function gh(path, opt = {}) {
  const key = getKey();
  const r = await fetch(`${GH}${path}`, {
    ...opt,
    headers: { Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', ...(key ? { Authorization: `Bearer ${key}` } : {}), ...(opt.headers || {}) },
  });
  return r;
}
async function refreshData(force = true) {
  const opts = { cache: force ? 'no-store' : 'no-cache' };
  const q = force ? `?t=${Date.now()}` : '';
  const [radar, cal, meta] = await Promise.all(['radar', 'calendar', 'meta'].map((n) => fetch(`data/${n}.json${q}`, opts).then((r) => {
    if (!r.ok) throw new Error(`讀不到 ${n}.json（${r.status}）`);
    return r.json();
  })));
  const changed = !S.radar || radar.generated !== S.radar.generated;
  S.radar = radar; S.cal = cal; S.meta = meta;
  return changed;
}
async function onScan() {
  if (S.scanning) return;
  const key = getKey();
  if (!key) {
    const btn = $('#scan');
    btn.classList.add('busy');
    let changed = false;
    try { changed = await refreshData(true); } catch (e) { toast(e.message); }
    btn.classList.remove('busy');
    if (changed) { renderAll(); toast(`已更新到 ${hm(S.radar.generated)} 的觀測`); return; }
    openSheet(`
      <div class="sh-top"><button type="button" class="ibtn" data-close aria-label="關閉">${ICON.close}</button><span class="sp"></span></div>
      <h2 class="sh-title" id="sh-title">已經是最新的觀測</h2>
      <p class="lede">最新資料是 ${hm(S.radar.generated)} 掃描的。雷達每小時自動掃描一次（大約每小時的 47 分），不用做任何事。</p>
      <p class="lede">想現在就重新掃描，有兩個方法：</p>
      <ol class="steps"><li>設定「遙控鑰匙」：一次設定，之後按「掃描」就會直接叫 GitHub 重抓，約 3 分鐘後自動更新。</li>
      <li>在 GitHub 手動執行：打開執行頁，按「Run workflow」再按綠色的「Run workflow」。</li></ol>
      <div class="btn-row"><button type="button" class="btn primary" data-go-key>設定遙控鑰匙</button><a class="btn" href="https://github.com/${esc(S.meta.repo)}/actions/workflows/echo.yml" target="_blank" rel="noopener">開啟 GitHub</a></div>`);
    return;
  }
  S.scanning = true;
  $('#scan').classList.add('busy');
  const steps = [['ask', '叫 GitHub 開始掃描'], ['run', '抓資料、評分、產生企劃包'], ['pub', '發布到 App'], ['load', '讀取新的觀測']];
  const paint = (cur, msg) => {
    const box = $('#scanbox');
    if (!box) return;
    const idx = steps.findIndex((s) => s[0] === cur);
    box.innerHTML = steps.map(([k, l], i) => `<div class="scanstep ${i < idx || cur === 'done' ? 'done' : i === idx ? 'on' : ''}"><i></i><span>${l}</span></div>`).join('')
      + (msg ? `<p class="lede" style="margin-top:8px">${esc(msg)}</p>` : '');
  };
  openSheet(`<div class="sh-top"><button type="button" class="ibtn" data-close aria-label="關閉">${ICON.close}</button><span class="sp"></span></div>
    <h2 class="sh-title" id="sh-title">雷達掃描中</h2><p class="lede">大約 3 分鐘。可以關掉這個視窗繼續看，掃完會自動更新。</p><div class="scanbox" id="scanbox"></div>`);
  paint('ask');
  const started = Date.now();
  try {
    const r = await gh(`/repos/${S.meta.repo}/actions/workflows/echo.yml/dispatches`, { method: 'POST', body: JSON.stringify({ ref: 'main', inputs: { mode: 'build' } }) });
    if (r.status === 401 || r.status === 403) throw new Error('鑰匙沒有權限或已過期，到「方向」重新設定遙控鑰匙。');
    if (r.status !== 204) throw new Error(`GitHub 回應 ${r.status}，請稍後再試。`);
    paint('run');
    let run = null;
    for (let i = 0; i < 70; i++) {
      await new Promise((ok) => setTimeout(ok, i < 3 ? 4000 : 8000));
      const rr = await gh(`/repos/${S.meta.repo}/actions/runs?event=workflow_dispatch&per_page=5`);
      if (!rr.ok) continue;
      const js = await rr.json();
      run = (js.workflow_runs || []).find((x) => new Date(x.created_at).getTime() >= started - 60000) || run;
      if (run && run.status === 'completed') break;
    }
    if (!run) throw new Error('GitHub 還沒開始跑，等一下再按一次掃描。');
    if (run.conclusion !== 'success') throw new Error(`這輪掃描沒有成功（${run.conclusion || run.status}），可以到 GitHub 執行紀錄看原因。`);
    paint('pub');
    const before = S.radar.generated;
    for (let i = 0; i < 24; i++) {
      await new Promise((ok) => setTimeout(ok, 8000));
      try { await refreshData(true); } catch (e) { continue; }
      if (S.radar.generated !== before) break;
    }
    paint('done', S.radar.generated !== before ? `完成：已更新到 ${hm(S.radar.generated)} 的觀測。` : '掃描完成，但網站還在更新，等一兩分鐘再按一次「掃描」。');
    renderAll();
  } catch (e) {
    paint('ask', e.message);
    toast(e.message);
  } finally {
    S.scanning = false;
    $('#scan').classList.remove('busy');
  }
}

// ------------------------------------------------------------------ 方向同步（user 分支的 prefs.json）
const Remote = {
  t: null,
  schedule() {
    if (!getKey()) return;
    clearTimeout(this.t);
    this.t = setTimeout(() => this.push(), 2500);
  },
  b64(s) { const b = new TextEncoder().encode(s); let x = ''; b.forEach((c) => { x += String.fromCharCode(c); }); return btoa(x); },
  unb64(s) { const bin = atob(s.replace(/\n/g, '')); const b = Uint8Array.from(bin, (c) => c.charCodeAt(0)); return new TextDecoder().decode(b); },
  async pull() {
    try {
      const r = await gh(`/repos/${S.meta.repo}/contents/prefs.json?ref=user`);
      if (!r.ok) return null;
      const js = await r.json();
      this.sha = js.sha;
      return JSON.parse(this.unb64(js.content || '') || '{}');
    } catch (e) { return null; }
  },
  // 套用合併後的設定（不改 updated，免得兩台裝置互相覺得對方比較舊）；有變就重排
  adopt(merged) {
    if (!merged || JSON.stringify(merged) === JSON.stringify(S.prefs)) return false;
    S.prefs = merged;
    try { localStorage.setItem(LS, JSON.stringify(S.prefs)); } catch (e) { /* ignore */ }
    if (S.radar && S.meta) {
      evaluate();
      if ($('#sheet').hidden && S.view !== 'set') render(S.view);
    }
    return true;
  },
  async push(retry = true) {
    if (!getKey()) return;
    try {
      // 上傳前先合併另一台裝置的變更，不是誰最後存誰贏
      const remote = await this.pull();
      if (remote) this.adopt(mergePrefs(S.prefs, remote));
      const body = { message: '更新回波方向設定', content: this.b64(JSON.stringify(S.prefs, null, 1)), branch: 'user', ...(this.sha ? { sha: this.sha } : {}) };
      const r = await gh(`/repos/${S.meta.repo}/contents/prefs.json`, { method: 'PUT', body: JSON.stringify(body) });
      if (r.ok) { const js = await r.json(); this.sha = js.content && js.content.sha; } else if ((r.status === 409 || r.status === 422) && retry) await this.push(false);
    } catch (e) { /* 下次再同步 */ }
  },
  async boot() {
    if (getKey()) {
      const remote = await this.pull();
      if (!remote) return false;
      const merged = mergePrefs(S.prefs, remote);
      const changed = this.adopt(merged);
      // 這台有還沒上傳的變更，就順便同步上去
      if (JSON.stringify(merged) !== JSON.stringify(remote)) this.schedule();
      return changed;
    }
    if (!S.prefs.updated) {
      try {
        const r = await fetch(`https://raw.githubusercontent.com/${S.meta.repo}/user/prefs.json?t=${Date.now()}`, { cache: 'no-store' });
        if (r.ok) {
          const remote = await r.json();
          if (remote && remote.updated) { S.prefs = remote; try { localStorage.setItem(LS, JSON.stringify(S.prefs)); } catch (e) { /* ignore */ } return true; }
        }
      } catch (e) { /* 沒有同步過 */ }
    }
    return false;
  },
};

// ------------------------------------------------------------------ 事件
function go(view) {
  S.view = view;
  document.querySelectorAll('.view').forEach((v) => { v.hidden = v.dataset.view !== view; });
  document.querySelectorAll('.tabs button').forEach((b) => b.setAttribute('aria-current', b.dataset.go === view ? 'page' : 'false'));
  render(view);
  window.scrollTo(0, 0);
}
function render(view) {
  if (view === 'echo') renderEcho();
  else if (view === 'list') renderList();
  else if (view === 'cal') renderCal();
  else if (view === 'plan') renderPlan();
  else if (view === 'set') renderSet();
}
function renderAll() {
  evaluate();
  render(S.view);
  if (S.view !== 'echo') renderObs();
}
function rerank() {
  evaluate();
  if (S.view === 'set') renderPreview();
}

document.addEventListener('click', async (e) => {
  const el = e.target.closest('button, a');
  if (!el) {
    if (e.target === $('#sheet')) closeSheet();
    return;
  }
  const d = el.dataset;
  if (d.go) { if (!$('#sheet').hidden) closeSheet(); go(d.go); return; }
  if (d.topic) { openTopic(d.topic); return; }
  if (d.href) { if (safeUrl(d.href)) window.open(safeUrl(d.href), '_blank', 'noopener'); return; }
  if (d.filter) { S.filter = d.filter; renderList(); return; }
  if ('urgent' in d) { S.filter = 'all'; S.sort = 'due'; go('list'); const so = $('#sort'); if (so) so.value = 'due'; return; }
  if ('close' in d) { closeSheet(); return; }
  if ('updates' in d) { showUpdates(true); return; }
  if (d.back) { openTopic(d.back); return; }
  if (d.goKey !== undefined) { closeSheet(); go('set'); setTimeout(() => { const k = $('#keygroup'); if (k) k.scrollIntoView({ behavior: 'smooth' }); }, 60); return; }
  if (d.reason) {
    const ul = $(`[data-reasons="${d.reason}"]`);
    ul.hidden = !ul.hidden;
    el.setAttribute('aria-expanded', String(!ul.hidden));
    return;
  }
  if (d.angle != null && S.open) {
    S.prefs.angle = { ...(S.prefs.angle || {}), [S.open]: Number(d.angle) };
    writePrefs();
    document.querySelectorAll('.ang').forEach((b) => b.setAttribute('aria-checked', String(b.dataset.angle === d.angle)));
    if (P.plan[S.open]) { S.prefs.plan[S.open].angle = Number(d.angle); writePrefs(); }
    return;
  }
  if ('star' in d && S.open) {
    const cur = P.plan[S.open];
    setPlan(S.open, cur ? null : 'idea');
    toast(cur ? '已從企劃移除' : '已加入企劃的「想拍」');
    rerank();
    openTopic(S.open);
    return;
  }
  if ('mute' in d && S.open) {
    const t = S.byId[S.open];
    const m = { ...(S.prefs.mute || {}) };
    if (m[S.open]) { delete m[S.open]; markDel('mute', S.open); } else { m[S.open] = { title: t.title, at: new Date().toISOString() }; unDel('mute', S.open); }
    S.prefs.mute = m;
    writePrefs();
    toast(m[S.open] ? '已靜音，這題不會再出現在雷達上' : '已取消靜音');
    rerank();
    openTopic(S.open);
    return;
  }
  if ('brief' in d && S.open) { saveNote(); openBrief(S.open); return; }
  if ('claude' in d && S.open) { saveNote(); await openClaude(S.open); return; }
  if ('copyBrief' in d) { toast((await copy(S.briefText)) ? '已複製企劃包，到筆電 Claude Code 貼上' : '複製失敗，請長按文字手動複製'); return; }
  if ('dlBrief' in d) { download(S.briefName, S.briefText); return; }
  if ('shareBrief' in d) { try { await navigator.share({ title: S.briefName, text: S.briefText }); } catch (e2) { /* 取消分享 */ } return; }
  if ('starMaking' in d && S.open) { setPlan(S.open, 'making'); rerank(); toast('已標成製作中'); return; }
  // 企劃頁
  const pc = el.closest('[data-plan]');
  if (pc) {
    const id = pc.dataset.plan;
    if (d.planStatus) { setPlan(id, d.planStatus); rerank(); renderPlan(); return; }
    if ('planOpen' in d) { openTopic(id); return; }
    if ('planDel' in d) { setPlan(id, null); rerank(); renderPlan(); return; }
  }
  const vc = el.closest('[data-video]');
  if (vc && d.videoStatus) {
    const id = vc.dataset.video;
    S.prefs.videos = { ...(S.prefs.videos || {}), [id]: { ...((S.prefs.videos || {})[id] || {}), status: d.videoStatus === 'live' ? 'done' : 'making' } };
    writePrefs();
    rerank();
    renderPlan();
    return;
  }
  // 方向頁
  if (el.id === 'watch-add') { addWatch(); return; }
  if (d.watchDel != null) {
    const w = [...P.watch];
    const [gone] = w.splice(Number(d.watchDel), 1);
    if (gone) markDel('watch', gone);
    S.prefs.watch = w; writePrefs(); rerank(); renderSet(); return;
  }
  if (d.unmute) { const m = { ...P.mute }; delete m[d.unmute]; markDel('mute', d.unmute); S.prefs.mute = m; writePrefs(); rerank(); renderSet(); return; }
  if (el.id === 'key-save') {
    const v = ($('#key-in').value || '').trim();
    if (!v) { toast('先貼上鑰匙'); return; }
    setKey(v);
    const ok = await testKey();
    if (!ok) { setKey(''); return; }
    // 先把另一台裝置同步上去的設定拿下來合併，再上傳，不會蓋掉筆電上的企劃與觀看數
    const remote = await Remote.pull();
    if (remote) Remote.adopt(mergePrefs(S.prefs, remote));
    await Remote.push();
    rerank();
    renderSet();
    return;
  }
  if (el.id === 'key-test') { await testKey(); return; }
  if (el.id === 'key-del') { setKey(''); toast('已移除這台裝置的鑰匙'); renderSet(); return; }
  if (el.id === 'exp') { toast((await copy(JSON.stringify(S.prefs))) ? '已複製設定，到另一台裝置按「貼上設定」' : '複製失敗'); return; }
  if (el.id === 'imp') {
    let txt = '';
    try { txt = await navigator.clipboard.readText(); } catch (e2) { txt = window.prompt('把設定貼在這裡') || ''; }
    try { const obj = JSON.parse(txt); if (typeof obj !== 'object') throw new Error(); S.prefs = obj; writePrefs(); rerank(); renderSet(); toast('已套用設定'); } catch (e2) { toast('貼上的內容不是回波的設定'); }
    return;
  }
  if (el.id === 'reset') { delete S.prefs.weights; delete S.prefs.twShare; delete S.prefs.lead; delete S.prefs.pillar; writePrefs(); rerank(); renderSet(); toast('已恢復預設權重'); return; }
  if (el.id === 'scan') { onScan(); }
});
function saveNote() {
  const el = $('#note');
  if (!el || !S.open) return;
  const v = el.value.trim();
  if (P.plan[S.open]) S.prefs.plan[S.open].note = v;
  else S.prefs.notes = { ...(S.prefs.notes || {}), [S.open]: v };
  writePrefs();
}
async function testKey() {
  try {
    const r = await gh(`/repos/${S.meta.repo}`);
    if (!r.ok) { toast(r.status === 401 ? '鑰匙無效或已過期' : `GitHub 回應 ${r.status}`); return false; }
    const js = await r.json();
    const can = js.permissions && js.permissions.push;
    toast(can ? '鑰匙可以用' : '鑰匙讀得到 repo，但沒有寫入權限，請確認 Actions 與 Contents 都是 Read and write');
    return !!can;
  } catch (e) { toast('連不到 GitHub，請檢查網路'); return false; }
}
function addWatch() {
  const el = $('#watch-in');
  const v = (el.value || '').trim();
  if (!v) return;
  if (!P.watch.includes(v)) S.prefs.watch = [...P.watch, v];
  S.prefs.watchAt = { ...(S.prefs.watchAt || {}), [v]: new Date().toISOString() };
  unDel('watch', v);
  writePrefs();
  rerank();
  renderSet();
}
document.addEventListener('input', (e) => {
  const el = e.target;
  if (el.id === 'q') { S.q = el.value.trim(); renderList(); return; }
  if (el.id && el.id.startsWith('w-')) {
    S.prefs.weights = { ...P.weights, [el.id.slice(2)]: Number(el.value) };
    el.nextElementSibling.textContent = el.value;
    writePrefs(); rerank(); return;
  }
  if (el.id === 'share') { S.prefs.twShare = Number(el.value) / 100; el.nextElementSibling.textContent = `${el.value}%`; writePrefs(); rerank(); return; }
  if (el.id === 'lead') { S.prefs.lead = Number(el.value); el.nextElementSibling.textContent = `${el.value} 天`; writePrefs(); rerank(); return; }
  if (el.id && el.id.startsWith('pl-')) {
    S.prefs.pillar = { ...(S.prefs.pillar || {}), [el.id.slice(3)]: Number(el.value) };
    el.nextElementSibling.textContent = `×${Number(el.value).toFixed(1)}`;
    writePrefs(); rerank();
  }
});
document.addEventListener('change', (e) => {
  const el = e.target;
  if (el.id === 'sort') { S.sort = el.value; renderList(); return; }
  const pc = el.closest('[data-plan]');
  if (pc && (el.matches('[data-plan-views]') || el.matches('[data-plan-date]'))) {
    const id = pc.dataset.plan;
    const x = { ...(S.prefs.plan[id] || {}) };
    if (el.matches('[data-plan-views]')) x.views = Number(el.value) || 0; else x.date = el.value;
    S.prefs.plan = { ...S.prefs.plan, [id]: x };
    writePrefs(); rerank(); renderPlan();
    return;
  }
  const vc = el.closest('[data-video]');
  if (vc && el.matches('[data-video-views]')) {
    const id = vc.dataset.video;
    S.prefs.videos = { ...(S.prefs.videos || {}), [id]: { ...((S.prefs.videos || {})[id] || {}), views: Number(el.value) || 0, status: 'done' } };
    writePrefs(); rerank(); renderPlan();
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#sheet').hidden) closeSheet();
  if (e.key === 'Enter' && e.target.id === 'watch-in') addWatch();
});
document.addEventListener('focusout', (e) => { if (e.target.id === 'note') saveNote(); });
document.addEventListener('visibilitychange', async () => {
  if (document.visibilityState !== 'visible' || !S.radar) return;
  try { if (await refreshData(false)) renderAll(); else renderObs(); } catch (e) { /* 離線時照舊 */ }
});

// ------------------------------------------------------------------ 開機
async function boot() {
  try {
    await refreshData(false);
  } catch (e) {
    $('#obs').textContent = '讀不到雷達資料';
    $('#top').innerHTML = `<li class="empty">${esc(e.message)}。如果是第一次打開，等 GitHub 跑完第一輪（約 3 分鐘）再重新整理。</li>`;
    return;
  }
  if (await Remote.boot()) { /* 用同步過來的方向 */ }
  renderAll();
  showUpdates();
  if ('serviceWorker' in navigator) navigator.serviceWorker.register(`sw.js?v=${BUILD}`).catch(() => {});
}
boot();
