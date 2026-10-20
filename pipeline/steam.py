"""Steam 測站：熱門排行、願望單、即將推出、新品熱銷、同時在線、評論（含繁中玩家評價）、商店資料。

全部是 Steam 公開的商店／統計端點，不需要金鑰。每個端點各有快取與每輪上限，避免被限流。
"""
import html as htmllib
import re
import time

from bs4 import BeautifulSoup

from .util import hours_since, iso, now, parse_iso

SEARCH = ('https://store.steampowered.com/search/results/?query=&start=0&count=100'
          '&{f}&category1=998&cc=tw&l={lang}&infinite=1')
MOSTPLAYED = 'https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/'
PLAYERS = 'https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid={appid}'
DETAILS = 'https://store.steampowered.com/api/appdetails?appids={appid}&cc=tw&l={lang}'
REVIEWS = ('https://store.steampowered.com/appreviews/{appid}?json=1&language={lang}'
           '&purchase_type=all&num_per_page={n}&filter=recent')
HEADER = 'https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg'
STORE = 'https://store.steampowered.com/app/{appid}/'

LISTS = {
    'wishlist': 'filter=popularwishlist',     # 願望單最多
    'upcoming': 'filter=popularcomingsoon',   # 熱門即將推出
    'trending': 'filter=popularnew',          # 熱門新品
    'topsell': 'filter=globaltopsellers',     # 全球熱銷
    'topsell_tw': 'filter=topsellers',        # 台灣區熱銷（cc=tw）
}

DETAILS_TTL_H = 20
REVIEWS_TTL_H = 5.5


def _num(s):
    s = re.sub(r'[^\d.]', '', s or '')
    try:
        return float(s) if s else None
    except ValueError:
        return None


def parse_search(results_html: str) -> list[dict]:
    """Steam 搜尋結果（infinite=1 的 results_html）→ 每款遊戲一列。與玩真的同一套解析。"""
    soup = BeautifulSoup(results_html or '', 'lxml')
    out = []
    for a in soup.select('a.search_result_row'):
        appid = (a.get('data-ds-appid') or '').strip()
        if not appid.isdigit() or a.get('data-ds-packageid') or a.get('data-ds-bundleid'):
            continue
        title_el = a.select_one('span.title')
        if not title_el:
            continue
        rel_el = a.select_one('div.search_released')
        rv = a.select_one('span.search_review_summary')
        review = None
        if rv is not None:
            tip = htmllib.unescape(rv.get('data-tooltip-html') or '')
            desc = re.split(r'<br\s*/?>', tip)[0].strip()
            pct = re.search(r'(\d{1,3})\s*%', tip)
            cnt = re.search(r'([\d,]+)\s*篇', tip) or re.search(r'([\d,]+)\s*user reviews', tip)
            review = {'desc': desc, 'pct': int(pct.group(1)) if pct else None,
                      'n': int(cnt.group(1).replace(',', '')) if cnt else None}
        db = a.select_one('div.discount_block')
        disc = int(db.get('data-discount') or 0) if db is not None else 0
        final = None
        if db is not None and (db.get('data-price-final') or '').isdigit():
            final = int(db['data-price-final']) / 100
        out.append({
            'appid': int(appid), 'name': title_el.get_text(strip=True).replace('\xa0', ' '),
            'released': rel_el.get_text(strip=True) if rel_el else '',
            'review': review, 'discount': disc, 'final': final,
        })
    return out


def fetch_lists(http, health) -> dict:
    """四份排行（中文名）＋同四份的英文名，回傳 {清單: [{appid, rank, name, en, released, review}]}。"""
    out = {}
    for key, f in LISTS.items():
        try:
            rows = []
            for lang in ('tchinese', 'english'):
                r = http.get(SEARCH.format(f=f, lang=lang), timeout=30)
                if r.status_code != 200:
                    raise RuntimeError(f'HTTP {r.status_code}')
                rows.append(parse_search(r.json().get('results_html', '')))
                time.sleep(0.6)
            zh, en = rows
            en_by = {x['appid']: x['name'] for x in en}
            for i, x in enumerate(zh, 1):
                x['rank'] = i
                x['en'] = en_by.get(x['appid'])
            out[key] = zh
            health[f'steam_{key}'] = {'ok': True, 'n': len(zh), 'at': iso(now())}
        except Exception as e:  # noqa: BLE001 — 單一清單失敗不影響其他
            health[f'steam_{key}'] = {'ok': False, 'err': str(e)[:160], 'at': iso(now())}
    return out


def fetch_mostplayed(http, health) -> list[dict]:
    try:
        d = http.json(MOSTPLAYED, timeout=30)
        ranks = (d.get('response') or {}).get('ranks') or []
        rows = [{'appid': int(x['appid']), 'rank': int(x['rank']),
                 'last_week_rank': int(x.get('last_week_rank') or 0) or None,
                 'peak': int(x.get('peak_in_game') or 0) or None} for x in ranks if x.get('appid')]
        health['steam_mostplayed'] = {'ok': True, 'n': len(rows), 'at': iso(now())}
        return rows
    except Exception as e:  # noqa: BLE001
        health['steam_mostplayed'] = {'ok': False, 'err': str(e)[:160], 'at': iso(now())}
        return []


# ---------------------------------------------------------------- 商店資料
_LANG_TC = re.compile(r'繁體中文|Traditional Chinese', re.I)
_LANG_SC = re.compile(r'簡體中文|Simplified Chinese', re.I)


def _langs(raw: str) -> dict:
    """supported_languages（HTML）→ 繁中有沒有介面／字幕／語音。"""
    raw = raw or ''
    text = re.sub(r'<br\s*/?>.*$', '', raw, flags=re.S)  # 後面是「*具有完整語音支援的語言」說明
    out = {'tc': False, 'tc_voice': False, 'sc': bool(_LANG_SC.search(text))}
    m = _LANG_TC.search(text)
    if m:
        out['tc'] = True
        tail = text[m.end(): m.end() + 30]
        out['tc_voice'] = '<strong>*</strong>' in tail[:20]
    return out


def _gpu_hint(req_html: str) -> str | None:
    """推薦配備裡的顯示卡（用來判斷「拿 5090 筆電實測」有沒有看頭）。"""
    for li in re.findall(r'<li>(.*?)</li>', req_html or '', re.S | re.I):
        text = re.sub(r'<[^>]+>', ' ', li)
        text = re.sub(r'[®™©]', '', text)
        m = re.match(r'\s*(?:顯示卡|Graphics|Video Card)\s*[:：]\s*(.+)', text, re.I)
        if m:
            return re.sub(r'\s+', ' ', m.group(1)).strip()[:90]
    return None


def trim_details(d: dict) -> dict:
    rd = d.get('release_date') or {}
    req = d.get('pc_requirements') or {}
    if isinstance(req, list):
        req = {}
    price = d.get('price_overview') or {}
    return {
        'name': d.get('name'), 'type': d.get('type'), 'free': bool(d.get('is_free')),
        'coming': bool(rd.get('coming_soon')), 'date_text': rd.get('date') or '',
        'langs': _langs(d.get('supported_languages')),
        'plat': [k for k, v in (d.get('platforms') or {}).items() if v],
        'genres': [g.get('description') for g in (d.get('genres') or []) if g.get('description')][:5],
        'cats': [c.get('description') for c in (d.get('categories') or []) if c.get('description')][:12],
        'price': (price.get('final') or 0) / 100 if price else None,
        'price_text': price.get('final_formatted') if price else None,
        'discount': price.get('discount_percent') if price else None,
        'movies': len(d.get('movies') or []),
        'meta': (d.get('metacritic') or {}).get('score'),
        'recs': (d.get('recommendations') or {}).get('total'),
        'dev': (d.get('developers') or [])[:2], 'pub': (d.get('publishers') or [])[:2],
        'desc': (d.get('short_description') or '')[:220],
        'gpu': _gpu_hint(req.get('recommended') or req.get('minimum')),
        'img': d.get('header_image'),
        'demo': bool(d.get('demos')),
    }


def details(http, appids, cache: dict, health, budget: int = 45):
    """商店資料（繁中版）每 20 小時更新一次；每輪最多 budget 筆。"""
    t0 = now()
    todo = [a for a in appids if hours_since((cache.get(str(a)) or {}).get('at'), t0) >= DETAILS_TTL_H]
    done = errs = 0
    for a in todo[:budget]:
        try:
            r = http.get(DETAILS.format(appid=a, lang='tchinese'), timeout=25)
            if r.status_code == 429:
                errs += 1
                break
            j = (r.json() or {}).get(str(a)) or {}
            if j.get('success') and j.get('data'):
                cache[str(a)] = {'at': iso(t0), 'd': trim_details(j['data'])}
            else:
                cache[str(a)] = {'at': iso(t0), 'd': None}
            done += 1
        except Exception:  # noqa: BLE001
            errs += 1
        time.sleep(0.35)
    health['steam_details'] = {'ok': errs == 0 or done > 0, 'n': done, 'todo': max(0, len(todo) - done),
                               'at': iso(t0), **({'err': f'{errs} 次失敗'} if errs else {})}


def _summary(q: dict) -> dict | None:
    if not q:
        return None
    pos, neg = q.get('total_positive') or 0, q.get('total_negative') or 0
    tot = pos + neg
    return {'pct': round(100 * pos / tot) if tot else None, 'n': tot,
            'desc': q.get('review_score_desc')}


def _recent(rec: list) -> dict | None:
    """最近 n 篇評論 → 好評率、每天幾篇（用最舊一篇到現在的時間算，評論很少的遊戲才不會被高估）。"""
    if not rec:
        return None
    ups = sum(1 for x in rec if x.get('voted_up'))
    ts = [x.get('timestamp_created') or 0 for x in rec]
    span_d = max(0.25, (time.time() - min(ts)) / 86400)
    return {'pct': round(100 * ups / len(rec)), 'n': len(rec), 'per_day': round(len(rec) / span_d, 1),
            'span_d': round(span_d, 1)}


def reviews(http, appids, cache: dict, health, budget: int = 40):
    """全球評價、最近 100 篇的好評率（抓負評潮）、繁中玩家評價。每 5.5 小時更新。"""
    t0 = now()
    todo = [a for a in appids if hours_since((cache.get(str(a)) or {}).get('at'), t0) >= REVIEWS_TTL_H]
    done = errs = 0
    for a in todo[:budget]:
        try:
            r = http.get(REVIEWS.format(appid=a, lang='all', n=100), timeout=25)
            if r.status_code == 429:
                errs += 1
                break
            j = r.json() or {}
            allq = _summary(j.get('query_summary'))
            recent = _recent(j.get('reviews') or [])
            time.sleep(0.3)
            r2 = http.get(REVIEWS.format(appid=a, lang='tchinese', n=100), timeout=25)
            tc = tc_recent = None
            if r2.status_code == 200:
                j2 = r2.json() or {}
                tc = _summary(j2.get('query_summary'))
                tc_recent = _recent(j2.get('reviews') or [])
            cache[str(a)] = {'at': iso(t0), 'all': allq, 'recent': recent, 'tc': tc, 'tc_recent': tc_recent}
            done += 1
        except Exception:  # noqa: BLE001
            errs += 1
        time.sleep(0.3)
    health['steam_reviews'] = {'ok': errs == 0 or done > 0, 'n': done, 'todo': max(0, len(todo) - done),
                               'at': iso(t0), **({'err': f'{errs} 次失敗'} if errs else {})}


def players(http, appids, hist: dict, health, budget: int = 120, skip: dict | None = None):
    """同時在線人數，每輪記一筆（保留 14 天）。查不到人數的遊戲（還沒上市、沒有統計）記在 skip，一天內不再查。"""
    t0 = now()
    stamp = int(t0.timestamp())
    done = errs = 0
    skip = skip if skip is not None else {}
    for k in [k for k, v in skip.items() if stamp - v > 86400]:
        del skip[k]
    for a in [a for a in appids if str(a) not in skip][:budget]:
        try:
            r = http.get(PLAYERS.format(appid=a), timeout=15, retries=0)
            if r.status_code == 200:
                n = ((r.json() or {}).get('response') or {}).get('player_count')
                if n is not None:
                    hist.setdefault(str(a), []).append([stamp, int(n)])
                    done += 1
                else:
                    skip[str(a)] = stamp  # 查得到但沒有人數（沒有統計的應用程式）
            elif r.status_code == 404:
                skip[str(a)] = stamp  # 還沒上市或沒有統計
            else:
                # 429、5xx 是暫時的：記成失敗、不列入一天不查的名單；被限流就先停
                errs += 1
                if r.status_code == 429:
                    break
        except Exception:  # noqa: BLE001
            errs += 1
        time.sleep(0.08)
    cut = stamp - 14 * 86400
    for k in list(hist):
        hist[k] = [p for p in hist[k] if p[0] >= cut][-400:]
        if not hist[k]:
            del hist[k]
    health['steam_players'] = {'ok': done > 0, 'n': done, 'at': iso(t0),
                               **({'err': f'{errs} 次失敗'} if errs else {})}


def player_stats(series: list) -> dict | None:
    """在線人數序列 → 現在、24 小時前、7 天前、14 天內高點。"""
    if not series:
        return None
    s = sorted(series)
    t_now, n_now = s[-1]

    def at(hours):
        target = t_now - hours * 3600
        best = None
        for t, n in s:
            if abs(t - target) <= 3 * 3600 and (best is None or abs(t - target) < abs(best[0] - target)):
                best = (t, n)
        return best[1] if best else None

    d1, d7 = at(24), at(24 * 7)
    peak = max(n for _, n in s)
    return {'now': n_now, 'd1': d1, 'd7': d7, 'peak14': peak, 'points': len(s),
            'g1': round(n_now / d1 - 1, 3) if d1 else None,
            'g7': round(n_now / d7 - 1, 3) if d7 else None}
