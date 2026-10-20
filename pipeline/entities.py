"""遊戲名冊：把 Steam、玩真的、維基發售表、手動簡稱表裡的遊戲合成一份，並從新聞、PTT、趨勢標題裡認出遊戲。

認名字的規則（同樣的標題永遠得到同樣的結果）：
- 中文名：去掉空白與標點後直接比對，名字至少 2 個字。
- 英文名：照字詞邊界比對；名字太短（少於 4 個字母）或是常見英文單字（例如 Control、Inside）的單字遊戲名，
  只在大小寫完全一樣、而且不在句首時才算，避免把一般文章誤認成遊戲。
"""
import json
import pathlib
import re
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent
_CJK = re.compile(r'[\u3400-\u9fff\uf900-\ufaff]')
_MARKS = re.compile(r'[®™©℠]')
_NONWORD = re.compile(r'[^0-9a-z\u3400-\u9fff\uf900-\ufaff]+')
_COMMON = {
    'control', 'inside', 'journey', 'prey', 'rust', 'anthem', 'doom', 'fable', 'hades', 'limbo', 'braid',
    'portal', 'abzu', 'gris', 'tunic', 'celeste', 'spiritfarer', 'outlast', 'evolve', 'dishonored', 'quake',
    'returnal', 'stray', 'cocoon', 'animal', 'unpacking', 'dredge', 'balatro', 'pacific', 'contraband',
    'blight', 'echo', 'origin', 'lost', 'unity', 'raft', 'grounded', 'satisfactory', 'factorio', 'atomfall',
    'replaced', 'samson', 'frostpunk', 'party', 'island', 'fallout', 'starfield', 'battlefield', 'warframe',
    'marathon', 'saros', 'flight', 'roadwarden', 'manor', 'cities', 'retro', 'arena', 'legend', 'legends',
    'heroes', 'world', 'rivals', 'skate', 'tempest', 'eternal', 'hollow', 'knight', 'nexus', 'souls',
}
# 「Marathon」「Saros」這種本身就是重要遊戲的單字名，在遊戲新聞標題裡出現仍然要認：
_STRONG_SINGLE = {'marathon', 'saros', 'samson', 'balatro', 'frostpunk', 'starfield', 'battlefield', 'warframe',
                  'fallout', 'satisfactory', 'factorio', 'atomfall', 'dredge', 'outlast', 'returnal', 'celeste',
                  'hades', 'tunic', 'stray', 'raft', 'contraband', 'fable', 'skate', 'replaced'}


_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")


def _words(s: str) -> list[str]:
    """英文名切成小寫字詞（標點一律當分隔），用來做字詞樹比對。"""
    return _WORD.findall(norm(s).lower().replace("'", "'"))


def display(s: str) -> str:
    """顯示用的名字：只拿掉商標符號，保留全形標點。"""
    return _MARKS.sub('', s or '').replace('\xa0', ' ').strip()


def norm(s: str) -> str:
    s = unicodedata.normalize('NFKC', s or '')
    s = _MARKS.sub('', s)
    return s.replace('’', "'").replace('‘', "'").replace('：', ':').strip()


def key(s: str) -> str:
    return _NONWORD.sub('', norm(s).lower())


def _clean_name(s):
    """Steam 有些中文名自己帶書名號（《動物園之星 2》），存檔時拿掉。"""
    if not s:
        return s
    s = s.strip()
    if s.startswith(('《', '「')) and s.endswith(('》', '」')) and s.count('《') + s.count('「') == 1:
        s = s[1:-1].strip()
    return s


def has_cjk(s: str) -> bool:
    return bool(_CJK.search(s or ''))


_GENERIC_TAIL = re.compile(r'edition|remaster|collection|bundle|\bpack\b|soundtrack|\bdlc\b|season pass|switch|ps5|ps4|'
                           r'xbox|\bdemo\b|playtest|deluxe|ultimate|complete|anniversary|goty|director|版|合輯|豪華|'
                           r'完全|終極|原聲|試玩|季票|典藏', re.I)


def short_forms(name: str) -> list[str]:
    """「Call of Duty: Modern Warfare 4」→ 也認「Modern Warfare 4」；「決勝時刻：現代戰爭 4」→ 也認「現代戰爭 4」。

    只取冒號後半段夠長、而且不是常見字的部分；前半段（系列名）不自動加，避免整個系列都算到同一款。
    """
    out = []
    n = norm(name)
    for sep in (':', ' - ', '—', ' – '):
        if sep in n:
            tail = n.split(sep, 1)[1].strip()
            if _GENERIC_TAIL.search(tail):
                continue
            k = key(tail)
            if (has_cjk(tail) and len(k) >= 3) or (not has_cjk(tail) and len(k) >= 8 and ' ' in tail):
                out.append(tail)
    return out


class Game:
    __slots__ = ('gid', 'appid', 'en', 'zh', 'aliases', 'wiki_en', 'wiki_zh', 'plat', 'release', 'release_prec',
                 'release_text', 'sources', 'img', 'ranks', 'big', 'sig', 'details', 'reviews', 'players',
                 'type_hint', 'watch', 'verified')

    def __init__(self, gid):
        self.gid = gid
        self.appid = None
        self.en = None
        self.zh = None
        self.aliases = set()
        self.wiki_en = None
        self.wiki_zh = None
        self.plat = set()
        self.release = None
        self.release_prec = None
        self.release_text = None
        self.sources = set()
        self.img = None
        self.ranks = {}
        self.big = False
        self.sig = {}
        self.details = None
        self.reviews = None
        self.players = None
        self.type_hint = None
        self.watch = False
        self.verified = False

    @property
    def name(self):
        return self.zh or self.en or (f'App {self.appid}' if self.appid else self.gid)

    @property
    def store_url(self):
        return f'https://store.steampowered.com/app/{self.appid}/' if self.appid else None


class Registry:
    def __init__(self):
        self.games: dict[str, Game] = {}
        self.by_app: dict[int, str] = {}
        self.by_key: dict[str, str] = {}
        self._cjk = []      # (key, gid)
        self._trie = {}
        self._single = {}   # 單字英文名：原樣 → gid

    # ------------------------------------------------------------ 建檔
    def _new(self, gid):
        g = Game(gid)
        self.games[gid] = g
        return g

    def find(self, *, appid=None, names=()):
        if appid and int(appid) in self.by_app:
            return self.games[self.by_app[int(appid)]]
        for n in names:
            k = key(n or '')
            if len(k) >= 3 and k in self.by_key:
                return self.games[self.by_key[k]]
        return None

    def upsert(self, *, appid=None, en=None, zh=None, source=None, **kw) -> Game:
        en = _clean_name(en)
        zh = _clean_name(zh)
        names = [x for x in (en, zh) if x]
        g = self.find(appid=appid, names=names)
        if g is None:
            gid = f'app:{appid}' if appid else 'w:' + key(en or zh or '')[:40]
            if gid in self.games:
                g = self.games[gid]
            else:
                g = self._new(gid)
        if appid and not g.appid:
            g.appid = int(appid)
            self.by_app[int(appid)] = g.gid
        if en:
            en = display(en)
            if not g.en or (has_cjk(g.en) and not has_cjk(en)):
                g.en = en
            if not has_cjk(en):
                g.aliases.add(en)
        if zh:
            zh = display(zh)
            if has_cjk(zh):
                if not g.zh:
                    g.zh = zh
                g.aliases.add(zh)
            elif not g.en:
                g.en = zh
                g.aliases.add(zh)
        if source:
            g.sources.add(source)
        for k2, v in kw.items():
            if v is None or v == '' or v == []:
                continue
            if k2 == 'plat':
                g.plat.update(v)
            elif k2 == 'rank':
                list_name, r = v
                g.ranks[list_name] = min(r, g.ranks.get(list_name, 10 ** 6))
            elif k2 == 'release':
                day, prec, text = v
                if day and (not g.release or (prec == 'day' and g.release_prec != 'day')):
                    g.release, g.release_prec, g.release_text = day, prec, text
            elif k2 == 'big':
                g.big = g.big or bool(v)
            elif k2 == 'alias':
                for a in v:
                    g.aliases.add(display(a))
            elif hasattr(g, k2) and getattr(g, k2) in (None, False):
                setattr(g, k2, v)
        for n in list(g.aliases):
            k = key(n)
            if len(k) >= 3:
                self.by_key.setdefault(k, g.gid)
        return g

    # ------------------------------------------------------------ 比對
    def build_matcher(self):
        cjk = {}
        latin = {}
        single = {}
        for g in self.games.values():
            names = set(g.aliases)
            for n in list(names):
                names.update(short_forms(n))
            for n in names:
                if not n:
                    continue
                if has_cjk(n):
                    k = key(n)
                    if len(k) >= 2:
                        # 同一個中文名對到多款遊戲時，交給排名最高（資料最多）的那款
                        cjk.setdefault(k, set()).add(g.gid)
                else:
                    words = re.findall(r"[A-Za-z0-9']+", n)
                    k = key(n)
                    if len(k) < 4 and not re.search(r'\d', n):
                        continue
                    if len(words) == 1:
                        w = words[0]
                        if w.lower() in _COMMON and w.lower() not in _STRONG_SINGLE:
                            single.setdefault(w, set()).add(g.gid)
                            continue
                    latin.setdefault(tuple(_words(n)), set()).add(g.gid)
        # 中文名：依前兩個字建索引，長的名字先比（「決勝時刻：現代戰爭 4」先於「決勝時刻」）
        idx = {}
        for k, gids in cjk.items():
            idx.setdefault(k[:2], []).append((k, gids))
        for v in idx.values():
            v.sort(key=lambda x: -len(x[0]))
        self._cjk = idx
        trie = {}
        for words, gids in latin.items():
            if not words:
                continue
            node = trie
            for w in words:
                node = node.setdefault(w, {})
            node.setdefault('$', set()).update(gids)
        self._trie = trie
        self._single = single
        self._single_re = (re.compile(r'(?<![A-Za-z0-9])(' + '|'.join(re.escape(w) for w in sorted(single, key=len, reverse=True))
                                      + r')(?![A-Za-z0-9])') if single else None)
        self._keys = {gid: {key(n) for n in g.aliases if len(key(n)) >= 2} for gid, g in self.games.items()}

    def _pick(self, gids):
        if len(gids) == 1:
            return next(iter(gids))

        def weight(gid):
            g = self.games[gid]
            return (g.verified, len(g.ranks), -min(g.ranks.values()) if g.ranks else 0, bool(g.appid))
        return max(gids, key=weight)

    def _drop_generic(self, found: dict) -> dict:
        """同一句同時認出「Final Fantasy」和「Final Fantasy VII Rebirth」時，只留具體的那款。
        但如果長的那個名字沒經過驗證（例如更新名稱「The Saros: Zenith」）而短的有，留短的。"""
        if len(found) < 2:
            return found
        gids = list(found)
        drop = set()
        for a in gids:
            for b in gids:
                if a == b or a in drop or b in drop:
                    continue
                ka, kb = self._keys.get(a, set()), self._keys.get(b, set())
                if any(x != y and len(x) < len(y) and x in y for x in ka for y in kb):
                    ga, gb = self.games[a], self.games[b]
                    if ga.verified and not gb.verified:
                        drop.add(b)
                    else:
                        drop.add(a)
        return {g: v for g, v in found.items() if g not in drop}

    def match(self, text: str, *, strict_latin: bool = False, min_cjk: int = 2) -> list[str]:
        """回傳標題裡出現的遊戲 gid（不重複，依出現位置排序）。

        strict_latin / min_cjk 用在一般大眾的內容（例如 Google 趨勢），名字太短就不認。
        """
        if not text:
            return []
        found = {}
        t = norm(text)
        # 《》或「」裡的名字最可靠
        for m in re.finditer(r'[《「]([^》」]{1,60})[》」]', t):
            g = self.find(names=[m.group(1)])
            if g:
                found.setdefault(g.gid, m.start())
        tk = key(t)
        used = [False] * len(tk)
        for i in range(len(tk) - 1):
            cands = self._cjk.get(tk[i:i + 2])
            if not cands or used[i]:
                continue
            for k, gids in cands:
                if len(k) < min_cjk or not tk.startswith(k, i):
                    continue
                if any(used[i:i + len(k)]):
                    continue
                found.setdefault(self._pick(gids), i)
                for j in range(i, i + len(k)):
                    used[j] = True
                break
        toks = _words(t)
        i = 0
        while i < len(toks):
            node, j, best = self._trie, i, None
            while j < len(toks) and toks[j] in node:
                node = node[toks[j]]
                j += 1
                if '$' in node:
                    best = (j, node['$'])
            if best and not (strict_latin and len(''.join(toks[i:best[0]])) < 6):
                found.setdefault(self._pick(best[1]), 1000 + i)
                i = best[0]
            else:
                i += 1
        if self._single_re is not None and not strict_latin:
            for m in self._single_re.finditer(t):
                if m.start() == 0:
                    continue
                after = t[m.end():m.end() + 5].lower()
                if after.startswith((' the', ' a ', ' an ', ' of', ' your')):
                    continue
                found.setdefault(self._pick(self._single[m.group(1)]), m.start())
        found = self._drop_generic(found)
        return [g for g, _ in sorted(found.items(), key=lambda x: x[1])]


def load_aliases() -> list[dict]:
    try:
        return json.loads((ROOT / 'config' / 'aliases.json').read_text(encoding='utf-8')).get('games', [])
    except (OSError, ValueError):
        return []


def apply_aliases(reg: Registry, entries: list[dict]):
    for e in entries:
        g = None
        for n in e.get('for', []):
            g = reg.find(names=[n])
            if g:
                break
        if g is None:
            continue
        reg.upsert(appid=g.appid, en=g.en, zh=e.get('zh'), alias=e.get('aliases', []))
        if e.get('wiki_en') and not g.wiki_en:
            g.wiki_en = e['wiki_en']
