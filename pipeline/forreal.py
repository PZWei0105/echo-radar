"""媒體測站：直接讀《玩真的》每小時整理好的新聞（已濾掉業配、攻略農場、課金手遊宣傳，並翻成繁中）。

玩真的的網站讀不到時，改讀它 repo 的 state 分支（內容相同，只是晚幾分鐘）。
"""
from .util import iso, now

PAGES = 'https://pzwei0105.github.io/forreal/data/{name}.json'
RAW = 'https://raw.githubusercontent.com/PZWei0105/forreal/state/{path}'
SRC_INFO = 'https://pzwei0105.github.io/forreal/data/meta.json'


def _get(http, name, path):
    for url in (PAGES.format(name=name), RAW.format(path=path)):
        try:
            r = http.get(url, timeout=30, headers={'Cache-Control': 'no-cache'})
            if r.status_code == 200:
                return r.json(), url
        except Exception:  # noqa: BLE001
            continue
    return None, None


def fetch(http, health) -> dict:
    t0 = now()
    out = {}
    for name, path in (('news', 'out/news.json'), ('tech', 'out/tech.json'),
                       ('releases', 'out/releases.json'), ('meta', 'out/meta.json')):
        data, url = _get(http, name, path)
        out[name] = data
        if name in ('news', 'tech'):
            ok = bool(data and data.get('items'))
            health[f'forreal_{name}'] = {'ok': ok, 'n': len((data or {}).get('items') or []),
                                         'at': iso(t0), 'via': 'pages' if url and 'github.io' in url else 'state',
                                         'generated': (data or {}).get('generated')}
    try:
        r = http.get(RAW.format(path='names.json'), timeout=30)
        names = r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        names = None
    out['names'] = names or {}
    return out


def source_table(meta: dict | None) -> dict:
    """玩真的的來源表：id → {name, tier}。tier 1 = 官方，2 = 可信媒體，3 = 一般。"""
    return {k: {'name': v.get('name', k), 'tier': v.get('tier', 3), 'home': v.get('home')}
            for k, v in ((meta or {}).get('sources') or {}).items()}
