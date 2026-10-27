"""離線測試用的假網路：用 tests/fixtures/raw 裡錄好的原始回應（.txt.gz）回答，不連網。

錄的是真實回應（2026-10-20 從 GitHub 實測；Steam 新評論介面是 2026-10-28 實測）。
PROBE 環境變數可以指向別的資料夾（裡面放 raw/<名稱>.txt 或 .txt.gz），例如 probe-results 分支解開的內容。
"""
import gzip
import json
import os
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
RAW = pathlib.Path(os.environ['PROBE']) / 'raw' if os.environ.get('PROBE') else HERE / 'fixtures' / 'raw'

# 網址裡有前面那段字，就回答後面那個檔名的內容（照順序比，先對到先用）
ROUTES = [
    ('forreal/data/news.json', 'fr_news'), ('forreal/data/tech.json', 'fr_tech'),
    ('forreal/data/releases.json', 'fr_releases'), ('forreal/data/meta.json', 'fr_meta'),
    ('forreal/state/names.json', 'fr_names'),
    ('filter=popularwishlist', 'steam_wishlist'), ('filter=popularcomingsoon', 'steam_comingsoon'),
    ('filter=popularnew', 'steam_popularnew'), ('filter=globaltopsellers', 'steam_globaltop'),
    ('filter=topsellers&category1=998&cc=tw&l=english', 'steam_topsell_tw_en'), ('filter=topsellers', 'steam_topsell_tw'),
    ('GetMostPlayedGames', 'steam_mostplayed'), ('steamspy.com', 'steamspy_top2w'),
    ('api/appdetails', 'steam_appdetails'),
    # Steam 評論（新介面 IUserReviewsService/GetAppReviews；languages 是繁中的是繁中評論）
    ('%22languages%22%3A%5B%22tchinese', 'steam_reviews_new_tc'), ('IUserReviewsService/GetAppReviews', 'steam_reviews_new_all'),
    ('GetNumberOfCurrentPlayers', 'steam_players_cs2'),
    ('query.wikidata.org/sparql', 'wikidata_sparql'),
    ('per-article/en.wikipedia', 'wiki_pv_article'), ('per-article/zh.wikipedia', 'wiki_pv_zh'),
    ('pttweb.cc/bbs/', 'pttweb_board'), ('trends.google.com/trending/rss', 'gtrends_new'),
    ('r/Games/top/.rss', 'reddit_games_rss'), ('r/pcgaming/top/.rss', 'reddit_pcgaming_rss'),
    ('news.google.com/rss/search?q=%22', 'gnews_tw_entity'), ('news.google.com/rss/search', 'gnews_tw_games'),
]


def raw_bytes(name):
    for p in (RAW / f'{name}.txt.gz', RAW / f'{name}.txt'):
        if p.exists():
            b = p.read_bytes()
            return gzip.decompress(b) if p.suffix == '.gz' else b
    return None


class R:
    def __init__(self, b, code=200, url=''):
        self.content = b
        self.status_code = code
        self.url = url
        self.text = b.decode('utf-8', errors='replace')

    def json(self):
        return json.loads(self.content.decode('utf-8-sig'))


class FakeHttp:
    def __init__(self, routes=ROUTES):
        self.routes = routes
        self.calls = 0
        self.by_host = {}
        self.log = []

    def get(self, url, **kw):
        self.calls += 1
        self.log.append(url)
        for frag, name in self.routes:
            if frag in url:
                b = raw_bytes(name)
                if b is not None:
                    return R(b, 200, url)
        return R(b'', 404, url)

    def json(self, url, **kw):
        r = self.get(url, **kw)
        if r.status_code != 200:
            raise RuntimeError(f'HTTP {r.status_code}')
        return r.json()
