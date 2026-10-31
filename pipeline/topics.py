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
import math
import pathlib
import re
from zoneinfo import ZoneInfo

from . import signals as S
from .entities import has_cjk
from .entities import key as name_key
from .util import TW, clamp, hours_since, parse_day, parse_iso, sid

ROOT = pathlib.Path(__file__).resolve().parent.parent
PT = ZoneInfo('America/Los_Angeles')
TPE = ZoneInfo('Asia/Taipei')


def load_cfg(name):
    return json.loads((ROOT / 'config' / name).read_text(encoding='utf-8'))


# 英數字的字詞邊界：Python 的 \b 會把中文字當成字的一部分（「新款PS6」裡的 PS6 前面不算邊界），所以自己定義
_ASCII_B = r'(?:(?<=[A-Za-z0-9])(?![A-Za-z0-9])|(?<![A-Za-z0-9])(?=[A-Za-z0-9]))'


def kw_regex(en=(), zh=()):
    """把關鍵字清單編成一個正規表示式。英文詞前後不能接英數字（sue 不會對到 issue）；
    中文詞照原樣比，但開頭或結尾是英數字時也加邊界（「Switch 2」不會對到「Switch 20」）。"""
    parts = []
    for p in en or []:
        parts.append(f'(?<![A-Za-z0-9])(?:{p.replace(chr(92) + "b", _ASCII_B)})(?![A-Za-z0-9])')
    for z in zh or []:
        pre = '(?<![A-Za-z0-9])' if re.match(r'[A-Za-z0-9]', z) else ''
        suf = '(?![A-Za-z0-9])' if re.search(r'[A-Za-z0-9]$', z) else ''
        parts.append(f'{pre}(?:{z.replace(chr(92) + "b", _ASCII_B)}){suf}')
    return re.compile('|'.join(parts), re.I) if parts else None


def ev_days(ev):
    """活動的開始與結束日（台灣日期）。Steam 的活動是太平洋時間早上 10 點開始，台灣已經是隔天凌晨 1 點。"""
    s, e = parse_day(ev.get('start')), parse_day(ev.get('end'))
    if ev.get('date_only') or 'steam' not in (ev.get('kind') or ''):
        return s, e

    def tw(d):
        return dt.datetime(d.year, d.month, d.day, 10, tzinfo=PT).astimezone(TPE).date() if d else None
    return tw(s), tw(e)


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


def shown(score: float) -> int:
    """畫面上顯示的整數分數（四捨五入，跟 App 的 Math.round 一樣）。"""
    return int(math.floor(score + 0.5))


def grade(score: float) -> str:
    """等級照畫面上顯示的整數判斷，才不會出現「58 分卻是觀望」。"""
    s = shown(score)
    if s >= 70:
        return '必拍'
    if s >= 58:
        return '可拍'
    if s >= 46:
        return '觀望'
    return '先跳過'


def _deadline_timing(today, open_d, start_by, publish_by, close):
    """有截止日的題目：回傳 (分數, 一句話)。"""
    if close and today > close:
        return None, '已經過了'
    if open_d and today < open_d:
        days = (open_d - today).days
        if days > 3:
            return 22, f'{days} 天後才開始熱（{md(open_d)} 起）'
        # 再幾天就開始熱：照開工日算，但還沒熱起來，最多給 60，免得前一天 22、隔天突然跳到 78
        tm, _ = _deadline_timing(today, None, start_by, publish_by, close)
        return min(60, tm if tm is not None else 22), f'{days} 天後開始熱（{md(open_d)} 起），現在可以先準備'
    d = (start_by - today).days if start_by else 99
    if publish_by and today > publish_by:
        return 45, f'最佳上架日 {md(publish_by)} 已過，現在補拍還來得及到 {md(close)}'
    if d < 0:
        left = (publish_by - today).days
        if left <= 0:
            return 72, '今天就是最後上架日，趕不上就換成下一階段的角度'
        return 88, f'時間很趕：最晚 {md(publish_by)}（週{wd(publish_by)}）要上架，只剩 {left} 天'
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


def _decay_anchor(first_seen, rising: bool, today):
    """話題型題目（爆紅、負評潮、新聞）的保鮮期從哪天起算。
    平常從第一次出現那天算；還在升溫的話最早只算到昨天，不會出現「還在升溫中」卻「補拍期已過」。"""
    d = parse_iso(first_seen).astimezone(TW).date() if parse_iso(first_seen) else today
    if rising:
        d = max(d, today - dt.timedelta(days=1))
    return d


REAPPEAR_H = 72  # 題目消失超過這麼多小時又出現，就當成新的一波

_F2P_CATS = re.compile(r'應用程式內購買|In-App Purchases', re.I)
_GACHA_HINT = re.compile(r'gacha|抽卡|轉蛋|卡池|角色池|二次元', re.I)


# Steam 把工具軟體也放在商店裡（桌布、剪輯、建模），有的還標成 game（Wallpaper Engine）：這些不當成遊戲題目。
# 看分類代號（Steam 的軟體分類是 50～60）；舊快取沒有代號就看名稱（照 Steam 繁中商店的寫法：Wallpaper Engine 是
# 「動畫和建模、設計和繪圖、圖像編輯」，OBS 是「工具、影片製作」）。同時有動作、角色扮演這類遊戲分類的（教育遊戲）照樣算遊戲。
_SOFTWARE_GENRE_IDS = {str(i) for i in range(50, 61)}
_SOFTWARE_GENRES = {'動畫和建模', '設計和繪圖', '圖像編輯', '工具', '影片製作', '音訊製作', '音效製作', '網頁發佈', '網頁發布',
                    '軟體訓練', '教育', '會計', '遊戲開發', 'Utilities', 'Animation & Modeling', 'Design & Illustration',
                    'Video Production', 'Audio Production', 'Photo Editing', 'Web Publishing', 'Software Training',
                    'Education', 'Accounting', 'Game Development'}
_GAME_GENRE_IDS = {'1', '2', '3', '9', '18', '25', '28', '29'}
_GAME_GENRES = {'動作', '策略', '角色扮演', '競速', '運動', '冒險', '模擬', '大型多人連線', 'Action', 'Strategy', 'RPG',
                'Racing', 'Sports', 'Adventure', 'Simulation', 'Massively Multiplayer'}


def is_software(g) -> bool:
    det = (g.details or {}) if g else {}
    ids, names = set(det.get('genre_ids') or []), set(det.get('genres') or [])
    soft = bool(ids & _SOFTWARE_GENRE_IDS or names & _SOFTWARE_GENRES)
    return soft and not (ids & _GAME_GENRE_IDS or names & _GAME_GENRES)


def _plain_name(s: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[®™©]', '', s or '')).strip()


def same_series(names, launched) -> bool:
    """names（老遊戲的中英文名）是不是剛上市新作名稱的開頭：《決勝時刻》之於《決勝時刻：現代戰爭 4》、
    Call of Duty 之於 Call of Duty: Modern Warfare 4。英文要整個字對上（Rust 不算 Rusty Lake 的系列）。"""
    for n in (_plain_name(x) for x in names if x):
        if len(n) < 2 or (n.isascii() and len(n) < 4):
            continue
        pat = re.compile(re.escape(n) + r'(?![A-Za-z0-9])', re.I)
        if any(pat.match(_plain_name(x)) for x in launched if x):
            return True
    return False


def is_live_f2p(g) -> bool:
    """免費遊玩＋內購（多半是抽卡或長期營運型），頻道不做這類宣傳。"""
    det = (g.details or {}) if g else {}
    if not det:
        return False
    cats = ' '.join(det.get('cats') or [])
    return bool(det.get('free') and (_F2P_CATS.search(cats) or _GACHA_HINT.search(det.get('desc') or '')))


def _fit(cfg, pillar: str, g=None, *, platforms=None, done=None, covered=None, tw_level=0.0, prev=None):
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
    elif prev:
        fit += 3
        why.append(f"頻道 {prev['date']} 做過《{prev['title']}》，可以當續集，舊觀眾會回來看")
    # 關注清單的加分只在 App 端算一次（方向設定改了馬上生效），這裡不加，免得同步後加兩次
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


def _outside(t: str, sep: str) -> list[str]:
    """只在全形括號外面切開：括號裡（遊戲名、漲跌幅）的冒號與頓號不算。"""
    out, cur, depth = [], [], 0
    for ch in t:
        if ch == '（':
            depth += 1
        elif ch == '）':
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            out.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append(''.join(cur))
    return out


def _why_now(cards: list[dict], timing_text: str, limit=2) -> str:
    own = [c for c in cards if not (c.get('text') or '').startswith('（')]
    rest = [c for c in cards if (c.get('text') or '').startswith('（')]
    picks = (sorted(own, key=lambda c: (c.get('up', False), c.get('level', 0)), reverse=True)
             + sorted(rest, key=lambda c: c.get('level', 0), reverse=True))[:limit]
    def short(c):
        # 括號裡的媒體名單、新聞標題拿掉（太長，切開還會剩「（Engadget、Game Developer」這種半個括號）；
        # 漲跌幅「（比上週 +55%）」是升溫的證據要留著，開頭的（遊戲名）是在說這是哪款遊戲的數字也留著
        t = re.sub(r'(?<=.)（[^（）]*）', lambda m: m.group() if '%' in m.group() else '', c['text'])
        if c['id'] in ('gnews', 'reddit', 'ptt'):
            t = _outside(t, '：')[0]  # 冒號後面是文章標題
        t = re.sub(r'，最熱$', '', t)
        return '、'.join(_outside(t, '、')[:2])
    parts = [short(c) for c in picks]
    parts = [p for p in parts if p]
    s = '；'.join(parts)
    if timing_text:
        s = (s + '。' if s else '') + timing_text
    return s


_GPU_TAIL = re.compile(r'\s{2,}|\s(?:網路|儲存空間|音效卡|DirectX|其他|附註|額外|Network|Storage|Sound|Additional)\b.*$')


def _gpu(text):
    if not text:
        return None
    t = re.sub(r'[®™©]', '', text)
    return _GPU_TAIL.split(t)[0].strip(' /，,') or None


def _game_brief(g) -> dict:
    det = g.details or {}
    rv = g.reviews or {}
    langs = det.get('langs') or {}
    plat = sorted(g.plat | ({'pc'} if g.appid else set()))
    return {
        'gid': g.gid, 'name': g.name, 'zh': g.zh, 'en': g.en, 'appid': g.appid, 'store': g.store_url,
        # 其他叫法（MW4、現代戰爭 4…）：App 的關注清單用它來比對
        'aliases': sorted(a for a in g.aliases if a and a not in (g.zh, g.en))[:8],
        'img': det.get('img') or g.img or (f'https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{g.appid}/header.jpg' if g.appid else None),
        'release': g.release, 'release_prec': g.release_prec, 'release_text': g.release_text or det.get('date_text'),
        'plat': plat, 'tc': langs.get('tc'), 'tc_voice': langs.get('tc_voice'), 'sc': langs.get('sc'),
        'price': det.get('price'), 'price_text': det.get('price_text'), 'discount': det.get('discount'),
        'genres': det.get('genres') or [], 'dev': det.get('dev') or [], 'pub': det.get('pub') or [],
        'desc': det.get('desc'), 'gpu': _gpu(det.get('gpu')), 'movies': det.get('movies'), 'demo': det.get('demo'),
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
        v = f"{rv.get('desc') or ''} {rv.get('pct')}%（{S.unit(rv['n'], '篇')}）"
        rc = b['recent'] or {}
        if rc.get('n'):
            v += f"；最近 {rc['n']} 篇 {rc['pct']}%"
        facts.append({'k': 'Steam 評價', 'v': v.strip(), 'src': 'Steam 評論統計', 'u': store})
    tc = b['tc_reviews'] or {}
    if tc.get('n'):
        facts.append({'k': '繁中玩家評價', 'v': f"{tc.get('pct')}%（{S.unit(tc['n'], '篇繁中評論')}）",
                      'src': 'Steam 評論統計（繁體中文）', 'u': store})
    p = b['players'] or {}
    if p.get('now'):
        v = S.unit(p['now'], '人')
        if p.get('peak14'):
            v += f"（14 天內高點 {S.unit(p['peak14'], '人')}）"
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


_PRODUCTS = [(re.compile(p, re.I), name) for p, name in [
    (r'(?<![A-Za-z0-9])PS6(?![A-Za-z0-9])|PlayStation 6', 'PS6'), (r'PS5 Pro', 'PS5 Pro'),
    (r'Switch 2(?![0-9])', 'Switch 2'), (r'Steam Machine', 'Steam Machine'), (r'Steam Deck', 'Steam Deck'),
    (r'Steam Frame', 'Steam Frame'), (r'Xbox Ally|ROG Ally', 'ROG Xbox Ally'), (r'Legion Go', 'Legion Go'),
    (r'next[- ]gen Xbox|次世代 ?Xbox', '次世代 Xbox'), (r'RTX ?50\d0', 'RTX 50 系列'), (r'RTX ?40\d0', 'RTX 40 系列'),
    (r'Radeon|(?<![A-Za-z0-9])RX ?9\d{3}', 'Radeon'), (r'(?<![A-Za-z0-9])DLSS(?: ?\d)?(?![A-Za-z0-9])', 'DLSS'),
    (r'(?<![A-Za-z0-9])FSR(?: ?\d)?(?![A-Za-z0-9])', 'FSR'),
    (r'(?<![A-Za-z0-9])DRAM(?![A-Za-z0-9])|記憶體.{0,4}(?:漲|價)|memory price|RAM price', '記憶體價格'),
]]


def _product(title: str):
    """標題的主角是哪個硬體：要出現在標題前段才算（「外媒憂淪下個 Steam Machine」的主角不是 Steam Machine）。"""
    best = None
    for rx, name in _PRODUCTS:
        m = rx.search(title or '')
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), name)
    if not best or best[0] > max(12, len(title) * 0.4):
        return None
    return best[1]


_HW_FACETS = [
    # 價格要有「價格」這類字，或「付／花＋金額」；單純出現金額（「價值 47 萬美元的貨被偷」）不算
    ('price', re.compile(r'價格|售價|定價|單價|價錢|漲價|降價|調價|要價|(?:支付|付|花).{0,6}(?:\$|美元|台幣|元)'
                         r'|(?<![A-Za-z0-9])(?:prices?|pricing|priced|price tag)(?![A-Za-z0-9])|(?<![A-Za-z0-9])pay \$', re.I)),
    ('sales', re.compile(r'銷量|銷售|出貨|賣出|(?<![A-Za-z0-9])(?:sold|sales|units|shipped|shipments?)(?![A-Za-z0-9])', re.I)),
    ('spec', re.compile(r'規格|效能|跑分|(?<![A-Za-z0-9])(?:specs?|specifications?|benchmarks?|leaks?)(?![A-Za-z0-9])', re.I)),
]


def _hw_facet(title: str):
    """硬體新聞在講哪個面向：價格、銷量、規格。同一個產品、同一個面向的新聞才合成一題。"""
    for name, rx in _HW_FACETS:
        if rx.search(title or ''):
            return name
    return None


def _merge_similar(groups: dict, products: dict) -> dict:
    """同一件事不同家報導、玩真的沒合併到的新聞（沒對到遊戲的爭議、硬體新聞），標題很像就合成一個題目。
    硬體新聞要主角是同一個產品，而且標題很像、或講的是同一個面向（例如都在講 PS6 的價格）才合併；
    不會把「RTX 5090 貨運被偷」跟「RTX 5090 降價」合在一起。"""
    keys = [k for k in groups if (k[0] == 'issue' and not k[1].startswith(('app:', 'w:'))) or k[0] == 'hardware']
    merged = {}
    used = set()
    for i, a in enumerate(keys):
        if a in used:
            continue
        sa = groups[a][0][0]
        for b in keys[i + 1:]:
            if b in used or b[0] != a[0]:
                continue
            if a[0] == 'hardware' and products.get(a) != products.get(b):
                continue
            sb = groups[b][0][0]
            same_facet = (a[0] == 'hardware' and products.get(a) is not None
                          and _hw_facet(sa.get('tz') or sa.get('t')) is not None
                          and _hw_facet(sa.get('tz') or sa.get('t')) == _hw_facet(sb.get('tz') or sb.get('t')))
            if same_facet or similar(sa.get('tz') or '', sb.get('tz') or '', True) >= 0.42 or \
                    similar(sa.get('t') or '', sb.get('t') or '', False) >= 0.5:
                groups[a] = groups[a] + groups[b]
                used.add(b)
        merged[a] = groups[a]
    for k, v in groups.items():
        if k not in used and k not in merged:
            merged[k] = v
    return merged


# ------------------------------------------------------------------ 已經做過？
def _video_day(s):
    """影片日期：「2026-10-07」或只寫到月份的「2026-07」（當成那個月的最後一天）。"""
    if not s:
        return None
    d = parse_day(s) if len(s) >= 10 else None
    if d:
        return d
    m = re.fullmatch(r'(\d{4})-(\d{2})', s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return dt.date(y, mo, cal.monthrange(y, mo)[1])
    return None


class Done:
    """頻道做過或正在做的影片。

    檔期（events）與月份（months）是一對一的，永遠算數；關鍵字（keys）比對新聞標題時，
    只有製作中或最近 30 天內上片的影片才算「已經做過」。更早的只當作「可以做續集」的參考，不扣分。
    """
    RECENT_DAYS = 30

    def __init__(self, videos: list[dict], today=None):
        self.videos = videos
        self.today = today
        self._rx = {}
        for v in videos:
            keys = (v.get('match') or {}).get('keys') or []
            self._rx[v['id']] = kw_regex(zh=[re.escape(k) for k in keys])

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

    def _recent(self, v):
        if v.get('status') == 'making' or not self.today:
            return True
        d = _video_day(v.get('date'))
        return d is None or (self.today - d).days <= self.RECENT_DAYS

    def _hits(self, text, pillar=None, head=False):
        """text 可以用換行分開好幾個標題（中文標題、英文標題）。head=True 時關鍵字要出現在某個標題的前段（是主角）。"""
        parts = [p for p in (text or '').split('\n') if p.strip()]
        for v in self.videos:
            rx = self._rx.get(v['id'])
            if not rx or (pillar is not None and v.get('pillar') != pillar):
                continue
            for p in parts:
                m = rx.search(p)
                if m and (not head or m.start() <= max(12, len(p) * 0.4)):
                    yield v
                    break

    def for_text(self, text, pillar=None):
        """製作中或最近上片的影片：新聞裡任何地方提到都算（正在做的題目，相關新聞都該併進那支影片）。"""
        return next((v for v in self._hits(text, pillar) if self._recent(v)), None)

    def older_for_text(self, text, pillar=None):
        """比較早的影片：新聞的主角就是它，才提示「可以做續集」。"""
        return next((v for v in self._hits(text, pillar, head=True) if not self._recent(v) and v.get('status') == 'done'), None)


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
    """兩個標題有多像（重疊的詞 ÷ 比較短那個標題的詞數）。

    太短的標題（例如「[閒聊] 漲價」）只要跟任何含「漲價」的新聞重疊一個詞就會變成 100%，
    所以比較短的那個至少要有 5 組（英文 3 個）詞，而且至少重疊 3 組（英文 2 個）才算。"""
    if zh:
        x, y = _bigrams(a), _bigrams(b)
    else:
        x, y = _words(a), _words(b)
    if not x or not y:
        return 0.0
    small = min(len(x), len(y))
    inter = len(x & y)
    if small < (5 if zh else 3) or inter < (3 if zh else 2):
        return 0.0
    return inter / small


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
                      'text': f"PTT {len(ptt_hits)} 篇在討論，最熱：{top['t'][:30]}（{push}）", 'url': top['u']})
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
        self.done = Done(videos, t0.astimezone(TW).date())
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
        # 新聞題的代號：上一輪的題目只要這輪還有同一篇報導，就沿用同一個代號
        # （報導一篇篇過期，題目代號也不會跟著變；靜音、想拍、角度、筆記才不會掉）
        self.story_tid = {}
        for tid, rec in sorted(seen.items(), key=lambda kv: kv[1].get('first') or ''):
            for x in rec.get('stories') or []:
                self.story_tid.setdefault(x, tid)
        self._claimed = set()
        self._compile_kw()

    # ---------------- 關鍵字
    def _compile_kw(self):
        comp = kw_regex
        order = self.kw.get('issue_priority') or list(self.kw['issue'])
        # 越具體的分類越前面（「下架」先於泛泛的「爭議」），題目的切角照第一個分類走
        cats = [k for k in order if k in self.kw['issue']] + [k for k in self.kw['issue'] if k not in order]
        self.issue_re = {k: comp(self.kw['issue'][k].get('en', []), self.kw['issue'][k].get('zh', [])) for k in cats}
        self.issue_label = {k: v['label'] for k, v in self.kw['issue'].items()}
        self.sens_re = comp(self.kw['sensitive']['en'], self.kw['sensitive']['zh'])
        hw = self.kw['hardware']
        self.hw_strong = comp(hw['strong_en'], hw['strong_zh'])
        self.hw_platform = comp(hw['platform_en'], hw['platform_zh'])
        self.hw_context = comp(hw['context_en'], hw['context_zh'])
        self.show_re = comp(self.kw['showcase']['en'], self.kw['showcase']['zh'])
        self.gaming_re = comp(self.kw['gaming']['en'], self.kw['gaming']['zh'])
        self.tech_src = set(self.kw.get('general_tech_sources') or [])

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
        # 消失超過 3 天又回來的題目是新的一波（例如同一款遊戲第二次負評潮），從這一輪重新算
        if rec and hours_since(rec.get('last'), self.t0) <= REAPPEAR_H:
            return rec['first']
        if rec:
            return self.t0.strftime('%Y-%m-%dT%H:%M:%SZ')
        return fallback or self.t0.strftime('%Y-%m-%dT%H:%M:%SZ')

    def add(self, t: dict):
        if not t:
            return
        old = self.topics.get(t['id'])
        if old and old.get('score', 0) >= t.get('score', 0):
            return
        self.topics[t['id']] = t

    def _finish(self, t: dict, cards, demand, demand_why, timing, timing_text, fit, fit_why, ease, ease_why, w):
        t['scores'] = {'demand': round(clamp(demand), 1), 'timing': round(clamp(timing), 1), 'fit': round(clamp(fit), 1),
                       'ease': round(clamp(ease), 1)}
        raw = total(t['scores'], w)
        t['score'] = round(raw, 1)
        t['total'] = raw  # 沒四捨五入的總分：TOP 5 的門檻與排序用它，才會跟 App 一模一樣（score 只是給人看的）
        t['grade'] = grade(raw)
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
            notable = (g.ranks.get('wishlist', 999) <= 80 or g.ranks.get('upcoming', 999) <= 12 or g.big
                       or g.ranks.get('topsell', 999) <= 40 or g.ranks.get('topsell_tw', 999) <= 25
                       or g.ranks.get('mostplayed', 999) <= 100 or summ['levels'].get('media', 0) >= 40
                       or summ['global'] >= 50 or summ['tw'] >= 40 or g.watch)
            if days < 0:
                notable = notable and (summ['global'] >= 30 or summ['tw'] >= 30 or g.big)
            if not notable:
                continue
            if not (g.zh or g.en) or (g.details and (g.details.get('type') not in (None, 'game') or is_software(g))):
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
            fit, fit_why = _fit(self.cfg, 'spotlight', g, covered=_done_obj(covered), tw_level=summ['tw'])
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
        # 已經上市的新作（新作首發題）：同系列老遊戲這時候在線暴增，多半是新作帶起來的
        # （《現代戰爭 4》要從《決勝時刻》主程式進去玩），那是新作的熱度，不算老遊戲又紅
        launched = [x for t in self.topics.values() if t.get('type') == 'launch' and t.get('phase') != 'pre'
                    for x in ((t.get('game') or {}).get('zh'), (t.get('game') or {}).get('en'))]
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
            age = (self.today - rd).days if rd else None
            recent_release = bool(age is not None and age <= 90)
            # 「熱門新品」榜上剛發售一個月內的才算黑馬；上市一個多月還在榜上，多半是特價或大作本來就賣得好
            fresh = bool(age is not None and age <= 30)
            if age is not None:
                old = age > 180
            else:
                yr = int(g.release[:4]) if (g.release or '')[:4].isdigit() else None
                old = bool(yr and yr < self.today.year - 1)
            under_covered = summ['levels'].get('media', 0) < 30
            if p.get('now', 0) >= 3000 and g7 is not None and g7 >= 0.5 and p.get('points', 0) >= 24 * 5:
                reasons.append(f"同時在線一週 {S.pct(g7)}")
            elif p.get('now', 0) >= 5000 and g1 is not None and g1 >= 0.6 and p.get('points', 0) >= 20:
                reasons.append(f"同時在線一天 {S.pct(g1)}")
            tr = g.ranks.get('trending')
            if tr and tr <= 10 and under_covered and fresh:
                reasons.append(f'Steam 熱門新品第 {tr}，但媒體還很少報')
            ts = g.ranks.get('topsell_tw')
            if ts and ts <= 12 and (g.ranks.get('topsell') or 999) > 60 and recent_release:
                reasons.append(f"台灣區熱銷第 {ts}，比全球（{'百名外' if not g.ranks.get('topsell') else '第 ' + str(g.ranks['topsell'])}）熱很多")
            wz = g.sig.get('wiki_en') or {}
            if (wz.get('spike') or 0) >= 2.5 and (wz.get('avg7') or 0) >= 1500:
                reasons.append(f"英文維基瀏覽突然暴增 {wz['spike']:.1f} 倍")
            # 一定要有商店資料確認是遊戲、而且有正式名稱：只有 appid（名字是「App 123」）或軟體（Wallpaper Engine 這類工具）不算
            if not g.details or not (g.zh or g.en) or g.details.get('type') != 'game' or is_software(g) or is_live_f2p(g):
                continue
            if not reasons:
                continue
            if g.appid in self._EVERGREEN and not (g7 and g7 >= 0.5):
                continue
            if same_series((g.zh, g.en), launched):
                continue
            tid = f'sleeper:{g.gid}'
            fs = self.first_seen(tid)
            timing, ttext = _decay_timing(fs, 6, bool(summ['rising']), self.t0)
            fsd = _decay_anchor(fs, bool(summ['rising']), self.today)
            fit, fit_why = _fit(self.cfg, 'spotlight', g, tw_level=summ['tw'])
            det = g.details or {}
            m = g.sig.get('media') or {}
            ease, ease_why = _ease(store=True, multi=(m.get('n72', 0) >= 3), trailer=(det.get('movies') or 0) > 0,
                                   demo=det.get('demo'))
            cards = S.station_cards(g, summ)
            demand, dwhy, glob, tw = self._demand_from_summary(summ)
            dwhy = reasons + dwhy
            # 上市半年以上的老遊戲是「又紅起來」，不是「突然爆紅」
            title = f'《{g.name}》又紅起來了：{reasons[0]}' if old else f'《{g.name}》突然爆紅：{reasons[0]}'
            t = {'id': tid, 'type': 'sleeper', 'pillar': 'spotlight', 'title': title, 'game': _game_brief(g),
                 'phase': 'old' if old else 'new',
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
        products = {}
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
            main_title = ' '.join(x for x in (s.get('t'), s.get('tz')) if x)
            # 綜合科技媒體（Tom's Hardware 等）放在遊戲區的文章，標題要真的跟遊戲有關才算
            in_game_sec = s.get('sec') == 'game' and s.get('src') not in self.tech_src
            gaming = in_game_sec or bool(gids) or bool(self.gaming_re and self.gaming_re.search(main_title))
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
                products[(kind, gkey)] = _product(s.get('tz') or s.get('t') or '')
            elif show and (n >= 2 or official):
                # 同一場發表會的多篇報導合成一題（「任天堂直面會」「Nintendo Direct」算同一場）
                kind = 'showcase'
                gkey = 'sc:' + self._show_name(title_text) + ':' + self._week_of(s.get('at'))
            else:
                continue
            groups.setdefault((kind, gkey), []).append((s, cats, gids, text))
        groups = _merge_similar(groups, products)
        for (kind, gkey), items in groups.items():
            t = self._news_topic(kind, gkey, items, w, products.get((kind, gkey)))
            if t:
                self.add(t)
        self._dedupe_reviews()

    _SHOW_ALIAS = {'任天堂直面會': 'nintendo direct', '直面會': 'nintendo direct', '遊戲大獎': 'game awards'}

    def _show_name(self, text):
        m = self.show_re.search(text or '') if self.show_re else None
        name = (m.group(0) if m else 'show').strip().lower()
        return self._SHOW_ALIAS.get(name, name)

    def _week_of(self, at):
        d = parse_iso(at) if at else None
        d = d.astimezone(TW).date() if d else self.today
        y, wk, _ = d.isocalendar()
        return f'{y}w{wk:02d}'

    def _dedupe_reviews(self):
        """同一款遊戲同時有「Steam 負評潮」和「玩家反彈」新聞題時只留一題：保留分數高的，把另一題的新聞與討論併進來。"""
        for tid in [k for k in self.topics if k.endswith(':reviews')]:
            rv = self.topics.get(tid)
            gid = (rv.get('game') or {}).get('gid') if rv else None
            if not gid or f'issue:{gid}:backlash' not in self.topics:
                continue
            # 只合併同一款遊戲的「玩家反彈」新聞題；裁員、延期這類別的事件照樣各自成題
            for other in [f'issue:{gid}:backlash']:
                ot = self.topics[other]
                keep, drop = (rv, ot) if rv['score'] >= ot['score'] else (ot, rv)
                seen_f = {(f.get('k'), f.get('v')) for f in keep.get('facts') or []}
                keep['facts'] = (keep.get('facts') or []) + [f for f in drop.get('facts') or [] if (f.get('k'), f.get('v')) not in seen_f]
                seen_t = {(x.get('at'), x.get('t')) for x in keep.get('timeline') or []}
                keep['timeline'] = sorted((keep.get('timeline') or []) + [x for x in drop.get('timeline') or []
                                                                          if (x.get('at'), x.get('t')) not in seen_t],
                                          key=lambda x: x.get('at') or '')[-8:]
                for k2 in ('ptt', 'reddit'):
                    if not keep.get(k2) and drop.get(k2):
                        keep[k2] = drop[k2]
                ids = {c['id'] for c in keep.get('stations') or []}
                keep['stations'] = (keep.get('stations') or []) + [c for c in drop.get('stations') or [] if c['id'] not in ids]
                del self.topics[drop['id']]
                if drop is rv:
                    break
                rv = keep

    def _big(self, gid) -> bool:
        g = self.reg.games.get(gid)
        if not g:
            return False
        sm = self.summ.get(gid) or {}
        return g.big or g.ranks.get('wishlist', 999) <= 50 or g.ranks.get('topsell', 999) <= 30 or sm.get('global', 0) >= 55

    def _news_topic(self, kind, gkey, items, w, product=None):
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
        # 題目代號：對到遊戲的爭議題用遊戲＋分類；其他的沿用上一輪同一批報導的代號，沒有才用最早那篇報導新編一個
        sids = [x[0]['id'] for x in items]
        anchor = min((x[0] for x in items), key=lambda s: s.get('at') or '9999')['id']
        prefix = {'issue': 'issue:', 'hardware': 'hw:'}.get(kind, 'show:')
        reuse = next((self.story_tid[x] for x in sids if x in self.story_tid
                      and self.story_tid[x].startswith(prefix) and self.story_tid[x] not in self._claimed), None)
        if kind == 'issue':
            if gkey.startswith(('app:', 'w:')):
                tid = f'issue:{gkey}'
            else:
                tid = reuse or f'issue:{anchor}:{gkey.rsplit(":", 1)[-1]}'
            half = 4
            pillar = 'issue'
        elif kind == 'hardware':
            tid = reuse or f'hw:{anchor}'
            half = 5
            pillar = 'hardware'
        else:
            tid = reuse or f'show:{gkey}'
            half = 2.5
            pillar = 'season'
        self._claimed.add(tid)
        fs = self.first_seen(tid, first)
        timing, ttext = _decay_timing(fs, half, n_rise >= 2, self.t0)
        done_text = zh_title + '\n' + (main.get('t') or '')
        done = self.done.for_text(done_text, None)
        prev = None if done else self.done.older_for_text(done_text, None)
        fit, fit_why = _fit(self.cfg, pillar, g if g else None, done=_done_obj(done), tw_level=tw, prev=_done_obj(prev))
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
        fsd = _decay_anchor(fs, n_rise >= 2, self.today)
        pub = fsd + dt.timedelta(days=3 if kind != 'showcase' else 2)
        t = {'id': tid, 'type': kind, 'pillar': pillar, 'title': title, 'issue': all_cats, 'issue_label': label,
             'product': product if kind == 'hardware' else None,
             'game': _game_brief(g) if g else None, 'facts': _stories_facts(stories, 6) + (_game_facts(g)[:3] if g else []),
             'timeline': _timeline(stories),
             'window': {'kind': 'decay', 'open': fsd.isoformat(), 'publish_by': pub.isoformat(),
                        'close': (fsd + dt.timedelta(days=10)).isoformat(), 'text': ttext},
             'ptt': ph, 'reddit': rh, 'flags': (['傳聞'] if rumor_only else []) + (['敏感題材'] if sensitive else []),
             'story_en': main.get('t') if main.get('lang') == 'en' else None,
             'summary': main.get('sz') or main.get('s'), 'done': _done_obj(done),
             'demand_parts': {'global': round(glob), 'tw': round(tw)}, 'first_hint': first, 'story_ids': sids}
        return self._finish(t, cards, demand, dwhy, timing, ttext, fit, fit_why, ease, ease_why, w)

    # ---------------- Steam 負評潮
    def review_bombs(self):
        w = self.cfg['weights']
        for g in self.reg.games.values():
            rv = g.reviews or {}
            # 評論快取超過 12 小時沒更新就不拿來判斷負評潮（舊數字不能當成現在的狀況）
            if hours_since(rv.get('at'), self.t0) > 12:
                continue
            a, r = rv.get('all') or {}, rv.get('recent') or {}
            if a.get('pct') is None or r.get('pct') is None or (a.get('n') or 0) < 500:
                continue
            if r['pct'] > a['pct'] - 25 or (r.get('per_day') or 0) < 15:
                continue
            summ = self.summ.get(g.gid)
            if not summ:
                continue
            tid = f'issue:{g.gid}:reviews'
            fs = self.first_seen(tid)
            # 還在升溫：有測站同步升溫，或最近 100 篇是兩天內湧進來的
            rising = bool(summ.get('rising')) or (r.get('span_d') or 99) <= 2
            timing, ttext = _decay_timing(fs, 4, rising, self.t0)
            fit, fit_why = _fit(self.cfg, 'issue', g, tw_level=summ['tw'])
            ease, ease_why = _ease(store=True, multi=(g.sig.get('media') or {}).get('n72', 0) >= 2,
                                   extra=[(10, '負評原因直接看 Steam 評論就查得到')])
            cards = S.station_cards(g, summ)
            demand, dwhy, glob, tw = self._demand_from_summary(summ)
            hook = f"最近 {r['n']} 篇只剩 {r['pct']}% 好評（整體 {a['pct']}%）"
            fsd = _decay_anchor(fs, rising, self.today)
            m = g.sig.get('media') or {}
            t = {'id': tid, 'type': 'issue', 'pillar': 'issue', 'title': f'《{g.name}》評價崩盤：{hook}',
                 'issue': ['backlash'], 'issue_label': '玩家反彈', 'game': _game_brief(g),
                 'facts': _game_facts(g) + _stories_facts(m.get('stories', []), 3), 'timeline': _timeline(m.get('stories', [])),
                 'window': {'kind': 'decay', 'open': fsd.isoformat(), 'publish_by': (fsd + dt.timedelta(days=3)).isoformat(),
                            'close': (fsd + dt.timedelta(days=10)).isoformat(), 'text': ttext},
                 'ptt': (g.sig.get('ptt') or {}).get('posts', [])[:5], 'flags': [], 'hooks': [hook],
                 'demand_parts': {'global': round(glob), 'tw': round(tw)}}
            self.add(self._finish(t, cards, clamp(demand + 8), dwhy + ['負評潮通常會帶來大量討論'], timing, ttext, fit, fit_why,
                                  ease, ease_why, w))

    # ---------------- 檔期（固定行事曆＋每月新作＋年底榜單＋每週大事）
    def season(self, cal_cfg, notable_by_month):
        w = self.cfg['weights']
        for ev in cal_cfg['events']:
            specs = ev.get('topics') or ([ev['topic']] if ev.get('topic') else [])
            for spec in specs:
                start, end = ev_days(ev)
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
                        'publish_by': publish_by.isoformat(), 'close': close.isoformat(), 'text': ttext, 'lead_min': 5},
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
        # 週報素材都是整理好的新聞，製作天數最多抓 2 天（App 端用 lead_max 照同一套規則重算）
        start_by = sunday - dt.timedelta(days=min(self.lead, 2))
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
                        'publish_by': sunday.isoformat(), 'close': (sunday + dt.timedelta(days=1)).isoformat(), 'text': ttext,
                        'lead_max': 2},
             'flags': ['新單元'], 'demand_parts': {}}
        self.add(self._finish(t, cards, d, ['每週固定單元，靠穩定更新累積觀眾'], timing, ttext, fit, fit_why, ease, ease_why, w))
