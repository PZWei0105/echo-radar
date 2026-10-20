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
