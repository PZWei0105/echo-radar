"""PTT 測站：台灣玩家在 PTT 遊戲相關看板討論什麼、推文熱不熱。

PTT 官方網站擋掉 GitHub 伺服器（Cloudflare 驗證），所以改讀 PTT 網頁版鏡像站 pttweb.cc 的看板列表：
每篇只取標題、推文數、發文時間；App 裡的連結一律指回 PTT 原文（www.ptt.cc）。
鏡像站的資料放在頁面的 __NUXT__ 區塊（一段 JavaScript 函式），這裡用小型解析器讀出字面值，不執行任何程式碼。
"""
import re
import time

from .util import iso, now

MIRROR = 'https://www.pttweb.cc/bbs/{board}'
PTT_URL = 'https://www.ptt.cc/bbs/{board}/{aid}.html'
BOARDS = {
    'Steam': 'Steam 板', 'PlayStation': 'PlayStation 板', 'NSwitch': 'NSwitch 板',
    'XBOX': 'XBOX 板', 'C_Chat': 'C_Chat 板', 'PC_Shopping': 'PC_Shopping 板',
}
KEEP_DAYS = 7
_TAG = re.compile(r'^\s*(?:Re:\s*|Fw:\s*)?\[([^\]]{1,6})\]\s*')


# ---------------------------------------------------------------- __NUXT__ 解析
class _Tok:
    """把 JavaScript 字面值切成 token：字串、數字、識別字、標點。"""
    _re = re.compile(r'''\s*(?:
        (?P<str>"(?:[^"\\]|\\.)*")|
        (?P<num>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)|
        (?P<id>[A-Za-z_$][A-Za-z0-9_$]*)|
        (?P<p>[{}\[\](),:;=.]))''', re.X)

    def __init__(self, s):
        self.s, self.i = s, 0

    def next(self):
        m = self._re.match(self.s, self.i)
        if not m:
            return None
        self.i = m.end()
        for k in ('str', 'num', 'id', 'p'):
            if m.group(k) is not None:
                return k, m.group(k)
        return None


def _js_str(tok: str) -> str:
    body = tok[1:-1]
    return re.sub(r'\\u([0-9a-fA-F]{4})|\\(.)',
                  lambda m: chr(int(m.group(1), 16)) if m.group(1) else
                  {'n': '\n', 't': '\t', 'r': ''}.get(m.group(2), m.group(2)), body)


def _value(tk: _Tok, env: dict):
    """讀一個值（只支援這個頁面會出現的形式）；遇到物件或陣列就跳過整段。"""
    t = tk.next()
    if t is None:
        return None
    kind, v = t
    if kind == 'str':
        return _js_str(v)
    if kind == 'num':
        return float(v) if ('.' in v or 'e' in v.lower()) else int(v)
    if kind == 'id':
        if v == 'true':
            return True
        if v in ('false',):
            return False
        if v in ('null', 'undefined'):
            return None
        if v == 'void':
            tk.next()  # 0
            return None
        if v == 'Array':
            depth = 0
            while True:
                t2 = tk.next()
                if t2 is None:
                    break
                if t2[1] == '(':
                    depth += 1
                elif t2[1] == ')':
                    depth -= 1
                    if depth == 0:
                        break
            return []
        return env.get(v)
    if kind == 'p' and v in '{[':
        close = '}' if v == '{' else ']'
        depth = 1
        while depth:
            t2 = tk.next()
            if t2 is None:
                break
            if t2[0] == 'p' and t2[1] in '{[':
                depth += 1
            elif t2[0] == 'p' and t2[1] in '}]':
                depth -= 1
        return {} if close == '}' else []
    return None


def _args_start(blob: str, body: int) -> int:
    """從函式本體的「{」往後走，跳過字串裡的括號，找到本體結束的「}」；回傳後面「(」之後的位置。
    （標題裡可能有「}(」這種字，不能直接找最後一個「}(」）"""
    depth = 0
    q = None
    i = body
    n = len(blob)
    while i < n:
        c = blob[i]
        if q:
            if c == '\\':
                i += 2
                continue
            if c == q:
                q = None
        elif c in '"\'`':
            q = c
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i + 2 if blob.startswith('(', i + 1) else -1
        i += 1
    return -1


def parse_nuxt_articles(html: str) -> list[dict]:
    i = html.find('__NUXT__=(function(')
    if i < 0:
        return []
    j = html.find('</script>', i)
    blob = html[i:j]
    m = re.match(r'__NUXT__=\(function\(([^)]*)\)\{', blob)
    if not m:
        return []
    params = [p.strip() for p in m.group(1).split(',')]
    k = _args_start(blob, m.end() - 1)
    if k < 0:
        return []
    tk = _Tok(blob[k:])
    args = []
    while len(args) < len(params):
        save = tk.i
        t = tk.next()
        if t is None or t == ('p', ')'):
            break
        if t == ('p', ','):
            continue
        tk.i = save
        args.append(_value(tk, {}))
    env = dict(zip(params, args))

    out = []
    for am in re.finditer(r'\{articleId:', blob):
        tk = _Tok(blob[am.start() + 1:])
        obj = {}
        while True:
            t = tk.next()
            if t is None or t == ('p', '}'):
                break
            if t == ('p', ','):
                continue
            if t[0] != 'id' and t[0] != 'str':
                break
            key = t[1] if t[0] == 'id' else _js_str(t[1])
            if tk.next() != ('p', ':'):
                break
            obj[key] = _value(tk, env)
        if obj.get('articleAid'):
            out.append(obj)
    return out


def push_score(a: dict) -> int:
    """推 − 噓（跟 PTT 列表上的推文數同一個意思；100 以上就是「爆」）。"""
    up = a.get('recommendUpCount') or 0
    down = a.get('recommendDownCount') or 0
    return int(up) - int(down)


def fetch(http, store: dict, health) -> int:
    t0 = now()
    seen = 0
    for board, label in BOARDS.items():
        try:
            r = http.get(MIRROR.format(board=board), timeout=30)
            if r.status_code != 200:
                raise RuntimeError(f'HTTP {r.status_code}')
            arts = parse_nuxt_articles(r.text)
            if not arts:
                raise RuntimeError('解析不到文章')
            n = 0
            for a in arts:
                if a.get('isPinned') or a.get('isDeleted') or a.get('isVanished'):
                    continue
                aid = a['articleAid']
                mt = re.match(r'M\.(\d{9,11})\.A\.[0-9A-F]{3}$', aid)
                if not mt:
                    continue
                title = a.get('title') or ''
                tag = _TAG.match(title)
                pid = f'{board}/{aid}'
                old = store.get(pid) or {}
                push = push_score(a)
                store[pid] = {
                    'board': board, 'url': PTT_URL.format(board=board, aid=aid), 'title': title,
                    'tag': tag.group(1) if tag else '', 'reply': title.lower().startswith('re:'),
                    'push': push, 'push_max': max(push, old.get('push_max', push)),
                    'ups': a.get('recommendUpCount') or 0, 'downs': a.get('recommendDownCount') or 0,
                    'ts': int(mt.group(1)), 'first': old.get('first') or iso(t0),
                }
                n += 1
            health[f'ptt_{board}'] = {'ok': True, 'n': n, 'at': iso(t0)}
            seen += n
        except Exception as e:  # noqa: BLE001
            health[f'ptt_{board}'] = {'ok': False, 'err': str(e)[:120], 'at': iso(t0)}
        time.sleep(1.2)
    cut = int(t0.timestamp()) - KEEP_DAYS * 86400
    for k in [k for k, v in store.items() if v.get('ts', 0) < cut]:
        del store[k]
    return seen
