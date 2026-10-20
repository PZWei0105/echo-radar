"""選題產生器：把遊戲訊號、新聞、檔期變成「可以拍的題目」，並打四項分數。

題目種類 → 頻道路線（見 config/channel.json）：
  season 檔期／showcase 發表會 → 檔期推薦；issue 爭議／產業、weekly 本週大事 → 產業爭議；
  ranking 榜單 → 期待榜單；hardware 硬體 → 硬體科技；launch 新作首發、sleeper 黑馬爆紅 → 新作焦點。

四項分數（0–100）：
  需求：會不會有人看（各測站回波，台灣與全球加權，加上同步升溫的測站數）
  時效：現在拍是不是剛好（離截止日多遠，或話題還新不新）
  契合：適不適合這個頻道（路線、平台、繁中、是否已經做過）
  好做：好不好做（有沒有官方來源與素材、是不是只有傳聞、題材敏不敏感）
"""
import calendar as cal
import datetime as dt
import json
import pathlib
import re
from zoneinfo import ZoneInfo

from . import signals as S
from .entities import key as name_key
from .util import TW, clamp, hours_since, parse_day, parse_iso, sid

ROOT = pathlib.Path(__file__).resolve().parent.parent
PT = ZoneInfo('America/Los_Angeles')
TPE = ZoneInfo('Asia/Taipei')


def load_cfg(name):
    return json.loads((ROOT / 'config' / name).read_text(encoding='utf-8'))


# 新聞驅動的題目（爭議、硬體、發表會）沒有 Steam 人數可看，媒體與社群討論的權重要拉高
NEWS_GLOBAL_W = {'media': 0.85, 'reddit': 0.7, 'steam': 0.45, 'wiki_en': 0.4}
NEWS_TW_W = {'ptt': 0.8, 'gnews': 0.7, 'trends': 0.9, 'wiki_zh': 0.35, 'steam_tw': 0.45}


# ------------------------------------------------------------------ 共用
def wd(d: dt.date) -> str:
    return '一二三四五六日'[d.weekday()]


def md(d: dt.date) -> str:
    return f'{d.month}/{d.day}'


def total(scores: dict, w: dict) -> float:
    """總分 = 四項加權平均 × 需求係數。需求是乘數：沒人看的題目，再好做、再準時也沒用。"""
    base = sum(scores[k] * w[k] for k in w) / sum(w.values())
    return base * (0.6 + 0.4 * scores['demand'] / 100)


def grade(score: float) -> str:
    if score >= 70:
        return '必拍'
    if score >= 58:
        return '可拍'
    if score >= 46:
        return '觀望'
    return '先跳過'


def _deadline_timing(today, open_d, start_by, publish_by, close):
    """有截止日的題目：回傳 (分數, 一句話)。"""
    if close and today > close:
        return None, '已經過了'
    if open_d and today < open_d:
        days = (open_d - today).days
        return 22, f'{days} 天後才開始熱（{md(open_d)} 起）'
    d = (start_by - today).days if start_by else 99
    if publish_by and today > publish_by:
        return 45, f'最佳上架日 {md(publish_by)} 已過，現在補拍還來得及到 {md(close)}'
    if d < 0:
        return 96, f'已經該開工了，最晚 {md(publish_by)}（週{wd(publish_by)}）要上架'
    if d == 0:
        return 100, f'今天就要開工，{md(publish_by)}（週{wd(publish_by)}）前上架'
    if d <= 2:
        return 92, f'{d} 天內要開工（{md(start_by)}），{md(publish_by)} 前上架'
    if d <= 6:
        return 78, f'本週開工（{md(start_by)} 前），{md(publish_by)} 前上架'
    if d <= 13:
        return 60, f'下週開工就好，{md(publish_by)} 前上架'
    if d <= 27:
        return 42, f'還有時間，{md(start_by)} 前開工'
    return 28, f'不急，{md(start_by)} 前開工'


def _decay_timing(first_seen, half_life_d: float, rising: bool, t0):
    age_h = hours_since(first_seen, t0)
    base = 100 * 0.5 ** (age_h / (half_life_d * 24))
    if rising:
        base = max(base, 85)
    if age_h < 30:
        txt = '話題剛冒出來，越早拍越好'
    elif rising:
        txt = '還在升溫中'
    elif base >= 50:
        txt = f'熱度還在，建議 {max(1, round(half_life_d * 0.8))} 天內上架'
    else:
        txt = '熱度在退，要拍就要有新角度'
    return clamp(base), txt


_F2P_CATS = re.compile(r'應用程式內購買|In-App Purchases', re.I)
_GACHA_HINT = re.compile(r'gacha|抽卡|轉蛋|卡池|角色池|二次元', re.I)


def is_live_f2p(g) -> bool:
    """免費遊玩＋內購（多半是抽卡或長期營運型），頻道不做這類宣傳。"""
    det = (g.details or {}) if g else {}
    if not det:
        return False
    cats = ' '.join(det.get('cats') or [])
    return bool(det.get('free') and (_F2P_CATS.search(cats) or _GACHA_HINT.search(det.get('desc') or '')))


def _fit(cfg, pillar: str, g=None, *, platforms=None, done=None, covered=None, tw_level=0.0, watch=False):
    p = cfg['pillars'][pillar]
    fit = p['base_fit']
    why = [f"{p['label']}路線：{p['desc']}"]
    plat = set(platforms or (g.plat if g else set()))
    det = (g.details or {}) if g else {}
    if g is not None:
        if g.appid:
            plat.add('pc')
        if plat and plat <= {'mobile', 'ios', 'android'}:
            fit -= 35
            why.append('只有手機版，頻道一向不做手遊')
        if is_live_f2p(g):
            fit -= 22
            why.append('免費遊玩加內購的長期營運遊戲，跟頻道路線不太合')
        langs = det.get('langs') or {}
        if langs.get('tc'):
            fit += 6
            why.append('有繁體中文' + ('（含語音）' if langs.get('tc_voice') else '') + '，台灣觀眾門檻低')
        elif det and not langs.get('tc'):
            fit -= 3
            why.append('沒有繁體中文，台灣觀眾門檻較高（但也能變成角度）')
    if tw_level >= 30:
        fit += 5
        why.append('台灣本地也在討論')
    if done:
        fit -= 25 if done['status'] in ('done', 'making') else 0
        why.append(f"頻道已經做過／正在做：《{done['title']}》")
    elif covered:
        fit -= 8
        why.append(f"《{covered['title']}》會提到它，單獨做要找新角度")
    if watch:
        fit += 15
        why.append('在你的關注清單上')
    return clamp(fit), why


def _ease(*, official=False, multi=False, trailer=False, demo=False, rumor_only=False, sensitive=False,
          store=False, extra=None):
    e = 55
    why = []
    if official or store:
        e += 15
        why.append('有官方來源可以查證' if official else '有 Steam 商店頁可以查資料')
    if multi:
        e += 10
        why.append('多家媒體報導，資料好找')
    if trailer:
        e += 8
        why.append('有官方預告片與截圖可用')
    if demo:
        e += 8
        why.append('有試玩版，可以自己錄畫面（版權風險最低）')
    if rumor_only:
        e -= 25
        why.append('目前只有傳聞，查證成本高')
    if sensitive:
        e -= 12
        why.append('題材敏感，措辭要小心（只談有證據的部分）')
    for delta, text in extra or []:
        e += delta
        why.append(text)
    return clamp(e), why


def _why_now(cards: list[dict], timing_text: str, limit=2) -> str:
    own = [c for c in cards if not (c.get('text') or '').startswith('（')]
    rest = [c for c in cards if (c.get('text') or '').startswith('（')]
    picks = (sorted(own, key=lambda c: (c.get('up', False), c.get('level', 0)), reverse=True)
             + sorted(rest, key=lambda c: c.get('level', 0), reverse=True))[:limit]
    parts = [c['text'].split('：')[0] if c['id'] in ('gnews', 'reddit') else c['text'] for c in picks]
    parts = [p for p in parts if p]
    s = '；'.join(parts)
    if timing_text:
        s = (s + '。' if s else '') + timing_text
    return s


def _game_brief(g) -> dict:
    det = g.details or {}
    rv = g.reviews or {}
    langs = det.get('langs') or {}
    plat = sorted(g.plat | ({'pc'} if g.appid else set()))
    return {
        'gid': g.gid, 'name': g.name, 'zh': g.zh, 'en': g.en, 'appid': g.appid, 'store': g.store_url,
        'img': det.get('img') or g.img or (f'https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{g.appid}/header.jpg' if g.appid else None),
        'release': g.release, 'release_prec': g.release_prec, 'release_text': g.release_text or det.get('date_text'),
        'plat': plat, 'tc': langs.get('tc'), 'tc_voice': langs.get('tc_voice'), 'sc': langs.get('sc'),
        'price': det.get('price'), 'price_text': det.get('price_text'), 'discount': det.get('discount'),
        'genres': det.get('genres') or [], 'dev': det.get('dev') or [], 'pub': det.get('pub') or [],
        'desc': det.get('desc'), 'gpu': det.get('gpu'), 'movies': det.get('movies'), 'demo': det.get('demo'),
        'free': det.get('free'), 'meta': det.get('meta'),
        'reviews': rv.get('all'), 'recent': rv.get('recent'), 'tc_reviews': rv.get('tc'), 'tc_recent': rv.get('tc_recent'),
        'players': g.players, 'ranks': dict(g.ranks),
        'wiki_en': (g.sig.get('wiki_en') or {}).get('url'), 'wiki_zh': (g.sig.get('wiki_zh') or {}).get('url'),
    }


def _game_facts(g) -> list[dict]:
    """可以直接寫進企劃包的事實（每條都附來源）。"""
    b = _game_brief(g)
    facts = []
    store = b['store']
    if b['release_text'] or b['release']:
        plats = '、'.join({'pc': 'PC', 'ps': 'PS5', 'xbox': 'Xbox', 'switch': 'Switch', 'switch2': 'Switch 2',
                          'mobile': '手機'}.get(p, p) for p in b['plat'])
        facts.append({'k': '發售', 'v': f"{b['release_text'] or b['release']}" + (f'（{plats}）' if plats else ''),
                      'src': 'Steam 商店頁' if store else '維基百科發售表', 'u': store or b['wiki_en']})
    if b['price_text']:
        v = b['price_text'] + (f"（特價 -{b['discount']}%）" if b.get('discount') else '')
        facts.append({'k': '台灣售價', 'v': v, 'src': 'Steam 台灣區', 'u': store})
    if b['tc'] is not None:
        facts.append({'k': '繁體中文', 'v': ('有' + ('（含語音）' if b['tc_voice'] else '（介面／字幕）')) if b['tc'] else '沒有',
                      'src': 'Steam 商店頁語言列表', 'u': store})
    rv = b['reviews'] or {}
    if rv.get('n'):
        v = f"{rv.get('desc') or ''} {rv.get('pct')}%（{S.fmt_num(rv['n'])} 篇）"
        rc = b['recent'] or {}
        if rc.get('n'):
            v += f"；最近 {rc['n']} 篇 {rc['pct']}%"
        facts.append({'k': 'Steam 評價', 'v': v.strip(), 'src': 'Steam 評論統計', 'u': store})
    tc = b['tc_reviews'] or {}
    if tc.get('n'):
        facts.append({'k': '繁中玩家評價', 'v': f"{tc.get('pct')}%（{S.fmt_num(tc['n'])} 篇繁中評論）",
                      'src': 'Steam 評論統計（繁體中文）', 'u': store})
    p = b['players'] or {}
    if p.get('now'):
        v = f"{S.fmt_num(p['now'])} 人"
        if p.get('peak14'):
            v += f"（14 天內高點 {S.fmt_num(p['peak14'])}）"
        facts.append({'k': '同時在線', 'v': v, 'src': 'Steam 官方統計 API（雷達每小時記錄）', 'u': store})
    if b['gpu']:
        facts.append({'k': '建議顯卡', 'v': b['gpu'], 'src': 'Steam 商店頁建議配備', 'u': store})
    if b['dev']:
        facts.append({'k': '開發／發行', 'v': '、'.join(b['dev']) + ('／' + '、'.join(b['pub']) if b['pub'] and b['pub'] != b['dev'] else ''),
                      'src': 'Steam 商店頁', 'u': store})
    return facts


def _stories_facts(stories: list[dict], limit=5) -> list[dict]:
    out = []
    seen = set()
    for s in sorted(stories, key=lambda x: (x.get('official', False), x.get('n', 1), x.get('at') or ''), reverse=True):
        if s['id'] in seen:
            continue
        seen.add(s['id'])
        tag = '官方' if s.get('official') else ('可信媒體' if s.get('tier', 3) <= 2 else '媒體')
        if 'rumor' in (s.get('soft') or []):
            tag = '傳聞'
        out.append({'k': tag, 'v': s.get('t'), 'src': s.get('srcname') + (f"等 {s['n']} 家" if s.get('n', 1) > 1 else ''),
                    'u': s.get('u'), 'at': s.get('at')})
        if len(out) >= limit:
            break
    return out


def _timeline(stories, extra=()):
    items = [{'at': s.get('at'), 't': s.get('t'), 'src': s.get('srcname'), 'u': s.get('u')} for s in stories if s.get('at')]
    items += list(extra)
    items.sort(key=lambda x: x.get('at') or '')
    seen, out = set(), []
    for it in items:
        k = (it.get('t') or '')[:30]
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out[-8:]


# ------------------------------------------------------------------ 已經做過？
class Done:
    def __init__(self, videos: list[dict]):
        self.videos = videos

    def for_event(self, event_id):
        for v in self.videos:
            if event_id in ((v.get('match') or {}).get('events') or []):
                return v
        return None

    def for_month(self, ym):
        for v in self.videos:
            if ym in ((v.get('match') or {}).get('months') or []):
                return v
        return None

    def for_text(self, text, pillar=None):
        t = (text or '').lower()
        for v in self.videos:
            for k in (v.get('match') or {}).get('keys') or []:
                if k.lower() in t and (pillar is None or v.get('pillar') == pillar):
                    return v
        return None


def _done_obj(v):
    return {'id': v['id'], 'title': v['title'], 'status': v.get('status'), 'date': v.get('date')} if v else None


# ------------------------------------------------------------------ 文字相似（找 PTT／Reddit 上討論同一件事的文章）
def _bigrams(s: str) -> set:
    s = re.sub(r'\s+|[^\w㐀-鿿]', '', (s or '').lower())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _words(s: str) -> set:
    stop = {'the', 'a', 'an', 'of', 'to', 'in', 'on', 'for', 'and', 'is', 'are', 'with', 'its', 'it', 'as', 'at', 'by',
            'new', 'game', 'games', 'after', 'from', 'that', 'this', 'be', 'will', 'has', 'have', 'now', 'out', 'says'}
    return {w for w in re.findall(r"[a-z0-9']{3,}", (s or '').lower()) if w not in stop}


def similar(a: str, b: str, zh: bool) -> float:
    if zh:
        x, y = _bigrams(a), _bigrams(b)
    else:
        x, y = _words(a), _words(b)
    if not x or not y:
        return 0.0
    return len(x & y) / min(len(x), len(y))


def related_posts(text_zh: str, text_en: str, ptt: dict, reddit: list, t0, kw=()):
    """跟某則新聞講同一件事的 PTT 文章與 Reddit 討論。kw 是額外的必含關鍵字（任一）。"""
    hits_ptt = []
    nowts = t0.timestamp()
    for p in ptt.values():
        if (nowts - p.get('ts', 0)) / 3600 > 96:
            continue
        title = S._ptt_clean(p.get('title'))
        ok = kw and any(k.lower() in title.lower() for k in kw)
        if ok or (text_zh and similar(text_zh, title, True) >= 0.45):
            hits_ptt.append({'t': p.get('title'), 'u': p.get('url'), 'push': p.get('push_max', p.get('push', 0)),
                             'board': p.get('board'), 'ts': p.get('ts')})
    hits_rd = []
    for e in reddit:
        if hours_since(e.get('at'), t0) > 72:
            continue
        ok = kw and any(k.lower() in (e.get('t') or '').lower() for k in kw)
        if ok or (text_en and similar(text_en, e.get('t'), False) >= 0.5):
            hits_rd.append({'t': e.get('t'), 'u': e.get('u'), 'sub': e.get('sub'), 'rank': e.get('rank')})
    hits_ptt.sort(key=lambda x: -x['push'])
    hits_rd.sort(key=lambda x: x.get('rank') or 99)
    return hits_ptt[:5], hits_rd[:3]


def _community_cards(ptt_hits, rd_hits):
    cards = []
    lv = {}
    if ptt_hits:
        pseudo = {'n72': len(ptt_hits), 'push_max': max(h['push'] for h in ptt_hits)}
        lv['ptt'] = S.ptt_level(pseudo)
        top = ptt_hits[0]
        push = '爆' if top['push'] >= 100 else f"{top['push']} 推"
        cards.append({'id': 'ptt', 'level': lv['ptt'], 'up': len(ptt_hits) >= 2,
                      'text': f"{len(ptt_hits)} 篇在討論，最熱：{top['t'][:30]}（{push}）", 'url': top['u']})
    if rd_hits:
        pseudo = {'best': rd_hits[0].get('rank') or 25, 'n': len(rd_hits)}
        lv['reddit'] = S.reddit_level(pseudo)
        cards.append({'id': 'reddit', 'level': lv['reddit'], 'up': (rd_hits[0].get('rank') or 99) <= 10,
                      'text': f"r/{rd_hits[0]['sub']} 當日熱門第 {rd_hits[0].get('rank')}：{rd_hits[0]['t'][:40]}",
                      'url': rd_hits[0]['u']})
    return cards, lv


# ------------------------------------------------------------------ 主程式
class Builder:
    def __init__(self, reg, *, cfg, videos, kw, stories, src_table, ptt_posts, reddit, gnews_items, trends,
                 summaries, hist_ranks, seen, t0, user=None):
        self.reg = reg
        self.cfg = cfg
        self.done = Done(videos)
        self.kw = kw
        self.stories = stories
        self.src = src_table
        self.ptt = ptt_posts
        self.reddit = reddit
        self.gnews_items = gnews_items
        self.trends = trends
        self.summ = summaries
        self.hist_ranks = hist_ranks
        self.seen = seen
        self.t0 = t0
        self.today = t0.astimezone(TW).date()
        self.lead = int(cfg.get('lead_days', 3))
        self.user = user or {}
        self.topics = {}
        self._compile_kw()

    # ---------------- 關鍵字
    def _compile_kw(self):
        def comp(en, zh):
            parts = [f'(?:{p})' for p in (en or [])] + [f'(?:{z})' for z in (zh or [])]
            return re.compile('|'.join(parts), re.I) if parts else None
        self.issue_re = {k: comp(v.get('en', []), v.get('zh', [])) for k, v in self.kw['issue'].items()}
        self.issue_label = {k: v['label'] for k, v in self.kw['issue'].items()}
        self.sens_re = comp(self.kw['sensitive']['en'], self.kw['sensitive']['zh'])
        hw = self.kw['hardware']
        self.hw_strong = comp(hw['strong_en'], hw['strong_zh'])
        self.hw_platform = comp(hw['platform_en'], hw['platform_zh'])
        self.hw_context = comp(hw['context_en'], hw['context_zh'])
        self.show_re = comp(self.kw['showcase']['en'], self.kw['showcase']['zh'])
        self.gaming_re = comp(self.kw['gaming']['en'], self.kw['gaming']['zh'])

    def _is_hardware(self, text, has_game):
        if self.hw_strong and self.hw_strong.search(text):
            return True
        if not has_game and self.hw_platform and self.hw_platform.search(text) and self.hw_context and self.hw_context.search(text):
            return True
        return False

    def _issue_cats(self, text):
        return [k for k, r in self.issue_re.items() if r and r.search(text)]

    def first_seen(self, tid, fallback=None):
        rec = self.seen.get(tid)
        if rec:
            return rec['first']
        return fallback or self.t0.strftime('%Y-%m-%dT%H:%M:%SZ')

    def add(self, t: dict):
        if not t:
            return
        old = self.topics.get(t['id'])
        if old and old.get('score', 0) >= t.get('score', 0):
            return
        self.topics[t['id']] = t

    def _finish(self, t: dict, cards, demand, demand_why, timing, timing_text, fit, fit_why, ease, ease_why, w):
        t['scores'] = {'demand': round(demand, 1), 'timing': round(timing, 1), 'fit': round(fit, 1), 'ease': round(ease, 1)}
        t['score'] = round(total(t['scores'], w), 1)
        t['grade'] = grade(t['score'])
        t['stations'] = cards
        t['rising'] = sum(1 for c in cards if c.get('up'))
        t['why'] = _why_now(cards, timing_text)
        t['reasons'] = {'demand': demand_why, 'timing': [timing_text], 'fit': fit_why, 'ease': ease_why}
        t['pillar_label'] = self.cfg['pillars'][t['pillar']]['label']
        t['type_label'] = self.cfg['type_label'].get(t['type'], t['type'])
        fs = self.first_seen(t['id'], t.get('first_hint'))
        t['first_seen'] = fs
        t['new'] = hours_since(fs, self.t0) < 6
        t.pop('first_hint', None)
        return t

    def _demand_from_summary(self, summ, extra_levels=None):
        lv = dict(summ['levels'])
        for k, v in (extra_levels or {}).items():
            lv[k] = max(lv.get(k, 0), v)
        glob = S.soft_or(lv, S.GLOBAL_W)
        tw = S.soft_or(lv, S.TW_W)
        share = float(self.cfg.get('taiwan_share', 0.45))
        n_rise = len(summ.get('rising') or [])
        d = (1 - share) * glob + share * tw + min(12, 4 * n_rise)
        why = [f'全球回波 {round(glob)}、台灣回波 {round(tw)}']
        if n_rise:
            why.append(f'{n_rise} 個測站同步升溫')
        return clamp(d), why, glob, tw

    # ---------------- 新作首發
    def launch(self):
        w = self.cfg['weights']
        for g in self.reg.games.values():
            rd = parse_day(g.release) if g.release_prec == 'day' else None
            if not rd:
                continue
            days = (rd - self.today).days
            if days < -10 or days > 21:
                continue
            summ = self.summ.get(g.gid)
            if not summ:
                continue
            notable = (g.ranks.get('wishlist', 999) <= 100 or g.ranks.get('upcoming', 999) <= 40 or g.big
                       or g.ranks.get('topsell', 999) <= 40 or g.ranks.get('topsell_tw', 999) <= 25
                       or g.ranks.get('mostplayed', 999) <= 100 or summ['levels'].get('media', 0) >= 40
                       or summ['global'] >= 50 or summ['tw'] >= 40 or g.watch)
            if days < 0:
                notable = notable and (summ['global'] >= 30 or summ['tw'] >= 30 or g.big)
            if not notable:
                continue
            if g.details and g.details.get('type') not in (None, 'game'):
                continue
            name = g.name
            if days > 3:
                phase, title = 'pre', f'《{name}》{md(rd)} 上市：值不值得首發？'
                publish_by, close = rd - dt.timedelta(days=1), rd + dt.timedelta(days=7)
                open_d = rd - dt.timedelta(days=14)
            elif days >= -3:
                phase, title = 'launch', f'《{name}》首發評價：值不值得買？'
                publish_by, close = rd + dt.timedelta(days=3), rd + dt.timedelta(days=10)
                open_d = rd - dt.timedelta(days=14)
            else:
                phase, title = 'post', f'《{name}》首週口碑：玩家怎麼說？'
                publish_by, close = rd + dt.timedelta(days=7), rd + dt.timedelta(days=14)
                open_d = rd
            start_by = publish_by - dt.timedelta(days=self.lead)
            timing, ttext = _deadline_timing(self.today, open_d, start_by, publish_by, close)
            if timing is None:
                continue
            ym = f'{rd.year}-{rd.month:02d}'
            covered = self.done.for_month(ym)
            fit, fit_why = _fit(self.cfg, 'spotlight', g, covered=_done_obj(covered), tw_level=summ['tw'], watch=g.watch)
            det = g.details or {}
            m = g.sig.get('media') or {}
            ease, ease_why = _ease(official=m.get('official'), multi=(m.get('n72', 0) >= 3), store=bool(g.appid),
                                   trailer=(det.get('movies') or 0) > 0, demo=det.get('demo'),
                                   rumor_only=bool(m) and all('rumor' in s['soft'] for s in m.get('stories', [])))
            cards = S.station_cards(g, summ)
            demand, dwhy, glob, tw = self._demand_from_summary(summ)
            t = {'id': f'launch:{g.gid}', 'type': 'launch', 'pillar': 'spotlight', 'title': title, 'phase': phase,
                 'game': _game_brief(g), 'facts': _game_facts(g) + _stories_facts(m.get('stories', []), 3),
                 'timeline': _timeline(m.get('stories', []), [{'at': f'{rd.isoformat()}T00:00:00Z', 't': f'《{name}》發售',
                                                                'src': '發售日'}]),
                 'window': {'kind': 'deadline', 'open': open_d.isoformat(), 'start_by': start_by.isoformat(),
                            'publish_by': publish_by.isoformat(), 'close': close.isoformat(), 'text': ttext},
                 'ptt': (g.sig.get('ptt') or {}).get('posts', [])[:5], 'flags': [],
                 'covered': _done_obj(covered), 'demand_parts': {'global': round(glob), 'tw': round(tw)}}
            if days < 0 and (g.reviews or {}).get('all') is None and not (g.players or {}).get('now'):
                t['flags'].append('資料還在累積')
            self.add(self._finish(t, cards, demand, dwhy, timing, ttext, fit, fit_why, ease, ease_why, w))

    # ---------------- 黑馬爆紅
    _EVERGREEN = {730, 570, 578080, 1172470, 440, 252490, 271590, 2767030, 1623730, 359550, 1085660, 230410,
                  381210, 892970, 105600, 4000, 236390, 1938090, 2357570, 2073850, 238960, 1599340, 2139460}

    def sleeper(self):
        w = self.cfg['weights']
        for g in self.reg.games.values():
            summ = self.summ.get(g.gid)
            if not summ or not g.appid:
                continue
            p = g.players or {}
            rd = parse_day(g.release) if g.release_prec == 'day' else None
            if rd and (self.today - rd).days <= 10:
                continue  # 剛上市的交給「新作首發」
            reasons = []
            g7, g1 = p.get('g7'), p.get('g1')
            recent_release = bool(rd and (self.today - rd).days <= 90)
            under_covered = summ['levels'].get('media', 0) < 30
            if p.get('now', 0) >= 3000 and g7 is not None and g7 >= 0.5 and p.get('points', 0) >= 24 * 5:
                reasons.append(f"同時在線一週 {S.pct(g7)}")
            elif p.get('now', 0) >= 5000 and g1 is not None and g1 >= 0.6 and p.get('points', 0) >= 20:
                reasons.append(f"同時在線一天 {S.pct(g1)}")
            tr = g.ranks.get('trending')
            if tr and tr <= 10 and under_covered and recent_release:
                reasons.append(f'Steam 熱門新品第 {tr}，但媒體還很少報')
            ts = g.ranks.get('topsell_tw')
            if ts and ts <= 12 and (g.ranks.get('topsell') or 999) > 60 and recent_release:
                reasons.append(f"台灣區熱銷第 {ts}，比全球（{'百名外' if not g.ranks.get('topsell') else '第 ' + str(g.ranks['topsell'])}）熱很多")
            wz = g.sig.get('wiki_en') or {}
            if (wz.get('spike') or 0) >= 2.5 and (wz.get('avg7') or 0) >= 1500:
                reasons.append(f"英文維基瀏覽突然暴增 {wz['spike']:.1f} 倍")
            if is_live_f2p(g) or ((g.details or {}).get('type') not in (None, 'game')):
                continue
            if not reasons:
                continue
            if g.appid in self._EVERGREEN and not (g7 and g7 >= 0.5):
                continue
            tid = f'sleeper:{g.gid}'
            fs = self.first_seen(tid)
            timing, ttext = _decay_timing(fs, 6, bool(summ['rising']), self.t0)
            fit, fit_why = _fit(self.cfg, 'spotlight', g, tw_level=summ['tw'], watch=g.watch)
            det = g.details or {}
            m = g.sig.get('media') or {}
            ease, ease_why = _ease(store=True, multi=(m.get('n72', 0) >= 3), trailer=(det.get('movies') or 0) > 0,
                                   demo=det.get('demo'))
            cards = S.station_cards(g, summ)
            demand, dwhy, glob, tw = self._demand_from_summary(summ)
            dwhy = reasons + dwhy
            title = f'《{g.name}》突然爆紅：{reasons[0]}'
            fsd = parse_iso(fs).astimezone(TW).date()
            t = {'id': tid, 'type': 'sleeper', 'pillar': 'spotlight', 'title': title, 'game': _game_brief(g),
                 'facts': _game_facts(g) + _stories_facts(m.get('stories', []), 3),
                 'timeline': _timeline(m.get('stories', [])),
                 'window': {'kind': 'decay', 'open': fsd.isoformat(), 'publish_by': (fsd + dt.timedelta(days=5)).isoformat(),
                            'close': (fsd + dt.timedelta(days=12)).isoformat(), 'text': ttext},
                 'ptt': (g.sig.get('ptt') or {}).get('posts', [])[:5], 'flags': [], 'hooks': reasons,
                 'demand_parts': {'global': round(glob), 'tw': round(tw)}}
            self.add(self._finish(t, cards, demand, dwhy, timing, ttext, fit, fit_why, ease, ease_why, w))

    # ---------------- 爭議／產業、硬體、發表會（新聞驅動）
    def news_driven(self, links: dict):
        w = self.cfg['weights']
        groups = {}
        for s in self.stories:
            age = hours_since(s.get('up') or s.get('at'), self.t0)
            if age > 96:
                continue
            text = ' '.join(S.story_texts(s)[:4]) + ' ' + (s.get('sz') or '') + ' ' + (s.get('s') or '')
            title_text = ' '.join(S.story_texts(s)[:4])
            cats = self._issue_cats(title_text)
            n = int(s.get('n') or 1)
            official = 'official' in (s.get('flags') or [])
            gids = links.get(s['id']) or []
            gaming = s.get('sec') == 'game' or bool(gids) or bool(self.gaming_re and self.gaming_re.search(title_text))
            hw = self._is_hardware(title_text, bool(gids))
            show = bool(self.show_re and self.show_re.search(title_text))
            if cats and gaming:
                if cats == ['delay'] and not (gids and self._big(gids[0])):
                    continue
                if n < 2 and not official and not (gids and self._big(gids[0])):
                    # 單一來源的小新聞，除非 PTT／Reddit 也在討論
                    ph, rh = related_posts(s.get('tz') or '', s.get('t') or '', self.ptt, self.reddit, self.t0)
                    if not ph and not rh:
                        continue
                kind = 'issue'
                gkey = (gids[0] if gids else s['id']) + ':' + cats[0]
            elif hw and (n >= 2 or official or self.src.get(s.get('src'), {}).get('tier', 3) <= 2):
                kind = 'hardware'
                gkey = s['id']
            elif show and (n >= 2 or official):
                kind = 'showcase'
                gkey = s['id']
            else:
                continue
            groups.setdefault((kind, gkey), []).append((s, cats, gids, text))
        for (kind, gkey), items in groups.items():
            t = self._news_topic(kind, gkey, items, w)
            if t:
                self.add(t)

    def _big(self, gid) -> bool:
        g = self.reg.games.get(gid)
        if not g:
            return False
        sm = self.summ.get(gid) or {}
        return g.big or g.ranks.get('wishlist', 999) <= 50 or g.ranks.get('topsell', 999) <= 30 or sm.get('global', 0) >= 55

    def _news_topic(self, kind, gkey, items, w):
        items.sort(key=lambda x: (int(x[0].get('n') or 1), 'official' in (x[0].get('flags') or [])), reverse=True)
        main, cats, gids, text = items[0]
        all_cats = []
        for _, c, _, _ in items:
            for x in c:
                if x not in all_cats:
                    all_cats.append(x)
        zh_title = main.get('tz') or main.get('t')
        en_title = main.get('t') if (main.get('lang') == 'en') else ''
        stories = []
        for s, _, _, _ in items:
            src = self.src.get(s.get('src')) or {}
            stories.append({'id': s['id'], 't': s.get('tz') or s.get('t'), 'u': s.get('u'), 'src': s.get('src'),
                            'srcname': src.get('name', s.get('src')), 'tier': src.get('tier', 3), 'n': int(s.get('n') or 1),
                            'official': 'official' in (s.get('flags') or []), 'at': s.get('at'), 'soft': s.get('soft') or [],
                            'types': s.get('types') or []})
            for m in s.get('m') or []:
                msrc = self.src.get(m.get('src')) or {}
                stories.append({'id': m.get('id'), 't': m.get('tz') or m.get('t'), 'u': m.get('u'), 'src': m.get('src'),
                                'srcname': msrc.get('name', m.get('src')), 'tier': msrc.get('tier', 3), 'n': 1,
                                'official': msrc.get('tier') == 1, 'at': m.get('at'), 'soft': s.get('soft') or [],
                                'types': []})
        n_src = len({x['src'] for x in stories})
        official = any(x['official'] for x in stories)
        rumor_only = all('rumor' in (x.get('soft') or []) for x in stories) and not official
        sensitive = bool(self.sens_re and self.sens_re.search(text))
        g = self.reg.games.get(gids[0]) if gids else None
        kwords = []
        if g:
            kwords = [x for x in (g.zh, g.en) if x]
        ph, rh = related_posts(zh_title, en_title or main.get('t') or '', self.ptt, self.reddit, self.t0, kw=())
        com_cards, com_lv = _community_cards(ph, rh)
        # 媒體回波：這則新聞的報導量
        cov = n_src * (1.3 if official else 1.0) + 0.3 * max(0, len(stories) - n_src)
        media_lv = clamp(26 * (cov ** 0.5))
        cards = [{'id': 'media', 'level': round(media_lv, 1), 'up': hours_since(main.get('at'), self.t0) < 24 and n_src >= 2,
                  'text': f"{n_src} 家媒體報導" + ('，含官方消息' if official else '') + (f"（{'、'.join(sorted({x['srcname'] for x in stories})[:4])}）"),
                  'url': main.get('u')}] + com_cards
        lv = {'media': media_lv, **com_lv}
        if g and self.summ.get(g.gid):
            gs = self.summ[g.gid]
            for k in ('steam', 'wiki_en', 'wiki_zh', 'gnews', 'trends', 'steam_tw'):
                if gs['levels'].get(k, 0) > 0:
                    lv[k] = max(lv.get(k, 0), gs['levels'][k] * 0.8)
            for c in S.station_cards(g, gs):
                if c['id'] not in {x['id'] for x in cards} and c['id'] != 'media':
                    c = dict(c)
                    c['level'] = round(c['level'] * 0.8, 1)
                    c['text'] = f"（{g.name}）{c['text']}"
                    cards.append(c)
        glob = S.soft_or(lv, NEWS_GLOBAL_W)
        tw = S.soft_or(lv, NEWS_TW_W)
        share = float(self.cfg.get('taiwan_share', 0.45))
        n_rise = sum(1 for c in cards if c.get('up'))
        demand = clamp((1 - share) * glob + share * tw + min(12, 4 * n_rise))
        dwhy = [f'全球回波 {round(glob)}、台灣回波 {round(tw)}'] + ([f'{n_rise} 個測站同步升溫'] if n_rise else [])
        first = min((x['at'] for x in stories if x.get('at')), default=None)
        if kind == 'issue':
            tid = f'issue:{gkey}'
            half = 4
            pillar = 'issue'
        elif kind == 'hardware':
            tid = f'hw:{gkey}'
            half = 5
            pillar = 'hardware'
        else:
            tid = f'show:{gkey}'
            half = 2.5
            pillar = 'season'
        fs = self.first_seen(tid, first)
        timing, ttext = _decay_timing(fs, half, n_rise >= 2, self.t0)
        done = self.done.for_text(zh_title + ' ' + (main.get('t') or ''), None)
        fit, fit_why = _fit(self.cfg, pillar, g if g else None, done=_done_obj(done), tw_level=tw)
        if kind == 'hardware':
            ease, ease_why = _ease(official=official, multi=n_src >= 3, rumor_only=rumor_only, sensitive=sensitive,
                                   extra=[(5, '你手上有 RTX 5090 筆電，可以做實測對照')] if re.search(r'RTX|GeForce|DLSS|顯卡|GPU', text, re.I) else None)
        else:
            ease, ease_why = _ease(official=official, multi=n_src >= 3, rumor_only=rumor_only, sensitive=sensitive,
                                   store=bool(g and g.appid))
        label = self.issue_label.get(all_cats[0]) if all_cats else None
        title = zh_title
        if len(title) > 34:
            title = title[:33] + '…'
        fsd = parse_iso(fs).astimezone(TW).date() if parse_iso(fs) else self.today
        pub = fsd + dt.timedelta(days=3 if kind != 'showcase' else 2)
        t = {'id': tid, 'type': kind, 'pillar': pillar, 'title': title, 'issue': all_cats, 'issue_label': label,
             'game': _game_brief(g) if g else None, 'facts': _stories_facts(stories, 6) + (_game_facts(g)[:3] if g else []),
             'timeline': _timeline(stories),
             'window': {'kind': 'decay', 'open': fsd.isoformat(), 'publish_by': pub.isoformat(),
                        'close': (fsd + dt.timedelta(days=10)).isoformat(), 'text': ttext},
             'ptt': ph, 'reddit': rh, 'flags': (['傳聞'] if rumor_only else []) + (['敏感題材'] if sensitive else []),
             'story_en': main.get('t') if main.get('lang') == 'en' else None,
             'summary': main.get('sz') or main.get('s'), 'done': _done_obj(done),
             'demand_parts': {'global': round(glob), 'tw': round(tw)}, 'first_hint': first}
        return self._finish(t, cards, demand, dwhy, timing, ttext, fit, fit_why, ease, ease_why, w)

    # ---------------- Steam 負評潮
    def review_bombs(self):
        w = self.cfg['weights']
        for g in self.reg.games.values():
            rv = g.reviews or {}
            a, r = rv.get('all') or {}, rv.get('recent') or {}
            if not a.get('pct') or not r.get('pct') or (a.get('n') or 0) < 500:
                continue
            if r['pct'] > a['pct'] - 25 or (r.get('per_day') or 0) < 15:
                continue
            summ = self.summ.get(g.gid)
            if not summ:
                continue
            tid = f'issue:{g.gid}:reviews'
            fs = self.first_seen(tid)
            timing, ttext = _decay_timing(fs, 4, True, self.t0)
            fit, fit_why = _fit(self.cfg, 'issue', g, tw_level=summ['tw'])
            ease, ease_why = _ease(store=True, multi=(g.sig.get('media') or {}).get('n72', 0) >= 2,
                                   extra=[(10, '負評原因直接看 Steam 評論就查得到')])
            cards = S.station_cards(g, summ)
            demand, dwhy, glob, tw = self._demand_from_summary(summ)
            hook = f"最近 {r['n']} 篇只剩 {r['pct']}% 好評（整體 {a['pct']}%）"
            fsd = parse_iso(fs).astimezone(TW).date()
            m = g.sig.get('media') or {}
            t = {'id': tid, 'type': 'issue', 'pillar': 'issue', 'title': f'《{g.name}》評價崩盤：{hook}',
                 'issue': ['backlash'], 'issue_label': '玩家反彈', 'game': _game_brief(g),
                 'facts': _game_facts(g) + _stories_facts(m.get('stories', []), 3), 'timeline': _timeline(m.get('stories', [])),
                 'window': {'kind': 'decay', 'open': fsd.isoformat(), 'publish_by': (fsd + dt.timedelta(days=3)).isoformat(),
                            'close': (fsd + dt.timedelta(days=10)).isoformat(), 'text': ttext},
                 'ptt': (g.sig.get('ptt') or {}).get('posts', [])[:5], 'flags': [], 'hooks': [hook],
                 'demand_parts': {'global': round(glob), 'tw': round(tw)}}
            self.add(self._finish(t, cards, demand + 8, dwhy + ['負評潮通常會帶來大量討論'], timing, ttext, fit, fit_why,
                                  ease, ease_why, w))

    # ---------------- 檔期（固定行事曆＋每月新作＋年底榜單＋每週大事）
    def season(self, cal_cfg, notable_by_month):
        w = self.cfg['weights']
        for ev in cal_cfg['events']:
            specs = ev.get('topics') or ([ev['topic']] if ev.get('topic') else [])
            for spec in specs:
                start = parse_day(ev['start'])
                end = parse_day(ev['end'])
                if not start:
                    continue
                key = spec.get('key', 'main')
                if 'publish_before' in spec:
                    publish_by = start - dt.timedelta(days=spec['publish_before'])
                    open_d = start - dt.timedelta(days=spec.get('open_before', 14))
                    close = start
                else:
                    publish_by = start + dt.timedelta(days=spec.get('publish_after_start', 2))
                    open_d = start - dt.timedelta(days=spec.get('open_before', 7))
                    close = end - dt.timedelta(days=1) if end and end > start else start + dt.timedelta(days=3)
                if self.today > close or self.today < open_d - dt.timedelta(days=10):
                    continue
                start_by = publish_by - dt.timedelta(days=self.lead)
                timing, ttext = _deadline_timing(self.today, open_d, start_by, publish_by, close)
                if timing is None:
                    continue
                tid = f'season:{ev["id"]}:{key}'
                done = self.done.for_event(ev['id']) if key == 'main' else None
                ttype = spec.get('type', 'season')
                pillar = self.cfg['type_pillar'].get(ttype, 'season')
                ev_kw = self._event_keywords(ev)
                d, dwhy, cards = self._keyword_demand(ev_kw, base=56)
                fit, fit_why = _fit(self.cfg, pillar, None, done=_done_obj(done))
                ease, ease_why = _ease(official=bool(ev.get('src')), extra=[(6, '有固定格式可以套（推薦／不推薦）')])
                facts = [{'k': '檔期', 'v': self._event_when(ev), 'src': '官方行事曆' if 'steam' in ev.get('kind', '') else '官方公告',
                          'u': ev.get('src')}]
                if ev.get('note'):
                    facts.append({'k': '備註', 'v': ev['note'], 'src': '', 'u': ev.get('src')})
                t = {'id': tid, 'type': ttype, 'pillar': pillar, 'title': spec['title'], 'event': ev['id'],
                     'event_name': ev['name'], 'facts': facts, 'timeline': [], 'format': spec.get('format'),
                     'window': {'kind': 'deadline', 'open': open_d.isoformat(), 'start_by': start_by.isoformat(),
                                'publish_by': publish_by.isoformat(), 'close': close.isoformat(), 'text': ttext},
                     'flags': [], 'done': _done_obj(done), 'demand_parts': {}}
                self.add(self._finish(t, cards, d, dwhy, timing, ttext, fit, fit_why, ease, ease_why, w))
        # 每月新作推薦
        for rec in cal_cfg.get('recurring', []):
            if rec['id'] == 'month-games':
                self._month_topic(rec, notable_by_month, w)
            elif rec['id'] in ('year-best', 'next-year'):
                self._year_topic(rec, w)
        self._weekly(w)

    def _event_keywords(self, ev):
        name = ev['name']
        kws = []
        if 'nextfest' in ev['id']:
            kws = ['Next Fest', '新品節']
        elif ev.get('kind') == 'steam_sale':
            kws = ['Steam Sale', '特賣', 'Winter Sale', 'Summer Sale', 'Scream', '尖叫節', '萬聖節特賣', '冬季特賣', '夏季特賣', '春季特賣']
        elif 'tga' in ev['id']:
            kws = ['Game Awards', 'TGA', '遊戲大獎', '年度遊戲']
        elif 'tpgs' in ev['id']:
            kws = ['台北電玩展', 'Taipei Game Show', '電玩展']
        elif 'blackfriday' in ev['id']:
            kws = ['Black Friday', '黑色星期五', '黑五']
        return kws or [name]

    def _keyword_demand(self, kws, base=50):
        """非遊戲題目（檔期、發表會）的需求：新聞、PTT、Reddit、台灣新聞裡提到關鍵字的量。"""
        rx = re.compile('|'.join(re.escape(k) for k in kws), re.I)
        st = [s for s in self.stories if hours_since(s.get('up') or s.get('at'), self.t0) <= 96
              and rx.search(' '.join(S.story_texts(s)[:4]))]
        ph, rh = related_posts('', '', self.ptt, self.reddit, self.t0, kw=kws)
        gn = [x for x in self.gnews_items if rx.search(x.get('t') or '') and not S.EXCLUDE_NEWS_SRC.search(x.get('src') or '')
              and hours_since(x.get('at'), self.t0) <= 72]
        cards = []
        lv = {}
        if st:
            n = sum(int(s.get('n') or 1) for s in st)
            lv['media'] = clamp(22 * n ** 0.5)
            top = max(st, key=lambda s: int(s.get('n') or 1))
            cards.append({'id': 'media', 'level': round(lv['media'], 1), 'up': len(st) >= 2,
                          'text': f"{len(st)} 則新聞提到（{(top.get('tz') or top.get('t'))[:28]}）", 'url': top.get('u')})
        c2, l2 = _community_cards(ph, rh)
        cards += c2
        lv.update(l2)
        if gn:
            lv['gnews'] = clamp(18 * len(gn) ** 0.5)
            cards.append({'id': 'gnews', 'level': round(lv['gnews'], 1), 'up': len(gn) >= 4,
                          'text': f"台灣 3 天 {len(gn)} 篇報導（{gn[0]['t'][:26]}）", 'url': gn[0].get('u')})
        glob = S.soft_or(lv, S.GLOBAL_W)
        tw = S.soft_or(lv, S.TW_W)
        share = float(self.cfg.get('taiwan_share', 0.45))
        sig = (1 - share) * glob + share * tw
        d = max(base, sig + min(12, 4 * sum(1 for c in cards if c.get('up'))))
        why = ['這是頻道做過、有固定觀眾的格式（基本盤 {}）'.format(base)]
        if sig > 0:
            why.append(f'相關討論：全球回波 {round(glob)}、台灣回波 {round(tw)}')
        cards.sort(key=lambda c: [x['id'] for x in S.STATIONS].index(c['id']))
        return clamp(d), why, cards

    def _event_when(self, ev):
        s, e = parse_day(ev['start']), parse_day(ev['end'])
        if ev.get('date_only') or 'steam' not in ev.get('kind', ''):
            if e and e != s:
                return f'{s.month}/{s.day}（週{wd(s)}）～{e.month}/{e.day}（週{wd(e)}）'
            return f'{s.month}/{s.day}（週{wd(s)}）'
        st = dt.datetime(s.year, s.month, s.day, 10, tzinfo=PT).astimezone(TPE)
        en = dt.datetime(e.year, e.month, e.day, 10, tzinfo=PT).astimezone(TPE)
        return (f'台灣時間 {st.month}/{st.day}（週{wd(st.date())}）{st.hour:02d}:00 開始，'
                f'{en.month}/{en.day}（週{wd(en.date())}）{en.hour:02d}:00 結束')

    def _month_topic(self, rec, notable_by_month, w):
        today = self.today
        if today.day < rec.get('open_day', 12):
            return
        y, m = (today.year + (today.month // 12), today.month % 12 + 1)
        ym = f'{y}-{m:02d}'
        last = cal.monthrange(today.year, today.month)[1]
        publish_by = dt.date(today.year, today.month, last)
        open_d = dt.date(today.year, today.month, rec.get('open_day', 12))
        close = dt.date(y, m, 7)
        start_by = publish_by - dt.timedelta(days=self.lead)
        timing, ttext = _deadline_timing(today, open_d, start_by, publish_by, close)
        if timing is None:
            return
        done = self.done.for_month(ym)
        games = notable_by_month.get(ym, [])
        kws = [f'{m}月', f'{m} 月']
        d, dwhy, cards = self._keyword_demand([f'{m} 月新作', f'{m}月新作', f'{m} 月遊戲', f'{m}月遊戲'], base=58)
        if games:
            d = max(d, min(85, 50 + 4 * len(games[:8])))
            dwhy.append(f'{len(games)} 款值得注意的新作在 {m} 月發售')
        fit, fit_why = _fit(self.cfg, 'season', None, done=_done_obj(done))
        ease, ease_why = _ease(store=True, extra=[(6, '有固定格式可以套（推薦／不推薦）')])
        facts = [{'k': '本月看點', 'v': '、'.join(f"《{x['name']}》{x['date'][5:].replace('-', '/')}" for x in games[:8]) or '還在統計',
                  'src': 'Steam 即將推出／維基發售表', 'u': None}]
        t = {'id': f'season:month:{ym}', 'type': 'season', 'pillar': 'season', 'title': rec['title'].replace('{m}', str(m)),
             'facts': facts, 'timeline': [], 'format': rec.get('format'), 'games': games[:12],
             'window': {'kind': 'deadline', 'open': open_d.isoformat(), 'start_by': start_by.isoformat(),
                        'publish_by': publish_by.isoformat(), 'close': close.isoformat(), 'text': ttext},
             'flags': [], 'done': _done_obj(done), 'demand_parts': {}}
        self.add(self._finish(t, cards, d, dwhy, timing, ttext, fit, fit_why, ease, ease_why, w))

    def _year_topic(self, rec, w):
        today = self.today
        y = today.year if today.month >= 6 else today.year - 1
        open_d = dt.date(y, 12, rec.get('open_day', 1))
        publish_by = dt.date(y, 12, min(31, rec.get('publish_day', 28)))
        close = publish_by + dt.timedelta(days=rec.get('close_days', 7))
        if today < open_d - dt.timedelta(days=10) or today > close:
            return
        start_by = publish_by - dt.timedelta(days=max(self.lead, 5))
        timing, ttext = _deadline_timing(today, open_d, start_by, publish_by, close)
        if timing is None:
            return
        title = rec['title'].replace('{y}', str(y)).replace('{y1}', str(y + 1))
        d, dwhy, cards = self._keyword_demand(['年度遊戲', 'GOTY', 'Game of the Year', '最期待', 'most anticipated'], base=60)
        fit, fit_why = _fit(self.cfg, 'ranking', None)
        ease, ease_why = _ease(store=True, extra=[(4, '榜單題材料多，但要花時間挑')])
        t = {'id': f'rank:{rec["id"]}:{y}', 'type': 'ranking', 'pillar': 'ranking', 'title': title, 'facts': [],
             'timeline': [], 'format': 'ranking',
             'window': {'kind': 'deadline', 'open': open_d.isoformat(), 'start_by': start_by.isoformat(),
                        'publish_by': publish_by.isoformat(), 'close': close.isoformat(), 'text': ttext},
             'flags': [], 'demand_parts': {}}
        self.add(self._finish(t, cards, d, dwhy, timing, ttext, fit, fit_why, ease, ease_why, w))

    def _weekly(self, w):
        today = self.today
        monday = today - dt.timedelta(days=today.weekday())
        sunday = monday + dt.timedelta(days=6)
        st = [s for s in self.stories if hours_since(s.get('up') or s.get('at'), self.t0) <= 7 * 24
              and s.get('sec') == 'game' and 'media' not in (s.get('soft') or [])]
        st.sort(key=lambda s: (int(s.get('n') or 1) * (1.3 if 'official' in (s.get('flags') or []) else 1), s.get('score') or 0),
                reverse=True)
        top = st[:6]
        if len(top) < 3:
            return
        start_by = sunday - dt.timedelta(days=2)
        timing, ttext = _deadline_timing(today, monday, start_by, sunday, sunday + dt.timedelta(days=1))
        if timing is None:
            return
        cov = sum(int(s.get('n') or 1) for s in top)
        lv = clamp(22 * cov ** 0.5)
        cards = [{'id': 'media', 'level': round(lv, 1), 'up': False,
                  'text': f"本週報導最多的 {len(top)} 件事，合計 {cov} 篇", 'url': top[0].get('u')}]
        d = max(42, lv * 0.6)
        fit, fit_why = _fit(self.cfg, 'issue', None)
        fit -= 15
        fit_why.append('「每週大事」頻道還沒做過，是可以固定化的新單元')
        ease, ease_why = _ease(official=True, multi=True, extra=[(5, '素材都是已整理好的新聞')])
        facts = []
        for s in top:
            src = self.src.get(s.get('src')) or {}
            facts.append({'k': '官方' if 'official' in (s.get('flags') or []) else '新聞', 'v': s.get('tz') or s.get('t'),
                          'src': src.get('name', s.get('src')) + (f"等 {s['n']} 家" if int(s.get('n') or 1) > 1 else ''),
                          'u': s.get('u'), 'at': s.get('at')})
        yw = today.isocalendar()
        t = {'id': f'weekly:{yw[0]}-{yw[1]:02d}', 'type': 'weekly', 'pillar': 'issue',
             'title': f'本週遊戲圈 {len(top)} 件大事（{md(monday)}～{md(sunday)}）', 'facts': facts, 'timeline': [],
             'format': 'roundup',
             'window': {'kind': 'deadline', 'open': monday.isoformat(), 'start_by': start_by.isoformat(),
                        'publish_by': sunday.isoformat(), 'close': (sunday + dt.timedelta(days=1)).isoformat(), 'text': ttext},
             'flags': ['新單元'], 'demand_parts': {}}
        self.add(self._finish(t, cards, d, ['每週固定單元，靠穩定更新累積觀眾'], timing, ttext, fit, fit_why, ease, ease_why, w))
