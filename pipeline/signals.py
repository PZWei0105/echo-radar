"""把各測站的原始資料掛到每款遊戲身上，算出每個測站的「回波強度」(0–100) 與是否正在升溫。

測站一覽（App 的「測站觀測」會照這個順序顯示）：
  全球：媒體（玩真的）、Steam、英文維基、Reddit
  台灣：PTT、台灣新聞（Google 新聞）、Google 趨勢、中文維基、Steam 台灣（台灣區熱銷＋繁中評論）
"""
import math
import re

from .util import clamp, hours_since, log_scale, now, parse_iso

STATIONS = [
    {'id': 'media', 'label': '媒體報導', 'region': 'global', 'desc': '玩真的整理的新聞：幾家媒體在報、有沒有官方消息'},
    {'id': 'steam', 'label': 'Steam', 'region': 'global', 'desc': '同時在線、全球熱銷、願望單與即將推出排名'},
    {'id': 'wiki_en', 'label': '英文維基', 'region': 'global', 'desc': '英文 Wikipedia 條目每日瀏覽量（全球關注度）'},
    {'id': 'reddit', 'label': 'Reddit', 'region': 'global', 'desc': 'r/Games、r/pcgaming 當日熱門'},
    {'id': 'ptt', 'label': 'PTT', 'region': 'tw', 'desc': 'Steam、PlayStation、NSwitch、XBOX、C_Chat、PC_Shopping 板'},
    {'id': 'gnews', 'label': '台灣新聞', 'region': 'tw', 'desc': 'Google 新聞（台灣繁中版）近三天的報導數，不計巴哈 GNN'},
    {'id': 'trends', 'label': 'Google 趨勢', 'region': 'tw', 'desc': '台灣 Google 熱門搜尋'},
    {'id': 'wiki_zh', 'label': '中文維基', 'region': 'tw', 'desc': '中文 Wikipedia 條目每日瀏覽量（華語讀者關注度）'},
    {'id': 'steam_tw', 'label': 'Steam 台灣', 'region': 'tw', 'desc': 'Steam 台灣區熱銷排名與繁體中文評論的速度'},
]
GLOBAL_W = {'media': 0.55, 'steam': 0.7, 'wiki_en': 0.55, 'reddit': 0.45}
TW_W = {'ptt': 0.75, 'gnews': 0.6, 'trends': 0.85, 'wiki_zh': 0.5, 'steam_tw': 0.7}
EXCLUDE_NEWS_SRC = re.compile(r'GNN|巴哈|gamer\.com\.tw|Microsoft|PlayStation Store|Nintendo eShop', re.I)


def soft_or(levels: dict, weights: dict) -> float:
    p = 1.0
    for k, w in weights.items():
        v = levels.get(k) or 0
        p *= 1 - w * v / 100
    return 100 * (1 - p)


def _recency_w(age_h: float) -> float:
    if age_h <= 24:
        return 1.0
    if age_h <= 48:
        return 0.7
    if age_h <= 72:
        return 0.45
    if age_h <= 96:
        return 0.25
    return 0.0


# ------------------------------------------------------------------ 媒體
def story_texts(s: dict) -> list[str]:
    out = [s.get('t') or '', s.get('tz') or '']
    for m in s.get('m') or []:
        out += [m.get('t') or '', m.get('tz') or '']
    return [x for x in out if x]


def attach_news(reg, stories: list[dict], src_table: dict, t0):
    """每則新聞（玩真的已合併同一件事）→ 認出遊戲 → 累積到遊戲的 media 訊號。回傳 story id → gids。"""
    links = {}
    for s in stories:
        age = hours_since(s.get('up') or s.get('at'), t0)
        if age > 120:
            continue
        gids = []
        for tx in story_texts(s)[:6]:
            for g in reg.match(tx):
                if g not in gids:
                    gids.append(g)
        if not gids and s.get('sz'):
            gids = reg.match(s['sz'])[:1]
        links[s['id']] = gids
        if not gids:
            continue
        n = max(1, int(s.get('n') or 1))
        official = 'official' in (s.get('flags') or [])
        w = n * (1.3 if official else 1.0) * _recency_w(age)
        if 'rumor' in (s.get('soft') or []):
            w *= 0.6
        for gid in gids[:3]:
            g = reg.games[gid]
            m = g.sig.setdefault('media', {'cov': 0.0, 'stories': [], 'n24': 0, 'n72': 0, 'official': False})
            # 一則新聞同時提到很多款遊戲時，主角拿大部分分數
            share = 1.0 if gid == gids[0] else 0.4
            m['cov'] += w * share
            m['official'] = m['official'] or official
            if age <= 24:
                m['n24'] += 1
            if age <= 72:
                m['n72'] += 1
            m['stories'].append({'id': s['id'], 't': s.get('tz') or s.get('t'), 'u': s.get('u'), 'src': s.get('src'),
                                  'srcname': (src_table.get(s.get('src')) or {}).get('name', s.get('src')),
                                  'tier': (src_table.get(s.get('src')) or {}).get('tier', 3),
                                  'n': n, 'official': official, 'at': s.get('at'), 'soft': s.get('soft') or [],
                                  'types': s.get('types') or [], 'main': gid == gids[0]})
    return links


# ------------------------------------------------------------------ PTT
def _ptt_clean(title: str) -> str:
    return re.sub(r'^\s*(?:Re:\s*|Fw:\s*)*\[[^\]]{1,6}\]\s*', '', title or '')


def attach_ptt(reg, posts: dict, t0):
    nowts = t0.timestamp()
    for pid, p in posts.items():
        age_h = (nowts - p.get('ts', 0)) / 3600
        if age_h > 144:
            continue
        gids = reg.match(_ptt_clean(p.get('title')))
        for gid in gids[:2]:
            g = reg.games[gid]
            s = g.sig.setdefault('ptt', {'n72': 0, 'prev72': 0, 'push_max': 0, 'push_sum': 0, 'posts': []})
            if age_h <= 72:
                s['n72'] += 1
                pm = p.get('push_max', p.get('push', 0))
                s['push_max'] = max(s['push_max'], pm)
                s['push_sum'] += max(0, pm)
                s['posts'].append({'t': p.get('title'), 'u': p.get('url'), 'push': pm, 'board': p.get('board'),
                                   'ts': p.get('ts')})
            else:
                s['prev72'] += 1


def ptt_level(s: dict | None) -> float:
    if not s or not s.get('n72'):
        return 0.0
    pm = s.get('push_max', 0)
    bonus = 50 if pm >= 100 else 35 if pm >= 60 else 22 if pm >= 30 else 10 if pm >= 10 else 0
    return clamp(14 * min(s['n72'], 5) + bonus)


# ------------------------------------------------------------------ 台灣新聞（Google 新聞）
def attach_gnews(reg, items: list[dict], per_entity: dict, t0):
    """items：一般查詢（遊戲、Steam、主機）抓到的台灣新聞；per_entity：針對熱門遊戲的專門查詢結果。"""
    seen = {}
    for it in items:
        if EXCLUDE_NEWS_SRC.search(it.get('src') or ''):
            continue
        if hours_since(it.get('at'), t0) > 72:
            continue
        for gid in reg.match(it.get('t'))[:2]:
            seen.setdefault(gid, {})[it.get('u')] = it
    for gid, rec in per_entity.items():
        if gid not in reg.games:
            continue
        for it in rec.get('items') or []:
            if EXCLUDE_NEWS_SRC.search(it.get('src') or ''):
                continue
            if hours_since(it.get('at'), t0) > 72:
                continue
            seen.setdefault(gid, {})[it.get('u')] = it
    for gid, d in seen.items():
        arts = sorted(d.values(), key=lambda x: x.get('at') or '', reverse=True)
        reg.games[gid].sig['gnews'] = {'n': len(arts), 'items': arts[:6]}


def gnews_level(s: dict | None) -> float:
    if not s or not s.get('n'):
        return 0.0
    return clamp(18 * math.sqrt(s['n']))


# ------------------------------------------------------------------ Google 趨勢
def attach_trends(reg, store: dict, t0):
    for k, x in store.items():
        if hours_since(x.get('first'), t0) > 72:
            continue
        texts = [x.get('term', '')] + [n.get('t', '') for n in (x.get('news') or [])]
        gids = []
        for tx in texts:
            for g in reg.match(tx, strict_latin=True, min_cjk=3):
                if g not in gids:
                    gids.append(g)
        for gid in gids[:1]:
            s = reg.games[gid].sig.setdefault('trends', {'hits': []})
            s['hits'].append({'term': x.get('term'), 'traffic': x.get('traffic'), 'traffic_text': x.get('traffic_text'),
                              'at': x.get('first')})


def trends_level(s: dict | None) -> float:
    if not s or not s.get('hits'):
        return 0.0
    tr = max(h.get('traffic') or 0 for h in s['hits'])
    return clamp(70 + log_scale(tr, 500, 200000) * 0.3)


# ------------------------------------------------------------------ Reddit
def attach_reddit(reg, entries: list[dict], t0):
    for e in entries:
        if hours_since(e.get('at'), t0) > 48:
            continue
        for gid in reg.match(e.get('t'))[:2]:
            s = reg.games[gid].sig.setdefault('reddit', {'best': 99, 'n': 0, 'posts': []})
            s['best'] = min(s['best'], e.get('rank', 99))
            s['n'] += 1
            s['posts'].append({'t': e.get('t'), 'u': e.get('u'), 'sub': e.get('sub'), 'rank': e.get('rank')})


def reddit_level(s: dict | None) -> float:
    if not s or not s.get('n'):
        return 0.0
    return clamp(100 - 3.2 * (s['best'] - 1) + 6 * (s['n'] - 1))


# ------------------------------------------------------------------ 每款遊戲彙總
def media_level(s: dict | None) -> float:
    if not s or s.get('cov', 0) <= 0:
        return 0.0
    return clamp(22 * math.sqrt(s['cov']))


def steam_level(g) -> tuple[float, list[str]]:
    """回傳 (強度, 說明)。已上市看在線人數與熱銷；未上市看願望單與即將推出排名。"""
    notes = []
    lv = 0.0
    p = g.players or {}
    if p.get('now'):
        lv = max(lv, log_scale(p['now'], 300, 300000))
    r = g.ranks
    if r.get('topsell'):
        lv = max(lv, 100 - 0.6 * (r['topsell'] - 1))
    if r.get('mostplayed'):
        lv = max(lv, 100 - 0.5 * (r['mostplayed'] - 1))
    if r.get('wishlist'):
        lv = max(lv, 100 - 0.6 * (r['wishlist'] - 1))
    if r.get('upcoming'):
        lv = max(lv, 92 - 0.6 * (r['upcoming'] - 1))
    if r.get('trending'):
        lv = max(lv, 88 - 0.6 * (r['trending'] - 1))
    return clamp(lv), notes


def steam_tw_level(g) -> float:
    lv = 0.0
    r = g.ranks
    if r.get('topsell_tw'):
        lv = max(lv, 100 - 0.7 * (r['topsell_tw'] - 1))
    rv = (g.reviews or {}).get('tc_recent') or {}
    if rv.get('per_day'):
        lv = max(lv, log_scale(rv['per_day'], 0.5, 60))
    return clamp(lv)


def wiki_level(st: dict | None, lo: float, hi: float) -> float:
    if not st:
        return 0.0
    return log_scale(st.get('avg7') or 0, lo, hi)


def rising_flags(g, hist_ranks: dict, t0) -> dict:
    """各測站是否正在升溫（給「幾個測站同步升溫」用）。"""
    out = {}
    s = g.sig
    m = s.get('media') or {}
    out['media'] = m.get('n24', 0) >= 2 or (m.get('n24', 0) >= 1 and m.get('official'))
    p = g.players or {}
    rk_up = False
    if g.appid:
        for lst in ('wishlist', 'topsell', 'trending', 'topsell_tw'):
            series = (hist_ranks.get(lst) or {}).get(str(g.appid)) or []
            cur = g.ranks.get(lst)
            if cur and series:
                old = [r for ts, r in series if t0.timestamp() - ts >= 60 * 3600]
                if old and old[-1] - cur >= 10:
                    rk_up = True
    out['steam'] = bool((p.get('g1') or 0) >= 0.25 or (p.get('g7') or 0) >= 0.4 or rk_up)
    for k in ('wiki_en', 'wiki_zh'):
        st = s.get(k) or {}
        out[k] = bool((st.get('g7') or 0) >= 0.3 or (st.get('spike') or 0) >= 2.0)
    pt = s.get('ptt') or {}
    out['ptt'] = pt.get('n72', 0) >= 2 and pt.get('n72', 0) >= 2 * max(1, pt.get('prev72', 0))
    out['gnews'] = (s.get('gnews') or {}).get('n', 0) >= 4
    out['trends'] = bool((s.get('trends') or {}).get('hits'))
    out['reddit'] = (s.get('reddit') or {}).get('best', 99) <= 10
    tc = (g.reviews or {}).get('tc_recent') or {}
    out['steam_tw'] = bool((g.ranks.get('topsell_tw') or 999) <= 15 or (tc.get('per_day') or 0) >= 15)
    return out


def summarize(g, hist_ranks: dict, taiwan_share: float, t0) -> dict:
    s = g.sig
    lv = {
        'media': media_level(s.get('media')),
        'steam': steam_level(g)[0],
        'wiki_en': wiki_level(s.get('wiki_en'), 300, 60000),
        'reddit': reddit_level(s.get('reddit')),
        'ptt': ptt_level(s.get('ptt')),
        'gnews': gnews_level(s.get('gnews')),
        'trends': trends_level(s.get('trends')),
        'wiki_zh': wiki_level(s.get('wiki_zh'), 30, 5000),
        'steam_tw': steam_tw_level(g),
    }
    glob = soft_or(lv, GLOBAL_W)
    tw = soft_or(lv, TW_W)
    rising = rising_flags(g, hist_ranks, t0)
    n_rise = sum(1 for k, v in rising.items() if v and lv.get(k, 0) > 0)
    demand = (1 - taiwan_share) * glob + taiwan_share * tw + min(12, 4 * n_rise)
    return {'levels': {k: round(v, 1) for k, v in lv.items()}, 'global': round(glob, 1), 'tw': round(tw, 1),
            'rising': [k for k, v in rising.items() if v and lv.get(k, 0) > 0], 'demand': round(clamp(demand), 1)}


def fmt_num(n) -> str:
    if n is None:
        return '—'
    n = float(n)
    if n >= 1e8:
        return f'{n / 1e8:.1f} 億'.replace('.0 ', ' ')
    if n >= 1e4:
        return f'{n / 1e4:.1f} 萬'.replace('.0 ', ' ')
    return f'{int(round(n)):,}'


def pct(g) -> str:
    return f'{round(g * 100):+d}%'


def station_cards(g, summ: dict) -> list[dict]:
    """App 的「測站觀測」：每個有讀數的測站一張小卡（文字＋連結）。"""
    s = g.sig
    lv = summ['levels']
    rising = set(summ['rising'])
    cards = []

    def add(sid, text, url=None, extra=None):
        if lv.get(sid, 0) <= 0 and sid not in ('steam_tw',):
            return
        cards.append({'id': sid, 'level': lv.get(sid, 0), 'up': sid in rising, 'text': text, 'url': url,
                      **(extra or {})})

    m = s.get('media')
    if m:
        srcs = {x['srcname'] for x in m['stories']}
        best = max(m['stories'], key=lambda x: (x['n'], x['official']))
        add('media', f"{len(m['stories'])} 則新聞、最多 {best['n']} 家報同一件事" + ('，含官方消息' if m.get('official') else ''),
            best.get('u'), {'srcs': sorted(srcs)[:6]})
    p = g.players or {}
    bits = []
    if p.get('now'):
        t = f"同時在線 {fmt_num(p['now'])}"
        if p.get('g1') is not None and abs(p['g1']) >= 0.05:
            t += f"（24 小時 {pct(p['g1'])}）"
        elif p.get('g7') is not None and abs(p['g7']) >= 0.05:
            t += f"（7 天 {pct(p['g7'])}）"
        bits.append(t)
    for lst, lab in (('topsell', '全球熱銷'), ('mostplayed', '最多人玩'), ('wishlist', '願望單'),
                     ('upcoming', '熱門即將推出'), ('trending', '熱門新品')):
        if g.ranks.get(lst):
            bits.append(f'{lab}第 {g.ranks[lst]}')
    if bits:
        add('steam', '、'.join(bits[:3]), g.store_url)
    for k, lab in (('wiki_en', '英文維基'), ('wiki_zh', '中文維基')):
        st = s.get(k)
        if st:
            t = f"每天約 {fmt_num(st['avg7'])} 人次"
            if st.get('g7') is not None and abs(st['g7']) >= 0.1:
                t += f"（週增減 {pct(st['g7'])}）"
            add(k, t, st.get('url'))
    r = s.get('reddit')
    if r:
        best = min(r['posts'], key=lambda x: x.get('rank') or 99)
        add('reddit', f"r/{best['sub']} 當日熱門第 {best['rank']}：{best['t'][:40]}", best.get('u'))
    pt = s.get('ptt')
    if pt and pt.get('n72'):
        top = max(pt['posts'], key=lambda x: x.get('push', 0))
        push = '爆' if top['push'] >= 100 else f"{top['push']} 推"
        add('ptt', f"3 天 {pt['n72']} 篇，最熱：{top['t'][:30]}（{push}）", top.get('u'))
    gn = s.get('gnews')
    if gn and gn.get('n'):
        add('gnews', f"台灣 3 天 {gn['n']} 篇報導：{gn['items'][0]['t'][:30]}", gn['items'][0].get('u'))
    tr = s.get('trends')
    if tr and tr.get('hits'):
        h = max(tr['hits'], key=lambda x: x.get('traffic') or 0)
        add('trends', f"台灣熱門搜尋「{h['term']}」{h.get('traffic_text') or ''}",
            'https://trends.google.com/trending?geo=TW')
    tw_bits = []
    if g.ranks.get('topsell_tw'):
        tw_bits.append(f"台灣區熱銷第 {g.ranks['topsell_tw']}")
    tc = (g.reviews or {}).get('tc') or {}
    tcr = (g.reviews or {}).get('tc_recent') or {}
    if tc.get('n'):
        tw_bits.append(f"繁中評論 {fmt_num(tc['n'])} 篇、好評 {tc.get('pct')}%")
    if tcr.get('per_day') and tcr['per_day'] >= 1:
        tw_bits.append(f"最近每天約 {tcr['per_day']:g} 篇")
    if tw_bits and lv.get('steam_tw', 0) > 0:
        add('steam_tw', '、'.join(tw_bits), g.store_url)
    order = [x['id'] for x in STATIONS]
    cards.sort(key=lambda c: order.index(c['id']))
    return cards
