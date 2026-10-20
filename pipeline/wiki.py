"""維基測站：Wikipedia 英文版與中文版每日瀏覽量（Wikimedia 官方統計 API，不需要金鑰）。

遊戲 → 條目的對應：Steam 遊戲用 Wikidata 的「Steam 應用程式 ID」(P1733) 查；
沒有 Steam 的遊戲（主機獨占）用玩真的發售表附的英文條目，再用 Wikidata 找中文條目。
"""
import datetime as dt
import json
import time
import urllib.parse

from .util import iso, now, hours_since

SPARQL = 'https://query.wikidata.org/sparql?format=json&query={q}'
WBGET = ('https://www.wikidata.org/w/api.php?action=wbgetentities&sites=enwiki&titles={titles}'
         '&props=sitelinks&sitefilter=enwiki|zhwiki&format=json')
PV = ('https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{proj}/all-access/user/'
      '{title}/daily/{start}/{end}')
MAP_TTL_H = 24 * 7


def _q(appids):
    vals = ' '.join(f'"{a}"' for a in appids)
    return ('SELECT ?appid ?en ?zh WHERE { VALUES ?appid { ' + vals + ' } ?item wdt:P1733 ?appid . '
            'OPTIONAL { ?en schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . } '
            'OPTIONAL { ?zh schema:about ?item ; schema:isPartOf <https://zh.wikipedia.org/> . } }')


def _title_from_url(u: str) -> str | None:
    if not u:
        return None
    t = u.rsplit('/wiki/', 1)[-1] if '/wiki/' in u else None
    return urllib.parse.unquote(t).replace(' ', '_') if t else None


def map_appids(http, appids, wmap: dict, health, budget: int = 150):
    """Steam appid → {en, zh} 條目名稱（一週更新一次）。"""
    t0 = now()
    todo = [a for a in appids if hours_since((wmap.get(f'app:{a}') or {}).get('at'), t0) >= MAP_TTL_H]
    todo = todo[:budget]
    got = 0
    try:
        for i in range(0, len(todo), 60):
            chunk = todo[i:i + 60]
            d = http.json(SPARQL.format(q=urllib.parse.quote(_q(chunk))), bot=True, timeout=60,
                          headers={'Accept': 'application/sparql-results+json'})
            found = {}
            for b in (d.get('results') or {}).get('bindings') or []:
                a = b['appid']['value']
                e = found.setdefault(a, {})
                if b.get('en'):
                    e['en'] = _title_from_url(b['en']['value'])
                if b.get('zh'):
                    e['zh'] = _title_from_url(b['zh']['value'])
            for a in chunk:
                e = found.get(str(a)) or {}
                wmap[f'app:{a}'] = {'at': iso(t0), 'en': e.get('en'), 'zh': e.get('zh')}
                got += 1 if e else 0
            time.sleep(1.0)
        health['wikidata'] = {'ok': True, 'n': got, 'checked': len(todo), 'at': iso(t0)}
    except Exception as e:  # noqa: BLE001
        health['wikidata'] = {'ok': False, 'err': str(e)[:160], 'at': iso(t0)}


def map_titles(http, en_titles, wmap: dict, health, budget: int = 100):
    """英文條目 → 中文條目（主機獨占等沒有 Steam 的遊戲用）。"""
    t0 = now()
    todo = [t for t in en_titles if t and hours_since((wmap.get(f'en:{t}') or {}).get('at'), t0) >= MAP_TTL_H]
    todo = todo[:budget]
    try:
        for i in range(0, len(todo), 40):
            chunk = todo[i:i + 40]
            d = http.json(WBGET.format(titles=urllib.parse.quote('|'.join(c.replace('_', ' ') for c in chunk))),
                          bot=True, timeout=40)
            seen = {}
            for ent in (d.get('entities') or {}).values():
                sl = ent.get('sitelinks') or {}
                en = (sl.get('enwiki') or {}).get('title')
                zh = (sl.get('zhwiki') or {}).get('title')
                if en:
                    seen[en.replace(' ', '_')] = zh.replace(' ', '_') if zh else None
            norm = {n.get('from', '').replace(' ', '_'): n.get('to', '').replace(' ', '_')
                    for n in (d.get('normalized') or [])}
            for t in chunk:
                key = norm.get(t, t)
                wmap[f'en:{t}'] = {'at': iso(t0), 'en': t, 'zh': seen.get(key)}
            time.sleep(0.8)
        health['wikidata_titles'] = {'ok': True, 'n': len(todo), 'at': iso(t0)}
    except Exception as e:  # noqa: BLE001
        health['wikidata_titles'] = {'ok': False, 'err': str(e)[:160], 'at': iso(t0)}


def pageviews(http, pages, pv: dict, health, budget: int = 260):
    """pages: [(proj, title)]；每個條目每天只抓一次最近 35 天，存在 pv['proj|title'] = {day: views}。"""
    t0 = now()
    today = t0.date()
    end = today - dt.timedelta(days=1)
    start = today - dt.timedelta(days=35)
    done = errs = missing = 0
    codes = {}
    for proj, title in pages:
        if done + errs >= budget:
            break
        key = f'{proj}|{title}'
        rec = pv.get(key) or {}
        if rec.get('fetched') == today.isoformat():
            continue
        url = PV.format(proj=proj, title=urllib.parse.quote(title, safe=''),
                        start=start.strftime('%Y%m%d'), end=end.strftime('%Y%m%d'))
        try:
            r = http.get(url, bot=True, timeout=20, retries=1)
            if r.status_code == 404:
                pv[key] = {'fetched': today.isoformat(), 'days': {}}
                missing += 1
                done += 1
                continue
            if r.status_code != 200:
                errs += 1
                codes[r.status_code] = codes.get(r.status_code, 0) + 1
                continue
            days = {}
            for it in r.json().get('items') or []:
                ts = it.get('timestamp', '')
                days[f'{ts[0:4]}-{ts[4:6]}-{ts[6:8]}'] = it.get('views', 0)
            pv[key] = {'fetched': today.isoformat(), 'days': days}
            done += 1
        except Exception:  # noqa: BLE001
            errs += 1
        time.sleep(0.12)
    # 只保留還在追蹤的條目，避免越存越多
    keep = {f'{p}|{t}' for p, t in pages}
    for k in list(pv):
        if k not in keep:
            del pv[k]
    health['wiki_pageviews'] = {'ok': done > 0 or errs == 0, 'n': done, 'missing': missing,
                                'at': iso(t0), **({'err': f'{errs} 次失敗 {codes}'} if errs else {})}


def pv_stats(rec: dict | None) -> dict | None:
    """每日瀏覽 → 最近 7 天平均、前 7 天平均、成長率、最近一天。"""
    if not rec or not rec.get('days'):
        return None
    days = sorted(rec['days'].items())
    vals = [v for _, v in days]
    if not vals:
        return None
    last7 = vals[-7:]
    prev7 = vals[-14:-7]
    base28 = vals[-35:-7]
    a7 = sum(last7) / len(last7)
    p7 = sum(prev7) / len(prev7) if prev7 else None
    b28 = sum(base28) / len(base28) if base28 else None
    return {'avg7': round(a7), 'prev7': round(p7) if p7 is not None else None,
            'last': vals[-1], 'last_day': days[-1][0],
            'g7': round(a7 / p7 - 1, 3) if p7 else None,
            'spike': round(vals[-1] / b28, 2) if b28 else None,
            'series': vals[-21:]}


def article_url(proj: str, title: str) -> str:
    lang = proj.split('.')[0]
    return f'https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title)}'


def dumps(o):
    return json.dumps(o, ensure_ascii=False)
