"""每小時一輪：抓各測站 → 建遊戲名冊 → 掛訊號 → 產生題目與角度 → 輸出 App 資料。"""
import datetime as dt
import gzip
import json
import os
import pathlib
import re
import shutil
import subprocess
import time
import traceback

from . import angles, entities, forreal, gitstate, ptt, signals as S, social, steam, topics, wiki
from .util import TW, Http, dumps, hours_since, iso, now, parse_day, parse_iso, sid

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_FILES = ['radar.json', 'calendar.json', 'meta.json']
_ZH_DATE = re.compile(r'(\d{4})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?')
_EN_DATE = re.compile(r'(\d{1,2})\s+([A-Za-z]{3})[a-z]*,?\s+(\d{4})|([A-Za-z]{3})[a-z]*\s+(\d{1,2}),?\s+(\d{4})')
_MON = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}


class State:
    def __init__(self, path: pathlib.Path):
        self.path = path

    def load(self, name, default):
        p = self.path / name
        try:
            if name.endswith('.gz'):
                return json.loads(gzip.decompress(p.read_bytes()).decode('utf-8'))
            return json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return default

    def save(self, name, obj):
        p = self.path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        raw = dumps(obj).encode('utf-8')
        if name.endswith('.gz'):
            p.write_bytes(gzip.compress(raw, 6))
        else:
            p.write_bytes(raw)


def parse_release(text: str):
    """Steam 的發售日文字 → ('2026-10-22', 'day') / ('2026-10', 'month')。"""
    text = (text or '').strip()
    m = _ZH_DATE.search(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), m.group(3)
        return (f'{y:04d}-{mo:02d}-{int(d):02d}', 'day') if d else (f'{y:04d}-{mo:02d}', 'month')
    m = _EN_DATE.search(text)
    if m:
        if m.group(1):
            d, mo, y = int(m.group(1)), _MON.get(m.group(2).lower()[:3]), int(m.group(3))
        else:
            mo, d, y = _MON.get(m.group(4).lower()[:3]), int(m.group(5)), int(m.group(6))
        if mo:
            return f'{y:04d}-{mo:02d}-{d:02d}', 'day'
    return None, None


def ensure_user_branch():
    """App 的遙控鑰匙會把方向設定寫到 user 分支的 prefs.json；分支不存在時先建一個空的（已存在就絕不動它）。"""
    try:
        r = subprocess.run(['git', 'ls-remote', '--exit-code', 'origin', 'refs/heads/user'], cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return
        if r.returncode != 2:
            return  # 查不到是因為連線問題，不要冒險
        path = gitstate.open_branch('user', '.user')
        (path / 'prefs.json').write_text('{}', encoding='utf-8')
        (path / 'README.md').write_text('回波 App 的方向設定（用遙控鑰匙同步）。請不要手動修改。\n', encoding='utf-8')
        gitstate.save_branch('user', '.user', 'create user branch')
    except (OSError, subprocess.SubprocessError):
        pass


def load_user(t0):
    """使用者在 App 存的方向設定（遙控鑰匙同步到 repo 的 user 分支），後台只用關注清單與靜音。"""
    try:
        r = subprocess.run(['git', 'fetch', '--depth=1', 'origin', '+user:refs/remotes/origin/user'], cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return {}
        raw = subprocess.run(['git', 'show', 'origin/user:prefs.json'], cwd=ROOT, capture_output=True, text=True,
                             timeout=30)
        return json.loads(raw.stdout) if raw.returncode == 0 else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


# ------------------------------------------------------------------ 名冊
def build_registry(fr, lists, mostplayed, spy, details_cache, user):
    reg = entities.Registry()
    for lst, rows in lists.items():
        for x in rows:
            day, prec = parse_release(x.get('released'))
            g = reg.upsert(appid=x['appid'], zh=x['name'], en=x.get('en'), source='steam', rank=(lst, x['rank']),
                           release=(day, prec, x.get('released')) if day else None)
            g.verified = True
    for x in mostplayed:
        name = (spy.get(str(x['appid'])) or {}).get('name')
        g = reg.upsert(appid=x['appid'], en=name, source='steam', rank=('mostplayed', x['rank']))
        g.verified = True
        g.type_hint = 'mostplayed'
    for k, v in (fr.get('names') or {}).items():
        if not (v.get('en') or v.get('zh')):
            continue
        g = reg.upsert(appid=v.get('appid'), en=v.get('en'), zh=v.get('zh'), source='forreal')
        if v.get('via') in ('steam', 'wiki'):
            g.verified = True
    rel = fr.get('releases') or {}
    for x in ((rel.get('steam_upcoming') or {}).get('items') or []) + ((rel.get('steam_new') or {}).get('items') or []):
        g = reg.upsert(appid=x.get('appid'), zh=x.get('name'), source='steam',
                       release=(x.get('date'), x.get('prec') or 'day', x.get('text')) if x.get('date') else None)
        g.verified = True
    for x in (rel.get('wiki') or {}).get('items') or []:
        if not x.get('title'):
            continue
        g = reg.upsert(en=x['title'], source='wiki', plat=x.get('plat') or [], big=x.get('big'),
                       release=(x.get('date'), x.get('prec'), x.get('text')) if x.get('date') else None)
        g.verified = True
        if x.get('url') and '/wiki/' in x['url'] and not g.wiki_en:
            g.wiki_en = x['url'].rsplit('/wiki/', 1)[-1]
    # 商店資料（中文名、發售日）
    for g in list(reg.games.values()):
        if g.appid and (details_cache.get(str(g.appid)) or {}).get('d'):
            d = details_cache[str(g.appid)]['d']
            g.details = d
            if d.get('name'):
                reg.upsert(appid=g.appid, zh=d['name'])
            day, prec = parse_release(d.get('date_text'))
            if day and (not g.release or g.release_prec != 'day' or prec == 'day'):
                g.release, g.release_prec, g.release_text = day, prec, d.get('date_text')
    entities.apply_aliases(reg, entities.load_aliases())
    for w in (user.get('watch') or []):
        name = (w.get('name') if isinstance(w, dict) else w) or ''
        g = reg.find(names=[name])
        if g:
            g.watch = True
    reg.build_matcher()
    return reg


def pick_candidates(reg, today):
    """要補資料（商店、評論、在線、維基）的遊戲，依重要性排序。"""
    scored = []
    for g in reg.games.values():
        pr = 0.0
        if g.watch:
            pr += 1000
        for lst, r in g.ranks.items():
            if r <= 100:
                pr += (100 - r) * (1.5 if lst in ('wishlist', 'topsell', 'topsell_tw', 'trending') else 1.0)
        rd = parse_day(g.release)
        if rd and -14 <= (rd - today).days <= 45:
            pr += 120 + (60 if g.big else 0)
        m = g.sig.get('media') or {}
        pr += 25 * m.get('cov', 0)
        pr += 15 * (g.sig.get('ptt') or {}).get('n72', 0)
        if g.big:
            pr += 40
        if pr > 0:
            scored.append((pr, g))
    scored.sort(key=lambda x: -x[0])
    return [g for _, g in scored]


# ------------------------------------------------------------------ 主流程
def execute(http, st: State, t0, data_dir: pathlib.Path, build_id: str, user=None):
    user = user if user is not None else {}
    health = st.load('health.json', {})
    cfg = topics.load_cfg('channel.json')
    videos = topics.load_cfg('videos.json')['videos']
    kw = topics.load_cfg('keywords.json')
    cal_cfg = topics.load_cfg('calendar.json')
    today = t0.astimezone(TW).date()
    timings = {}

    def tick(name, t_start):
        timings[name] = round(time.time() - t_start, 1)

    # 1) 玩真的的新聞
    t_ = time.time()
    fr = forreal.fetch(http, health)
    src_table = forreal.source_table(fr.get('meta'))
    stories = [dict(x, sec='game') for x in ((fr.get('news') or {}).get('items') or [])]
    stories += [dict(x, sec='tech') for x in ((fr.get('tech') or {}).get('items') or [])]
    tick('forreal', t_)

    # 2) Steam 排行
    t_ = time.time()
    lists = steam.fetch_lists(http, health)
    mostplayed = steam.fetch_mostplayed(http, health)
    try:
        spy = http.json('https://steamspy.com/api.php?request=top100in2weeks', timeout=30, retries=0)
        health['steamspy'] = {'ok': True, 'n': len(spy), 'at': iso(t0)}
    except Exception as e:  # noqa: BLE001
        spy = {}
        health['steamspy'] = {'ok': False, 'err': str(e)[:120], 'at': iso(t0)}
    tick('steam_lists', t_)

    # 3) 名冊
    t_ = time.time()
    details_cache = st.load('cache/details.json.gz', {})
    reviews_cache = st.load('cache/reviews.json.gz', {})
    reg = build_registry(fr, lists, mostplayed, spy, details_cache, user)
    tick('registry', t_)

    # 4) 社群與台灣訊號（先抓，才能用來排補資料的優先順序）
    t_ = time.time()
    ptt_store = st.load('ptt.json.gz', {})
    ptt.fetch(http, ptt_store, health)
    trends_store = st.load('trends.json', {})
    social.fetch_trends(http, trends_store, health)
    reddit_store = st.load('reddit.json', {})
    social.fetch_reddit_rss(http, reddit_store, health)
    gnews_items = social.fetch_gnews_general(http, health)
    tick('community', t_)

    links = S.attach_news(reg, stories, src_table, t0)
    S.attach_ptt(reg, ptt_store, t0)

    # 5) 補資料
    t_ = time.time()
    cands = pick_candidates(reg, today)
    app_cands = [g.appid for g in cands if g.appid]
    steam.details(http, app_cands[:220], details_cache, health, budget=45)
    for g in cands:
        if g.appid and (details_cache.get(str(g.appid)) or {}).get('d'):
            d = details_cache[str(g.appid)]['d']
            g.details = d
            day, prec = parse_release(d.get('date_text'))
            if day and (not g.release or g.release_prec != 'day' or prec == 'day'):
                g.release, g.release_prec, g.release_text = day, prec, d.get('date_text')
    released = [g.appid for g in cands if g.appid and not ((g.details or {}).get('coming'))]
    steam.reviews(http, released[:160], reviews_cache, health, budget=40)
    players_hist = st.load('hist/players.json.gz', {})
    mp_ids = [x['appid'] for x in mostplayed]
    pl_ids = list(dict.fromkeys(mp_ids + released[:120]))
    no_players = st.load('cache/noplayers.json', {})
    steam.players(http, pl_ids, players_hist, health, budget=200, skip=no_players)
    for g in reg.games.values():
        if g.appid:
            if str(g.appid) in players_hist:
                g.players = steam.player_stats(players_hist[str(g.appid)])
            if str(g.appid) in reviews_cache:
                g.reviews = reviews_cache[str(g.appid)]
    tick('steam_enrich', t_)

    # 6) 維基
    t_ = time.time()
    wmap = st.load('cache/wikimap.json', {})
    pv = st.load('hist/pv.json.gz', {})
    top_c = cands[:170]
    wiki.map_appids(http, [g.appid for g in top_c if g.appid], wmap, health)
    wiki.map_titles(http, [g.wiki_en for g in top_c if g.wiki_en and not g.appid], wmap, health)
    pages = []
    for g in top_c:
        rec = (wmap.get(f'app:{g.appid}') if g.appid else None) or (wmap.get(f'en:{g.wiki_en}') if g.wiki_en else None) or {}
        en = rec.get('en') or g.wiki_en
        zh = rec.get('zh')
        if en:
            g.wiki_en = en
            pages.append(('en.wikipedia', en))
        if zh:
            g.wiki_zh = zh
            pages.append(('zh.wikipedia', zh))
    wiki.pageviews(http, list(dict.fromkeys(pages)), pv, health)
    for g in top_c:
        for proj, attr, sk in (('en.wikipedia', 'wiki_en', 'wiki_en'), ('zh.wikipedia', 'wiki_zh', 'wiki_zh')):
            title = getattr(g, attr)
            if title:
                stt = wiki.pv_stats(pv.get(f'{proj}|{title}'))
                if stt:
                    stt['url'] = wiki.article_url(proj, title)
                    g.sig[sk] = stt
    tick('wiki', t_)

    # 7) 台灣新聞（針對熱門遊戲）、趨勢、Reddit 掛上去
    t_ = time.time()
    gn_cache = st.load('cache/gnews.json.gz', {})
    targets = [(g.gid, [g.zh, g.en if g.en and len(g.en) >= 6 else None]) for g in cands[:40]]
    social.fetch_gnews_entities(http, targets, gn_cache, health, budget=10)
    S.attach_gnews(reg, gnews_items, gn_cache, t0)
    S.attach_trends(reg, trends_store, t0)
    reddit_entries = list(reddit_store.values())
    S.attach_reddit(reg, reddit_entries, t0)
    tick('tw_signals', t_)

    # 8) 排名歷史（每 3 小時記一筆）
    hist_ranks = st.load('hist/ranks.json.gz', {})
    stamp = int(t0.timestamp())
    for lst, rows in list(lists.items()) + [('mostplayed', mostplayed)]:
        h = hist_ranks.setdefault(lst, {})
        for x in rows:
            ser = h.setdefault(str(x['appid']), [])
            if not ser or stamp - ser[-1][0] >= 3 * 3600 - 300:
                ser.append([stamp, x['rank']])
        for k in list(h):
            h[k] = [p for p in h[k] if stamp - p[0] <= 10 * 86400]
            if not h[k]:
                del h[k]

    # 9) 每款遊戲的測站彙總
    share = float(user.get('taiwan_share', cfg.get('taiwan_share', 0.45)))
    summaries = {}
    for g in reg.games.values():
        if not g.sig and not g.players and not g.ranks:
            continue
        summaries[g.gid] = S.summarize(g, hist_ranks, share, t0)

    # 10) 題目
    t_ = time.time()
    seen = st.load('topics_seen.json', {})
    notable_by_month = {}
    for g in reg.games.values():
        if not g.release:
            continue
        ym = g.release[:7]
        if not re.fullmatch(r'\d{4}-\d{2}', ym):
            continue
        sm = summaries.get(g.gid) or {}
        ok = g.big or g.ranks.get('wishlist', 999) <= 150 or g.ranks.get('upcoming', 999) <= 60 or g.watch \
            or sm.get('global', 0) >= 45
        if ok:
            notable_by_month.setdefault(ym, []).append({'gid': g.gid, 'name': g.name, 'date': g.release,
                                                        'prec': g.release_prec, 'big': g.big,
                                                        'w': g.ranks.get('wishlist'), 'score': sm.get('global', 0)})
    for v in notable_by_month.values():
        v.sort(key=lambda x: (not x['big'], -(x['score'] or 0)))
    b = topics.Builder(reg, cfg=cfg, videos=videos, kw=kw, stories=stories, src_table=src_table, ptt_posts=ptt_store,
                       reddit=reddit_entries, gnews_items=gnews_items, trends=trends_store, summaries=summaries,
                       hist_ranks=hist_ranks, seen=seen, t0=t0, user=user)
    for step in (b.launch, b.sleeper, b.review_bombs):
        try:
            step()
        except Exception:  # noqa: BLE001 — 一種題目出錯，其他照常
            traceback.print_exc()
    try:
        b.news_driven(links)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    try:
        b.season(cal_cfg, notable_by_month)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    tlist = list(b.topics.values())
    for t in tlist:
        try:
            t['angles'] = angles.make(t)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            t['angles'] = []
    tlist.sort(key=lambda t: -t['score'])
    tlist = tlist[:120]
    for t in tlist:
        rec = seen.get(t['id']) or {'first': t['first_seen']}
        rec['last'] = iso(t0)
        rec['best'] = max(rec.get('best', 0), t['score'])
        seen[t['id']] = rec
    for k in list(seen):
        if hours_since(seen[k].get('last'), t0) > 21 * 24:
            del seen[k]
    tick('topics', t_)

    # 11) 預報（行事曆）
    calendar = build_calendar(cal_cfg, tlist, reg, notable_by_month, today, b)

    # 12) 輸出
    station_health = {}
    for k, v in health.items():
        station_health[k] = {x: v.get(x) for x in ('ok', 'n', 'at', 'err', 'todo') if v.get(x) is not None}
    meta = {
        'generated': iso(t0), 'build': build_id, 'today': today.isoformat(),
        'channel': {k: cfg[k] for k in ('name', 'lead_days', 'weights', 'taiwan_share', 'pillars', 'type_label', 'owned_hardware', 'style')},
        'videos': videos, 'stations': S.STATIONS, 'health': station_health,
        'stats': {'games': len(reg.games), 'tracked': len(summaries), 'stories': len(stories),
                  'ptt_posts': len(ptt_store), 'topics': len(tlist), 'calls': http.calls, 'timings': timings},
        'repo': os.environ.get('REPO', 'PZWei0105/echo-radar'),
    }
    radar = {'generated': iso(t0), 'build': build_id, 'top': pick_top(tlist), 'topics': tlist}
    data_dir.mkdir(parents=True, exist_ok=True)
    outputs = {'radar.json': radar, 'calendar.json': calendar, 'meta.json': meta}
    for name, obj in outputs.items():
        (data_dir / name).write_text(dumps(obj), encoding='utf-8')

    # 13) 存狀態
    st.save('health.json', health)
    st.save('cache/details.json.gz', details_cache)
    st.save('cache/reviews.json.gz', reviews_cache)
    st.save('cache/wikimap.json', wmap)
    st.save('cache/gnews.json.gz', gn_cache)
    st.save('hist/players.json.gz', players_hist)
    st.save('cache/noplayers.json', no_players)
    st.save('hist/pv.json.gz', pv)
    st.save('hist/ranks.json.gz', hist_ranks)
    st.save('ptt.json.gz', ptt_store)
    st.save('trends.json', trends_store)
    st.save('reddit.json', reddit_store)
    st.save('topics_seen.json', seen)
    for name, obj in outputs.items():
        st.save(f'out/{name}', obj)
    runs = st.load('runs.json', [])
    runs.append({'at': iso(t0), 'secs': round(time.time() - t0.timestamp(), 1), 'calls': http.calls,
                 'topics': len(tlist), 'games': len(summaries), 'timings': timings,
                 'errors': sorted(k for k, v in health.items() if not v.get('ok'))})
    st.save('runs.json', runs[-60:])
    return outputs


def pick_top(tlist, n=5):
    """本週 TOP 5：照總分挑，但同一條路線最多 2 個、同一種題目最多 2 個，避免整排都是同一類。
    頻道已經在做的題目不放進 TOP（還是會出現在題庫裡）。"""
    out, by_p, by_t = [], {}, {}
    for t in tlist:
        if (t.get('done') or {}).get('status') in ('done', 'making'):
            continue
        if by_p.get(t['pillar'], 0) >= 2 or by_t.get(t['type'], 0) >= 2:
            continue
        out.append(t['id'])
        by_p[t['pillar']] = by_p.get(t['pillar'], 0) + 1
        by_t[t['type']] = by_t.get(t['type'], 0) + 1
        if len(out) >= n:
            break
    return out


def build_calendar(cal_cfg, tlist, reg, notable_by_month, today, b):
    """預報頁：未來 60 天的檔期、題目截止日、值得注意的發售日。"""
    items = []
    horizon = today + dt.timedelta(days=60)
    for ev in cal_cfg['events']:
        s, e = parse_day(ev['start']), parse_day(ev['end'])
        if not s or (e or s) < today - dt.timedelta(days=1) or s > horizon:
            continue
        items.append({'date': s.isoformat(), 'end': (e or s).isoformat(), 'kind': ev.get('kind'), 'title': ev['name'],
                      'when': b._event_when(ev), 'note': ev.get('note'), 'u': ev.get('src'), 'ref': None})
    for t in tlist:
        w = t.get('window') or {}
        for k, lab in (('start_by', '最晚開工'), ('publish_by', '最晚上架')):
            d = parse_day(w.get(k))
            if d and today <= d <= horizon and t['score'] >= 45:
                items.append({'date': d.isoformat(), 'kind': 'deadline', 'title': f'{lab}：{t["title"]}', 'ref': t['id'],
                              'score': t['score'], 'grade': t['grade'], 'which': k})
    for ym, games in notable_by_month.items():
        for x in games[:40]:
            g = reg.games.get(x['gid'])
            strong = x['big'] or (x.get('w') or 999) <= 60 or (g and (g.ranks.get('upcoming', 999) <= 20 or g.watch)) \
                or (x.get('score') or 0) >= 50
            d = parse_day(x['date']) if x.get('prec') == 'day' else None
            if strong and d and today - dt.timedelta(days=1) <= d <= horizon:
                items.append({'date': d.isoformat(), 'kind': 'release', 'title': f'《{x["name"]}》發售', 'ref': x['gid'],
                              'big': x['big']})
    items.sort(key=lambda x: (x['date'], {'steam_sale': 0, 'steam_fest': 0, 'show': 0, 'retail': 1, 'release': 2,
                                            'deadline': 3, 'steam_theme': 4}.get(x['kind'], 5)))
    return {'generated': iso(now()), 'today': today.isoformat(), 'items': items}


def copy_site(out_dir: pathlib.Path) -> str:
    site = ROOT / 'site'
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(site, out_dir)
    build_id = sid(str(time.time()))
    for p in out_dir.rglob('*'):
        if p.suffix in ('.html', '.js') and p.is_file():
            p.write_text(p.read_text(encoding='utf-8').replace('__BUILD__', build_id), encoding='utf-8')
    return build_id


def run(mode: str) -> bool:
    t0 = now()
    out = ROOT / 'public'
    build_id = copy_site(out)
    if mode == 'deploy':
        # 只改介面：沿用 state 分支裡最近一次的資料
        path = gitstate.open_branch('state', '.state')
        st = State(path)
        (out / 'data').mkdir(parents=True, exist_ok=True)
        for name in OUT_FILES:
            obj = st.load(f'out/{name}', None)
            if obj is not None:
                if name == 'meta.json':
                    obj['build'] = build_id
                (out / 'data' / name).write_text(dumps(obj), encoding='utf-8')
        return True
    path = gitstate.open_branch('state', '.state')
    st = State(path)
    http = Http()
    ensure_user_branch()
    user = load_user(t0)
    execute(http, st, t0, out / 'data', build_id, user)
    ok = gitstate.save_branch('state', '.state', f'state {iso(t0)}')
    print(f'[echo] state saved={ok} calls={http.calls} hosts={http.by_host}', flush=True)
    return True
