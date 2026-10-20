"""用 probe 抓到的原始回應測試（不連網）。PROBE 環境變數指向 probe-results 分支解開的資料夾。"""
import json
import os
import pathlib

RAW = pathlib.Path(os.environ.get('PROBE', '/tmp/claude-0/-home-claude/cee980f9-5e38-5530-b4df-135bdea4688a/scratchpad/probe_all')) / 'raw'

ROUTES = [
    ('forreal/data/news.json', 'fr_news'), ('forreal/data/tech.json', 'fr_tech'),
    ('forreal/data/releases.json', 'fr_releases'), ('forreal/data/meta.json', 'fr_meta'),
    ('forreal/state/names.json', 'fr_names'),
    ('filter=popularwishlist', 'steam_wishlist'), ('filter=popularcomingsoon', 'steam_comingsoon'),
    ('filter=popularnew', 'steam_popularnew'), ('filter=globaltopsellers', 'steam_globaltop'),
    ('filter=topsellers&category1=998&cc=tw&l=english', 'steam_topsell_tw_en'), ('filter=topsellers', 'steam_topsell_tw'),
    ('GetMostPlayedGames', 'steam_mostplayed'), ('steamspy.com', 'steamspy_top2w'),
    ('api/appdetails', 'steam_appdetails'),
    ('language=tchinese', 'steam_reviews_tc_recent'), ('appreviews/', 'steam_reviews_recent'),
    ('GetNumberOfCurrentPlayers', 'steam_players_cs2'),
    ('query.wikidata.org/sparql', 'wikidata_sparql'),
    ('per-article/en.wikipedia', 'wiki_pv_article'), ('per-article/zh.wikipedia', 'wiki_pv_zh'),
    ('pttweb.cc/bbs/', 'pttweb_board'), ('trends.google.com/trending/rss', 'gtrends_new'),
    ('r/Games/top/.rss', 'reddit_games_rss'), ('r/pcgaming/top/.rss', 'reddit_pcgaming_rss'),
    ('news.google.com/rss/search?q=%22', 'gnews_tw_entity'), ('news.google.com/rss/search', 'gnews_tw_games'),
]


class R:
    def __init__(self, b, code=200):
        self.content = b
        self.status_code = code
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
                p = RAW / f'{name}.txt'
                if p.exists():
                    return R(p.read_bytes())
        return R(b'', 404)

    def json(self, url, **kw):
        r = self.get(url, **kw)
        if r.status_code != 200:
            raise RuntimeError(f'HTTP {r.status_code}')
        return r.json()
