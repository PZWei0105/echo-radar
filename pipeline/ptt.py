"""PTT 測站：台灣玩家在 PTT 遊戲相關看板討論什麼、推文熱不熱。

只讀看板列表頁（標題、推文數、發文時間），不抓內文。每輪讀最新幾頁，累積 7 天。
"""
import re
import time

from bs4 import BeautifulSoup

from .util import iso, now

BASE = 'https://www.ptt.cc'
BOARDS = {
    'Steam': {'label': 'Steam 板', 'pages': 2},
    'PlayStation': {'label': 'PlayStation 板', 'pages': 2},
    'NSwitch': {'label': 'NSwitch 板', 'pages': 2},
    'XBOX': {'label': 'XBOX 板', 'pages': 1},
    'C_Chat': {'label': 'C_Chat 板', 'pages': 4},
    'PC_Shopping': {'label': 'PC_Shopping 板', 'pages': 2},
}
KEEP_DAYS = 7
_M = re.compile(r'/M\.(\d{9,11})\.A\.[0-9A-F]{3}\.html')
_TAG = re.compile(r'^\s*(?:Re:\s*|Fw:\s*)?\[([^\]]{1,6})\]\s*')


def push_value(s: str) -> int:
    s = (s or '').strip()
    if s == '爆':
        return 100
    if s.startswith('X'):
        rest = s[1:]
        return -100 if rest == 'X' else -10 * int(rest) if rest.isdigit() else -10
    return int(s) if s.isdigit() else 0


def parse_index(html: str, board: str) -> tuple[list[dict], str | None]:
    soup = BeautifulSoup(html, 'lxml')
    container = soup.select_one('div.r-list-container') or soup
    posts = []
    for el in container.children:
        if getattr(el, 'get', None) is None:
            continue
        cls = el.get('class') or []
        if 'r-list-sep' in cls:
            break  # 底下是置底公告
        if 'r-ent' not in cls:
            continue
        a = el.select_one('div.title a')
        if not a:
            continue
        href = a.get('href') or ''
        m = _M.search(href)
        if not m:
            continue
        title = a.get_text(strip=True)
        tag = _TAG.match(title)
        nrec = el.select_one('div.nrec')
        posts.append({
            'id': f'{board}/{href.rsplit("/", 1)[-1][:-5]}',
            'board': board, 'url': BASE + href, 'title': title,
            'tag': tag.group(1) if tag else '', 'reply': title.lower().startswith('re:'),
            'push': push_value(nrec.get_text(strip=True) if nrec else ''),
            'ts': int(m.group(1)),
        })
    prev = None
    for btn in soup.select('div.btn-group-paging a.btn'):
        if '上頁' in btn.get_text():
            prev = btn.get('href')
    return posts, prev


def fetch(http, store: dict, health) -> int:
    """抓各看板最新幾頁，更新 store（post id → 資料）；回傳這輪看到的文章數。"""
    t0 = now()
    seen = 0
    for board, cfg in BOARDS.items():
        url = f'{BASE}/bbs/{board}/index.html'
        ok = True
        got = 0
        for _ in range(cfg['pages']):
            try:
                r = http.get(url, headers={'Cookie': 'over18=1'}, timeout=20)
                if r.status_code != 200:
                    raise RuntimeError(f'HTTP {r.status_code}')
                posts, prev = parse_index(r.text, board)
            except Exception as e:  # noqa: BLE001
                health[f'ptt_{board}'] = {'ok': False, 'err': str(e)[:120], 'at': iso(t0)}
                ok = False
                break
            for p in posts:
                old = store.get(p['id'])
                p['first'] = old.get('first') if old else iso(t0)
                p['push_max'] = max(p['push'], (old or {}).get('push_max', p['push']))
                store[p['id']] = p
            got += len(posts)
            if not prev:
                break
            url = BASE + prev
            time.sleep(0.8)
        if ok:
            health[f'ptt_{board}'] = {'ok': True, 'n': got, 'at': iso(t0)}
        seen += got
        time.sleep(0.5)
    cut = int(t0.timestamp()) - KEEP_DAYS * 86400
    for k in [k for k, v in store.items() if v.get('ts', 0) < cut]:
        del store[k]
    return seen
