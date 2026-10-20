"""Google 搜尋趨勢（台灣）與 Reddit 測站。

- Google 趨勢：官方「熱門搜尋」RSS（台灣），代表一般大眾正在搜什麼。
- Reddit：r/Games、r/pcgaming、r/gaming 每日熱門，代表國外玩家社群在吵什麼。
兩者都只存標題、數字與連結，保留 7 天。
"""
import re
import time
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from .util import iso, now, parse_iso

TRENDS = ['https://trends.google.com/trending/rss?geo=TW',
          'https://trends.google.com/trends/trendingsearches/daily/rss?geo=TW']
REDDIT = [('Games', 'https://www.reddit.com/r/Games/top.json?t=day&limit=60'),
          ('pcgaming', 'https://www.reddit.com/r/pcgaming/top.json?t=day&limit=60'),
          ('gaming', 'https://www.reddit.com/r/gaming/top.json?t=day&limit=40')]
KEEP_DAYS = 7


def _traffic(s: str) -> int:
    s = (s or '').replace(',', '').replace('+', '').strip()
    m = re.match(r'(\d+(?:\.\d+)?)\s*([KkMm萬]?)', s)
    if not m:
        return 0
    mult = {'K': 1000, 'k': 1000, 'M': 1_000_000, 'm': 1_000_000, '萬': 10000}.get(m.group(2), 1)
    return int(float(m.group(1)) * mult)


def parse_trends(xml: str) -> list[dict]:
    soup = BeautifulSoup(xml, 'xml')
    out = []
    for it in soup.find_all('item'):
        title = (it.find('title').get_text(strip=True) if it.find('title') else '').strip()
        if not title:
            continue
        tr = it.find('approx_traffic')
        pd = it.find('pubDate')
        try:
            at = iso(parsedate_to_datetime(pd.get_text(strip=True))) if pd else iso(now())
        except (TypeError, ValueError):
            at = iso(now())
        news = []
        for n in it.find_all('news_item')[:3]:
            t = n.find('news_item_title')
            u = n.find('news_item_url')
            s = n.find('news_item_source')
            if t and u:
                news.append({'t': t.get_text(strip=True), 'u': u.get_text(strip=True),
                             'src': s.get_text(strip=True) if s else ''})
        out.append({'term': title, 'traffic': _traffic(tr.get_text() if tr else ''),
                    'traffic_text': tr.get_text(strip=True) if tr else '', 'at': at, 'news': news})
    return out


def fetch_trends(http, store: dict, health):
    t0 = now()
    for url in TRENDS:
        try:
            r = http.get(url, timeout=25)
            if r.status_code != 200:
                raise RuntimeError(f'HTTP {r.status_code}')
            items = parse_trends(r.text)
            if not items:
                raise RuntimeError('沒有資料')
            for x in items:
                key = x['term'].lower()
                old = store.get(key)
                x['first'] = old.get('first') if old else iso(t0)
                x['traffic'] = max(x['traffic'], (old or {}).get('traffic', 0))
                x['last'] = iso(t0)
                store[key] = x
            health['gtrends'] = {'ok': True, 'n': len(items), 'at': iso(t0)}
            break
        except Exception as e:  # noqa: BLE001
            health['gtrends'] = {'ok': False, 'err': str(e)[:120], 'at': iso(t0)}
    cut = t0.timestamp() - KEEP_DAYS * 86400
    for k in list(store):
        t = parse_iso(store[k].get('last') or store[k].get('first'))
        if not t or t.timestamp() < cut:
            del store[k]


def fetch_reddit(http, store: dict, health):
    t0 = now()
    ok_any = False
    errs = []
    for sub, url in REDDIT:
        try:
            r = http.get(url, bot=True, timeout=25, retries=0)
            if r.status_code != 200:
                raise RuntimeError(f'HTTP {r.status_code}')
            for ch in (r.json().get('data') or {}).get('children') or []:
                d = ch.get('data') or {}
                if d.get('stickied'):
                    continue
                pid = d.get('id')
                if not pid:
                    continue
                store[pid] = {'sub': sub, 'title': d.get('title', ''), 'score': d.get('score', 0),
                              'comments': d.get('num_comments', 0), 'ts': int(d.get('created_utc') or 0),
                              'url': 'https://www.reddit.com' + (d.get('permalink') or ''),
                              'flair': d.get('link_flair_text') or ''}
            ok_any = True
        except Exception as e:  # noqa: BLE001
            errs.append(f'{sub}: {str(e)[:60]}')
        time.sleep(1.0)
    health['reddit'] = {'ok': ok_any, 'at': iso(t0), **({'err': '；'.join(errs)} if errs else {})}
    cut = int(t0.timestamp()) - KEEP_DAYS * 86400
    for k in [k for k, v in store.items() if v.get('ts', 0) < cut]:
        del store[k]


# ---------------------------------------------------------------- Reddit（RSS 版，JSON 會被擋）
REDDIT_RSS = [('Games', 'https://www.reddit.com/r/Games/top/.rss?t=day'),
              ('pcgaming', 'https://www.reddit.com/r/pcgaming/top/.rss?t=day')]


def fetch_reddit_rss(http, store: dict, health):
    """Reddit 每日熱門（RSS 依熱度排序，名次就是熱度）。兩個看板之間要隔幾秒，不然會被限流。"""
    import feedparser
    t0 = now()
    ok_any, errs = False, []
    for i, (sub, url) in enumerate(REDDIT_RSS):
        if i:
            time.sleep(4)
        try:
            r = http.get(url, bot=True, timeout=25, retries=0)
            if r.status_code != 200:
                raise RuntimeError(f'HTTP {r.status_code}')
            d = feedparser.parse(r.content)
            for rank, e in enumerate(d.entries, 1):
                pid = (e.get('id') or e.get('link') or '').rsplit('/', 1)[-1] or e.get('link')
                at = e.get('published') or e.get('updated')
                old = store.get(pid) or {}
                store[pid] = {'sub': sub, 't': e.get('title', ''), 'u': e.get('link'), 'rank': min(rank, old.get('rank', rank)),
                              'at': at, 'seen': iso(t0), 'first': old.get('first') or iso(t0)}
            ok_any = True
        except Exception as e:  # noqa: BLE001
            errs.append(f'{sub}: {str(e)[:60]}')
    health['reddit'] = {'ok': ok_any, 'at': iso(t0), **({'err': '；'.join(errs)} if errs else {})}
    cut = t0.timestamp() - KEEP_DAYS * 86400
    for k in list(store):
        tt = parse_iso(store[k].get('at')) or parse_iso(store[k].get('first'))
        if not tt or tt.timestamp() < cut:
            del store[k]


# ---------------------------------------------------------------- Google 新聞（台灣繁中版）
GNEWS = 'https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant'
GNEWS_GENERAL = ['遊戲 when:2d', 'Steam when:2d', 'PS5 OR "Switch 2" OR Xbox when:2d', '電玩 when:2d']


def _parse_gnews(content) -> list[dict]:
    import feedparser
    d = feedparser.parse(content)
    out = []
    for e in d.entries:
        src = (e.get('source') or {}).get('title') or ''
        title = e.get('title') or ''
        if src and title.endswith(' - ' + src):
            title = title[: -len(src) - 3]
        try:
            at = iso(parsedate_to_datetime(e.get('published')))
        except (TypeError, ValueError):
            at = None
        out.append({'t': title.strip(), 'u': e.get('link'), 'src': src, 'at': at})
    return out


def fetch_gnews_general(http, health) -> list[dict]:
    import urllib.parse
    t0 = now()
    items, errs = {}, []
    for q in GNEWS_GENERAL:
        try:
            r = http.get(GNEWS.format(q=urllib.parse.quote(q)), timeout=25, retries=0)
            if r.status_code != 200:
                raise RuntimeError(f'HTTP {r.status_code}')
            for it in _parse_gnews(r.content):
                items.setdefault(it['u'], it)
        except Exception as e:  # noqa: BLE001
            errs.append(f'{q}: {str(e)[:40]}')
        time.sleep(1.5)
    health['gnews'] = {'ok': bool(items), 'n': len(items), 'at': iso(t0), **({'err': '；'.join(errs)} if errs else {})}
    return list(items.values())


def fetch_gnews_entities(http, targets: list[tuple], cache: dict, health, budget: int = 10, ttl_h: float = 3):
    """targets: [(gid, [名稱…])]。每款熱門遊戲專門查一次台灣新聞（3 小時更新一次）。"""
    import urllib.parse
    from .util import hours_since
    t0 = now()
    done = 0
    for gid, names in targets:
        if done >= budget:
            break
        rec = cache.get(gid) or {}
        if hours_since(rec.get('at'), t0) < ttl_h:
            continue
        names = [n for n in names if n and len(n) >= 2][:2]
        if not names:
            continue
        q = ' OR '.join(f'"{n}"' for n in names) + ' when:3d'
        try:
            r = http.get(GNEWS.format(q=urllib.parse.quote(q)), timeout=25, retries=0)
            if r.status_code != 200:
                raise RuntimeError(f'HTTP {r.status_code}')
            cache[gid] = {'at': iso(t0), 'q': q, 'items': _parse_gnews(r.content)[:30]}
            done += 1
        except Exception:  # noqa: BLE001
            cache[gid] = {'at': iso(t0), 'q': q, 'items': rec.get('items') or [], 'err': True}
        time.sleep(1.5)
    keep = {g for g, _ in targets}
    for k in list(cache):
        if k not in keep and hours_since(cache[k].get('at'), t0) > 48:
            del cache[k]
    old = health.get('gnews') or {}
    old['entity_queries'] = done
    health['gnews'] = old
