"""回波的全部離線測試（不連網）：python3 tests/run_all.py [--ui]

1. 用 tests/fixtures 錄好的真實回應跑一整輪後台，檢查輸出合理（題目、分數、等級、TOP 5、預報日期、評論資料）。
2. 各模組的單元測試：Steam 新評論介面、PTT 解析、遊戲名冊、已做過比對、關鍵字邊界、標題相似度、一句話說明、TOP 5 規則。
3. 後台與 App 的時效分數一模一樣（node 跑 tests/app_check.mjs）；加 --ui 會再用 Chromium 開網站檢查（tests/ui_check.mjs）。
全部通過才印「全部通過」，有任何一項失敗就以非零結束。
"""
import contextlib
import datetime as dt
import io
import json
import pathlib
import random
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline import build, entities, ptt, steam, topics  # noqa: E402
from tests import local_build  # noqa: E402
from tests.fake_http import R, raw_bytes  # noqa: E402

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print('  ✗', msg)
    return cond


def section(name):
    print(f'\n── {name}')


# ------------------------------------------------------------------ 1. 整輪後台
def test_pipeline(tmp: pathlib.Path):
    section('整輪後台（錄好的真實回應）')
    out = tmp / 'site'
    state = tmp / 'state'
    shutil.copytree(local_build.SEED, state)
    (state / 'cache' / 'reviews.json.gz').unlink(missing_ok=True)  # 讓評論重新抓，走新介面
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        outputs, http = local_build.run(out, state, quiet=True)
    check('Traceback' not in err.getvalue(), '後台各步驟沒有例外：\n' + err.getvalue()[-1500:])
    radar, cal, meta = outputs['radar.json'], outputs['calendar.json'], outputs['meta.json']
    tl = radar['topics']
    check(len(tl) >= 50, f'題目數量合理（{len(tl)}）')
    for t in tl:
        ok = all(k in t for k in ('id', 'title', 'scores', 'score', 'grade', 'angles', 'why', 'reasons', 'pillar'))
        if not check(ok, f'題目欄位齊全：{t.get("id")}'):
            break
        check(t['grade'] == topics.grade(topics.total(t['scores'], meta['channel']['weights'])), f'等級與分數一致：{t["id"]}')
        check(t.get('total') == topics.total(t['scores'], meta['channel']['weights']), f'有沒四捨五入的總分：{t["id"]}')
        check(len(t['angles']) == 3, f'角度三選一：{t["id"]}')
        check('《App ' not in t['title'], f'題目沒有暫代名稱：{t["title"]}')
        g = t.get('game') or {}
        check(not str(g.get('name') or '').startswith('App '), f'遊戲有正式名稱：{t["id"]}')
        w = t['why']
        check(w.count('（') == w.count('）') and '最熱' not in w and w.endswith(t['reasons']['timing'][0]),
              f'「為什麼是現在」沒有半個括號、最後一句是時效：{w}')
    # 頻道規矩：巴哈姆特（含 GNN）的內容與連結一律不收（原始資料裡有，輸出不能有）
    blob = json.dumps([radar, cal], ensure_ascii=False)
    check(not any(x in blob for x in ('gamer.com.tw', '巴哈姆特', 'GNN 新聞網')), '輸出沒有巴哈姆特／GNN 的內容與連結')
    check(len(radar['top']) == 5, 'TOP 5 剛好 5 題')
    check(radar['top'] == build.pick_top(tl), 'TOP 5 照 pick_top 規則')
    by = {t['id']: t for t in tl}
    tops = [by[i] for i in radar['top']]
    check(all(not (t.get('done') or {}).get('status') for t in tops), 'TOP 5 沒有已做過或製作中的題目')
    h = meta['health']
    # 評論快取清空後重抓：一輪最多 40 款（每 5.5 小時更新一次，幾輪後就會補齊）
    check(h.get('steam_reviews', {}).get('ok') and h['steam_reviews'].get('n', 0) >= 30, f'Steam 評論有抓到（{h.get("steam_reviews")}）')
    rv = [t for t in tl if (t.get('game') or {}).get('reviews')]
    check(len(rv) >= 5, f'有評論資料的遊戲題目（{len(rv)}）')
    sample = rv[0]['game']['reviews'] if rv else {}
    check(isinstance(sample.get('pct'), int) and sample.get('n', 0) > 0 and sample.get('desc'), f'評論統計格式（{sample}）')
    for k in ('steam_lists', 'steam_wishlist', 'steam_players', 'ptt_Steam', 'forreal_news', 'gnews'):
        if k in h:
            check(h[k].get('ok'), f'測站 {k} 正常（{h[k]}）')
    # 預報：Steam 活動用台灣日期（新品節 10/19 太平洋時間 10 點開始＝台灣 10/20 凌晨 1 點）
    nf = [x for x in cal['items'] if x['title'].startswith('Steam 新品節')]
    check(nf and nf[0]['date'] == '2026-10-20' and nf[0]['end'] == '2026-10-26', f'新品節是台灣日期（{nf[:1]}）')
    return out, radar


# ------------------------------------------------------------------ 2. 單元測試
def test_reviews():
    section('Steam 評論（新介面 IUserReviewsService）')
    seen = []

    class H:
        def __init__(self, mode):
            self.mode = mode

        def get(self, url, **kw):
            seen.append(url)
            if self.mode == 'redirect':  # 舊介面的狀況：轉回商店首頁
                return R(b'<html>Steam store</html>', 200, 'https://store.steampowered.com/')
            if self.mode == '429':
                return R(b'', 429)
            if self.mode == 'empty':  # 還沒有人評論
                return R(b'{"response":{}}', 200, url)
            name = 'steam_reviews_new_tc' if '%22languages%22%3A%5B%22tchinese' in url else 'steam_reviews_new_all'
            return R(raw_bytes(name), 200, url)
    cache, health = {}, {}
    steam.reviews(H('ok'), [1867240], cache, health)
    c = cache.get('1867240') or {}
    check(health['steam_reviews']['ok'] and health['steam_reviews']['n'] == 1, f'抓得到（{health["steam_reviews"]}）')
    check((c.get('all') or {}).get('pct') == 66 and c['all']['n'] == 146523, f'全球評價（{c.get("all")}）')
    check((c.get('all') or {}).get('desc') == '褒貶不一', '評價文字是繁中')
    check((c.get('recent') or {}).get('n') == 100, '最近 100 篇')
    check((c.get('tc') or {}).get('n') == 11413, f'繁中評論（{c.get("tc")}）')
    check('input_json=' in seen[0] and 'api.steampowered.com/IUserReviewsService/GetAppReviews' in seen[0], '用新的網址')
    q = json.loads(__import__('urllib.parse').parse.unquote(seen[0].split('input_json=')[1]))
    check(q.get('filter') == 1 and q.get('languages') == ['all'] and q.get('purchase_type') == 1, f'參數正確（{q}）')
    for mode in ('redirect', '429'):
        cache, health = {}, {}
        steam.reviews(H(mode), [1, 2], cache, health)
        check(not cache and not health['steam_reviews']['ok'] and '失敗' in health['steam_reviews'].get('err', ''),
              f'{mode} 時記錄錯誤原因、不寫入壞資料（{health["steam_reviews"]}）')
    cache, health = {}, {}
    steam.reviews(H('empty'), [3], cache, health)
    check(health['steam_reviews']['ok'] and not health['steam_reviews'].get('err') and '3' in cache and cache['3']['all'] is None,
          f'還沒有人評論：存成 0 篇、不算失敗（{health["steam_reviews"]}）')


def test_ptt():
    section('PTT 鏡像站解析')
    html = raw_bytes('pttweb_board').decode('utf-8', 'replace')
    arts = ptt.parse_nuxt_articles(html)
    check(len(arts) >= 20, f'解析得到文章（{len(arts)}）')
    i = html.find('__NUXT__=(function(')
    j = html.find('</script>', i)
    blob = html[i:j]
    k = blob.rfind('}(')
    args = blob[k + 2:]
    q1 = args.find('"')
    q2 = args.find('"', q1 + 1)
    evil = html[:i] + blob[:k + 2] + args[:q1] + '"[情報] {限時}(免費)"' + args[q2 + 1:] + html[j:]
    arts2 = ptt.parse_nuxt_articles(evil)
    same = sum(1 for a, b in zip(arts, arts2) if a.get('recommendUpCount') == b.get('recommendUpCount'))
    check(same == len(arts), '參數裡有「}(」也不會解析錯')


def test_entities():
    section('遊戲名冊與認名字')
    reg = entities.Registry()
    reg.upsert(appid=2384580, zh='真・三國無雙 起源', source='steam')
    reg.upsert(appid=968790, zh='師父', en='Sifu', source='steam')
    reg.upsert(appid=2050650, en='Resident Evil 4', zh='惡靈古堡 4', source='steam')
    reg.upsert(appid=254700, en='Resident Evil 4', source='steam')
    reg.upsert(appid=4017880, zh='アノマリス／ANOMALITH', source='steam')
    reg.upsert(en='Anomalith', source='wiki')
    reg.build_matcher()
    for text in ('真‧三國無雙 起源 銷量突破百萬', '真·三國無雙 起源 更新', '真三國無雙 起源 DLC', '《真・三國無雙 起源》'):
        check(reg.match(text) == ['app:2384580'], f'認得「{text}」')
    check(reg.match('這位師父很厲害') == [], '「師父」在一般句子裡不認')
    check(reg.match('《師父》開發商新作') == ['app:968790'], '「《師父》」放在書名號裡才認')
    check(reg.find(appid=254700) is not reg.find(appid=2050650), '不同 appid 的同名遊戲不會合併')
    check(len([g for g in reg.games.values() if g.appid == 4017880 or (g.en or '').lower() == 'anomalith']) == 1,
          '雙語名稱（アノマリス／ANOMALITH）跟維基的 Anomalith 合成一款')


def test_rules():
    section('已做過比對、關鍵字邊界、標題相似度')
    today = dt.date(2026, 10, 20)
    vids = [{'id': 'gears', 'title': '戰爭機器 DEI', 'status': 'done', 'date': '2026-10-08', 'pillar': 'issue',
             'match': {'keys': ['E-Day', 'DEI']}},
            {'id': 'sm', 'title': 'Steam Machine 分析', 'status': 'done', 'date': '2026-07', 'pillar': 'hardware',
             'match': {'keys': ['Steam Machine']}}]
    d = topics.Done(vids, today)
    check(d.for_text('Xbox one-day sale starts now') is None, '「one-day」不會被當成 E-Day')
    check(d.for_text('Gears of War: E-Day delayed') is not None, '「E-Day」認得')
    check(d.for_text('Steam Machine price revealed') is None, '三個月前的影片不算「已經做過」')
    check(d.older_for_text('Steam Machine price revealed') is not None, '但會提示可以做續集')
    check(d.older_for_text('Googlebook 挑戰高階市場，外媒憂淪下個 Steam Machine') is None, '不是主角就不提示續集')
    kw = topics.load_cfg('keywords.json')
    legal = topics.kw_regex(kw['issue']['legal']['en'], kw['issue']['legal']['zh'])
    labor = topics.kw_regex(kw['issue']['labor']['en'], kw['issue']['labor']['zh'])
    check(not legal.search('Developer Issues Statement'), '「Issues」不是訴訟')
    check(legal.search('Nintendo sues Palworld developer') is not None, '「sues」是訴訟')
    check(not labor.search('Counter-Strike 2 update') and not labor.search('Crunchyroll Game Vault'), 'Counter-Strike、Crunchyroll 不是勞資')
    check(labor.search('Rockstar staff hold strike vote') is not None, '罷工投票是勞資')
    check(topics.similar('[閒聊] 漲價', '索尼宣布 PS5 主機在台漲價', True) == 0, '太短的 PTT 標題不算同一件事')
    check(topics.similar('決勝時刻現代戰爭4首發評價炸裂', '《決勝時刻：現代戰爭4》首發評價出爐', True) >= 0.45, '同一件事的標題算相似')


def test_why():
    section('「為什麼是現在」的一句話說明')
    tt = '今天就要開工，10/31（週六）前上架'
    cases = [
        ([{'id': 'reddit', 'text': 'r/Games 當日熱門第 1：Shadow Blade Zero review: great', 'level': 80, 'up': True},
          {'id': 'ptt', 'text': 'PTT 3 天 27 篇討論，最熱：[情報] 影之刃零（PS5 版畫面（52 推）', 'level': 70}],
         'r/Games 當日熱門第 1；PTT 3 天 27 篇討論', 'PTT 的數字要寫出是 PTT，最熱那篇的標題不放'),
        ([{'id': 'steam_tw', 'text': '台灣區熱銷第 2', 'level': 90},
          {'id': 'wiki_en', 'text': '英文維基每天約 5.6 萬人次（比上週 +55%）', 'level': 80, 'up': True}],
         '英文維基每天約 5.6 萬人次（比上週 +55%）；台灣區熱銷第 2', '漲跌幅留著'),
        ([{'id': 'media', 'text': '6 家媒體報導，含官方消息（Engadget、Game Developer、IGN、PC Gamer）', 'level': 80},
          {'id': 'gnews', 'text': '（決勝時刻：現代戰爭 4）台灣 3 天 27 篇報導：《決勝時刻》新作：首週', 'level': 60}],
         '6 家媒體報導，含官方消息；（決勝時刻：現代戰爭 4）台灣 3 天 27 篇報導', '媒體名單拿掉，遊戲名裡的冒號不切'),
        ([{'id': 'steam', 'text': '同時在線 62.5 萬人（24 小時 +12%）、全球熱銷第 13、願望單第 6', 'level': 80}],
         '同時在線 62.5 萬人（24 小時 +12%）、全球熱銷第 13', '最多兩項'),
    ]
    for cards, want, why in cases:
        got = topics._why_now(cards, tt)
        check(got == f'{want}。{tt}', f'{why}（{got}）')


def test_software():
    section('軟體不當成遊戲題目')
    G = lambda genres, ids=None: type('G', (), {'details': {'genres': genres, **({'genre_ids': ids} if ids else {})}})()  # noqa: E731
    check(topics.is_software(G(['休閒', '獨立製作', '動畫和建模', '設計和繪圖', '圖像編輯'])), 'Wallpaper Engine（Steam 繁中分類名稱）是軟體')
    check(topics.is_software(G(['工具', '影片製作'])), 'OBS 是軟體')
    check(topics.is_software(G(['休閒'], ['4', '57'])), '看分類代號（舊名稱對不上也認得）')
    check(not topics.is_software(G(['角色扮演', '教育'])), '有遊戲分類的教育遊戲照樣是遊戲')
    check(not topics.is_software(G(['動作', '冒險'], ['1', '25'])), '一般遊戲不是軟體')


def test_top5():
    section('TOP 5 規則')

    def T(i, score, pillar, typ='launch', done=None):
        return {'id': i, 'score': score, 'pillar': pillar, 'type': typ, 'done': done}
    tl = [T('a', 90, 'spotlight'), T('b', 89, 'spotlight'), T('c', 84, 'issue', 'issue'), T('d', 83.5, 'spotlight'),
          T('e', 78, 'spotlight', 'sleeper'), T('f', 74, 'season', 'season', {'status': 'making'}),
          T('g', 69, 'issue', 'issue'), T('h', 59, 'season', 'season')]
    check(build.pick_top(tl) == ['a', 'b', 'c', 'd', 'g'], f'必拍題不會被可拍題擠掉（{build.pick_top(tl)}）')
    tl2 = [T('a', 90, 'spotlight'), T('b', 85, 'spotlight'), T('c', 80, 'spotlight'), T('d', 79, 'spotlight'),
           T('e', 60, 'issue', 'issue'), T('f', 40, 'season', 'season')]
    check(build.pick_top(tl2) == ['a', 'b', 'c', 'd', 'e'], f'寧可多放同路線的高分題，也不拿低分題湊多樣性（{build.pick_top(tl2)}）')
    tl3 = [T('a', 90, 'spotlight'), T('b', 85, 'spotlight'), T('c', 80, 'spotlight'), T('d', 79, 'spotlight'),
           T('e', 75, 'spotlight', 'sleeper'), T('f', 60, 'issue', 'issue'), T('g', 59, 'season', 'season')]
    check(build.pick_top(tl3) == ['a', 'b', 'c', 'f', 'g'], f'可拍題夠的時候照樣分散路線（{build.pick_top(tl3)}）')
    # 69.46 分：畫面上是 69（可拍），不能因為先四捨五入成 69.5 就被當成 70 分的必拍
    w = {'demand': 40, 'timing': 25, 'fit': 20, 'ease': 15}
    raw = topics.total({'demand': 80, 'timing': 88, 'fit': 70, 'ease': 50}, w)
    c = {**T('c', round(raw, 1), 'spotlight'), 'total': raw}
    tl4 = [T('a', 90, 'spotlight'), T('b', 85, 'spotlight'), c, T('d', 66, 'issue', 'issue'), T('e', 65, 'season', 'season'),
           T('f', 64, 'hardware', 'hardware')]
    check(topics.shown(raw) == 69 and build.pick_top(tl4) == ['a', 'b', 'd', 'e', 'f'], f'門檻照畫面上的整數分數（{raw:.3f}：{build.pick_top(tl4)}）')


def deadline_cases(path: pathlib.Path):
    random.seed(7)
    base = dt.date(2026, 10, 1)
    cases = []
    for _ in range(3000):
        today = base + dt.timedelta(days=random.randint(0, 120))
        pub = base + dt.timedelta(days=random.randint(0, 120))
        sb = pub - dt.timedelta(days=random.randint(1, 7))
        od = pub - dt.timedelta(days=random.randint(0, 30))
        cl = pub + dt.timedelta(days=random.randint(0, 14))
        s, txt = topics._deadline_timing(today, od, sb, pub, cl)
        cases.append([today.isoformat(), od.isoformat(), sb.isoformat(), pub.isoformat(), cl.isoformat(), s, txt])
    path.write_text(json.dumps(cases, ensure_ascii=False), encoding='utf-8')


def run_node(script, *args):
    try:
        p = subprocess.run(['node', str(ROOT / 'tests' / script), *map(str, args)], capture_output=True, text=True, timeout=600)
    except FileNotFoundError:
        print('  （沒有 node，略過）')
        return
    print(p.stdout.rstrip())
    check(p.returncode == 0, f'{script} 通過' + (f'：{p.stderr[-800:]}' if p.returncode else ''))


def main():
    ui = '--ui' in sys.argv
    t_start = time.time()
    with tempfile.TemporaryDirectory(prefix='echo-test-') as td:
        tmp = pathlib.Path(td)
        out, radar = test_pipeline(tmp)
        test_reviews()
        test_ptt()
        test_entities()
        test_rules()
        test_why()
        test_software()
        test_top5()
        section('App 與後台規則一致（node）')
        deadline_cases(tmp / 'deadline_cases.json')
        run_node('app_check.mjs', tmp / 'deadline_cases.json')
        if ui:
            section('瀏覽器實測（Chromium）')
            shots = ROOT / 'tests' / 'out'
            shots.mkdir(exist_ok=True)
            run_node('ui_check.mjs', out, shots)
    print(f'\n{"全部通過" if not FAILS else f"失敗 {len(FAILS)} 項"}（{round(time.time() - t_start, 1)} 秒）')
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
