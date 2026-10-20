"""角度三選一：每個題目給三個切法。

  穩：大家都會做、最安全的切法
  反差：從數據裡找到的衝突點（評價 vs 人數、全球 vs 台灣、官方說法 vs 玩家反應）
  只有你：別人不會做、要靠 PZ 的身分或設備才做得出來（台灣視角、繁中、PTT、RTX 5090 筆電、自嘲）

每個角度都附「為什麼」（用到哪個數字）；需要 PZ 自己玩過或親自到場的，標 need。
規則固定，同樣的資料永遠得到同樣的角度。
"""
import re

from .signals import fmt_num


def _A(kind, title, hook, why, need=None, risk=None):
    a = {'kind': kind, 'label': {'safe': '穩', 'contrast': '反差', 'pz': '只有你'}[kind], 'title': title,
         'hook': hook, 'why': why}
    if need:
        a['need'] = need
    if risk:
        a['risk'] = risk
    return a


def _price(gb):
    return gb.get('price') if gb else None


def _tc_gap(gb):
    a = (gb.get('reviews') or {}).get('pct')
    t = (gb.get('tc_reviews') or {}).get('pct')
    n = (gb.get('tc_reviews') or {}).get('n') or 0
    if a is None or t is None or n < 30:
        return None
    return a, t


def _heavy_gpu(gb):
    """建議配備要中高階以上的顯卡（RTX 3070／4070／5070、RX 6800／7800 等級以上），或是售價 1,500 以上的大作。"""
    g = (gb.get('gpu') or '').upper().replace(' ', '')
    if re.search(r'RTX(30[789]0|40[789]0|50[6-9]0)|RX(6[89]\d0|7[789]\d0|90[67]0)', g):
        return True
    return (gb.get('price') or 0) >= 1500 and any(x in ' '.join(gb.get('genres') or []) for x in ('動作', '射擊', '角色扮演', '冒險'))


def _ptt_top(t):
    p = t.get('ptt') or []
    return max(p, key=lambda x: x.get('push', 0)) if p else None


def launch(t):
    gb = t['game']
    n = gb['name']
    out = []
    phase = t.get('phase')
    rv = gb.get('reviews') or {}
    pl = gb.get('players') or {}
    rc = gb.get('recent') or {}
    if phase == 'pre':
        out.append(_A('safe', f'《{n}》上市前必看：值不值得首發？', '發售日、售價、平台、配備、有沒有中文，一支講清楚',
                      '上市前一週是搜尋高峰，「值不值得買」是觀眾最直接的問題'))
    else:
        out.append(_A('safe', f'《{n}》首發評價：值不值得買？', '先講結論，再講優缺點與適合誰',
                      '上市後幾天「好不好玩」的搜尋最多'))
    # 反差
    con = []
    if rv.get('pct') is not None and rv.get('pct') <= 72 and (pl.get('now') or 0) >= 10000:
        con.append(_A('contrast', f'罵歸罵還是在玩？《{n}》好評只有 {rv["pct"]}%，在線卻有 {fmt_num(pl["now"])} 人',
                      '用數字拆解「大家嘴上罵、身體很誠實」', f'Steam 好評率 {rv["pct"]}%、同時在線 {fmt_num(pl["now"])}'))
    if rv.get('pct') and rc.get('pct') is not None and rc.get('n', 0) >= 50 and rc['pct'] <= rv['pct'] - 15:
        con.append(_A('contrast', f'首發叫好、現在叫罵：《{n}》最近 {rc["n"]} 篇只剩 {rc["pct"]}% 好評',
                      '從評論找出口碑翻轉的時間點與原因', f'整體 {rv["pct"]}% → 最近 {rc["pct"]}%'))
    if rv.get('pct') is not None and rv['pct'] >= 92 and (rv.get('n') or 0) >= 2000:
        con.append(_A('contrast', f'{rv["pct"]}% 好評的《{n}》真的有那麼神？我專門挑毛病',
                      '大家都在吹的時候，講清楚它不適合誰', f'Steam {rv["pct"]}% 好評、{fmt_num(rv["n"])} 篇評論'))
    w = (gb.get('ranks') or {}).get('wishlist')
    if phase == 'pre' and w and w <= 30 and (t.get('scores') or {}).get('demand', 0) < 60:
        con.append(_A('contrast', f'願望單第 {w} 名卻沒什麼人在講：《{n}》是不是被低估了？',
                      '大家都在等，但媒體還沒跟上：先講就是先贏', f'Steam 願望單第 {w} 名'))
    p = _price(gb)
    if p and p >= 1200:
        con.append(_A('contrast', f'NT${int(p):,} 的《{n}》，首發買還是等特價？', '拿同類型遊戲的價格與內容量來比',
                      f'Steam 台灣售價 {gb.get("price_text")}'))
    if not con:
        con.append(_A('contrast', f'先別急著買：《{n}》可能踩雷的地方', '只講有證據的雷點（評測、評論、官方公告），不講傳聞',
                      '「不推薦」的影片在這個頻道一向有人看（推薦與不推薦格式）'))
    out.append(con[0])
    # 只有你
    pz = []
    gap = _tc_gap(gb)
    if gap and abs(gap[0] - gap[1]) >= 10:
        a, b = gap
        word = '不滿' if b < a else '特別買單'
        pz.append(_A('pz', f'全球 {a}% 好評，繁中玩家 {b}%：台灣人在{word}什麼？',
                     '翻繁中評論找答案（翻譯、價格、伺服器、文化差異）', f'Steam 全球 {a}% vs 繁中評論 {b}%'))
    if gb.get('tc') is False:
        pz.append(_A('pz', f'沒有中文的《{n}》，值得硬啃嗎？', '實際看英文量有多大、哪些地方會卡關，給英文不好的台灣玩家參考',
                     'Steam 語言列表沒有繁體中文', need='需要你實際玩過一段'))
    if _heavy_gpu(gb):
        pz.append(_A('pz', f'RTX 5090 筆電全開實測：《{n}》吃不吃效能？', '實測幀數、畫質設定建議，順便自嘲「這台筆電比我的存款還值錢」',
                     f'建議配備：{gb.get("gpu")}' if gb.get('gpu') else '大作上市，畫質與效能是玩家第一個問題',
                     need='需要你用筆電實測錄影'))
    top = _ptt_top(t)
    if top and top.get('push', 0) >= 20:
        pz.append(_A('pz', f'PTT 鄉民最在意的《{n}》問題，我一次回答', '把 PTT 熱門討論整理成 QA，加上自己的立場',
                     f'PTT 最熱的一篇 {top["push"] if top["push"] < 100 else "爆"} 推'))
    if gb.get('tc') and not pz:
        pz.append(_A('pz', f'繁中翻譯品質檢查：《{n}》的中文到底行不行？', '挑幾段經典翻譯，好的誇、爛的吐槽',
                     '台灣觀眾最在意中文品質', need='需要你實際玩過一段'))
    if not pz:
        pz.append(_A('pz', f'老玩家觀點：《{n}》跟同類型比，強在哪、弱在哪', '用玩過的同類作品當對照，講出別人講不出的比較',
                     '頻道定位是資深玩家視角'))
    out.append(pz[0])
    return out


def sleeper(t):
    gb = t['game']
    n = gb['name']
    hook = (t.get('hooks') or [''])[0]
    out = [_A('safe', f'《{n}》為什麼突然爆紅？', '先講它是什麼遊戲，再講紅的原因', hook or 'Steam 數據突然上升')]
    r = gb.get('ranks') or {}
    if r.get('topsell_tw') and (r.get('topsell') or 999) > 40:
        out.append(_A('contrast', f'台灣人特別愛？《{n}》台灣熱銷第 {r["topsell_tw"]}，全球卻{"排不進前百" if not r.get("topsell") else "只有第 " + str(r["topsell"])}',
                      '找出台灣玩家特別買單的原因', '台灣區熱銷與全球熱銷的落差'))
    else:
        out.append(_A('contrast', f'小團隊打爆大作？《{n}》爆紅的真正原因', '拿同期大作的數字對比，講清楚它贏在哪', hook or '在線人數與熱銷排名'))
    p = _price(gb)
    if p:
        out.append(_A('pz', f'NT${int(p):,} 跟風試玩《{n}》：值不值得？', '自己玩一段的真心話，順便自嘲錢包又破洞',
                      f'台灣售價 {gb.get("price_text")}', need='需要你實際玩過一段'))
    else:
        out.append(_A('pz', f'PZ 試玩《{n}》：它紅在哪、會紅多久', '玩一段之後給判斷：是曇花一現還是長青', '數據在升溫', need='需要你實際玩過一段'))
    return out


_ISSUE_CON = {
    'price': ('漲價真的合理嗎？算給你看', '把歷代售價、內容量、匯率攤開來比'),
    'layoff': ('又一間工作室倒下：問題出在哪？', '整理這幾年的作品與銷量，講清楚是誰做錯決定'),
    'monet': ('內購吃相：這次哪裡踩線？', '列出付費設計，跟同類遊戲比'),
    'ownership': ('你買的遊戲真的是你的嗎？', '整理下架／停服的來龍去脈與玩家能做什麼'),
    'backlash': ('玩家罵的跟官方說的，差在哪？', '把玩家的不滿一條條對照官方說法，給出有立場的結論'),
    'legal': ('法律要管遊戲了？會怎麼影響玩家', '講法條重點與時程，不講政治立場'),
    'labor': ('做遊戲的人在抗爭：玩家該在意嗎？', '整理訴求與公司回應'),
    'ai': ('AI 做的遊戲你會買嗎？', '整理這次爭議的事實，再講玩家真正在意的點'),
    'culture': ('只談遊戲本身：玩家真正在意的是什麼', '把跟遊戲無關的口水戰拿掉，只留跟遊戲品質有關的證據'),
    'deal': ('被收購之後，會變好還是變爛？', '用過去的收購案當對照'),
    'delay': ('又延期！這次的時間可以信嗎？', '整理歷次延期紀錄與官方說法'),
}


def _subject(t):
    """角度標題裡要講清楚在講誰：有遊戲就用《遊戲名》，硬體用產品名；都沒有而且題目太長就不加（題目會寫在企劃包裡）。"""
    gb = t.get('game')
    if gb:
        return f'《{gb["name"]}》'
    if t.get('product'):
        return t['product']
    head = re.split(r'[：:，,]', t['title'])[0]
    return head if len(head) <= 12 else None


def issue(t):
    short = t['title']
    cats = t.get('issue') or []
    c = cats[0] if cats else 'backlash'
    gb = t.get('game')
    subj = _subject(t)
    top = _ptt_top(t)
    if t['id'].endswith(':reviews') and gb:
        out = [_A('safe', f'{subj}為什麼評價崩盤？一次看懂', '玩家在氣什麼、官方怎麼回應、現在還值不值得買',
                  (t.get('hooks') or ['近期評價大跌'])[0])]
        out.append(_A('contrast', f'{subj}負評轟炸：玩家任性，還是官方活該？', '把玩家的不滿一條條對照官方說法，給出有立場的結論',
                      (t.get('hooks') or ['近期評價大跌'])[0]))
        gap = _tc_gap(gb)
        if gap and gap[1] <= gap[0] - 10:
            out.append(_A('pz', f'繁中玩家好評只剩 {gap[1]}%：台灣人在不滿什麼？', '翻繁中評論找台灣玩家的抱怨（翻譯、伺服器、價格、外掛）',
                          f'Steam 全球 {gap[0]}% vs 繁中評論 {gap[1]}%'))
        else:
            out.append(_A('pz', f'{subj}還能不能買？台灣玩家的避雷指南', '用台灣售價與繁中評論給結論，順便自嘲「我的願望單也中槍」',
                          '台灣觀眾最在意「到底能不能買」'))
        return out
    out = [_A('safe', f'一次看懂：{short}', '時間線＋各方說法＋目前已知的事實', '多家媒體報導，資料完整')]
    con_t, con_h = _ISSUE_CON.get(c, _ISSUE_CON['backlash'])
    basis = (t.get('hooks') or [None])[0] or f'題材類型：{t.get("issue_label") or "產業事件"}'
    out.append(_A('contrast', (f'{subj}{con_t}' if gb else f'{subj}：{con_t}') if subj else con_t, con_h, basis,
                  risk='只用有證據的內容，不碰傳聞與人身攻擊' if c == 'culture' or '敏感題材' in (t.get('flags') or []) else None))
    if c == 'ownership':
        out.append(_A('pz', f'數位擁有權續集：{subj or "這次"}又是誰把遊戲收走了？', '接續頻道上一支 Torres 法案影片，延續系列觀眾',
                      '頻道已經做過數位擁有權題材，續集最容易帶回舊觀眾'))
    elif c == 'price' and gb and gb.get('price'):
        out.append(_A('pz', f'換算成台幣 NT${int(gb["price"]):,}：台灣玩家還買不買單？', '用台灣售價與薪水換算，自嘲錢包先投降',
                      f'Steam 台灣售價 {gb.get("price_text")}'))
    elif top and top.get('push', 0) >= 10:
        out.append(_A('pz', f'PTT 鄉民怎麼看{subj or "這件事"}？台灣玩家反應＋我的立場', '整理 PTT 熱門推文，再講自己站哪邊（有立場，不裝中立）',
                      f'PTT 相關討論最熱 {top["push"] if top["push"] < 100 else "爆"} 推'))
    else:
        out.append(_A('pz', f'{subj + "，" if subj else ""}台灣玩家該在意嗎？（我的錢包先發表意見）', '把國外的事拉回台灣玩家的角度：價格、中文、伺服器、會不會影響我們',
                      '台灣媒體報得少，台灣視角就是差異化'))
    return out


_HW_BIZ = re.compile(r'價|售|漲|降|銷|出貨|規格|上市|發表|price|sale|sold|spec|launch|ship', re.I)


def hardware(t):
    short = t['title']
    subj = _subject(t)
    text = ' '.join([t['title']] + [f.get('v', '') for f in (t.get('facts') or [])[:3]])
    if _HW_BIZ.search(text):
        out = [_A('safe', f'一次看懂：{short}', '規格、價格、上市時間、跟上一代差多少', '多家媒體報導')]
        out.append(_A('contrast', f'{subj or "新硬體"}對台灣玩家其實不划算？算給你看', '用台灣售價與實際遊戲需求算 CP 值', '硬體題觀眾最在意「值不值得」'))
        out.append(_A('pz', f'我的 RTX 5090 筆電 vs {subj or "新硬體"}：真的需要升級嗎？', '拿手上的筆電當對照組，自嘲「我已經升無可升」',
                      '你手上有 RTX 5090 筆電可以實測', need='需要你用筆電實測錄影'))
        return out
    # 不是新品或價格的硬體新聞（改裝、破解、驅動、技術）
    return [
        _A('safe', f'一次看懂：{short}', '這是什麼技術、誰做的、現在能不能用', '硬體技術新聞'),
        _A('contrast', '這件事對一般玩家的真正影響是什麼？', '把技術名詞翻成白話：會不會讓遊戲更好玩、更便宜、更危險', '多數觀眾只想知道「跟我有沒有關係」'),
        _A('pz', '用我的 RTX 5090 筆電試給你看', '能實測就實測，不能測就講清楚為什麼不建議跟著做', '你手上有 RTX 5090 筆電', need='需要你用筆電實測錄影'),
    ]


def showcase(t):
    name = t.get('event_name') or t['title']
    return [
        _A('safe', f'{name} 懶人包：5 分鐘看完重點', '依「最重要 → 最有趣」排序，每款一句話', '發表會後 48 小時是搜尋高峰'),
        _A('contrast', f'{name} 最大驚喜與最大失望', '有立場地評：哪個是真的好、哪個是空包彈', '觀眾愛看有立場的評論'),
        _A('pz', '台灣玩家最該關注的 3 款（附中文版與上市消息）', '只挑有繁中或台灣上市消息的', '台灣視角是差異化'),
    ]


def season(t):
    ev = t.get('event') or ''
    title = t['title']
    if 'nextfest' in ev:
        return [
            _A('safe', title, '推薦區＋隱藏好貨＋不推薦區，結尾一張總表', '新品節是頻道做過的固定格式'),
            _A('contrast', '新品節隱藏好貨：沒人在講但最好玩的 3 款', '專挑願望單排名不高但試玩口碑好的', '大作大家都會講，冷門好貨才有差異'),
            _A('pz', 'PZ 親自試玩：錄給你看（附有沒有繁中）', '自己錄試玩畫面，比官方預告有說服力、版權風險也最低',
               '試玩版只在新品節期間能玩', need='需要你親自試玩錄影'),
        ]
    if 'scream' in ev:
        return [
            _A('safe', title, '恐怖遊戲推薦與不推薦，附特價幅度', '萬聖節特賣是每年固定檔期'),
            _A('contrast', '不嚇人但超好玩的「假恐怖遊戲」', '專挑恐怖分類裡其實很歡樂的', '反差感強，標題好點'),
            _A('pz', '膽小鬼 PZ 實測：這幾款我真的玩不下去', '用自己被嚇到的反應當笑點，自嘲到底', '自嘲是頻道的招牌風格',
               need='需要你親自試玩錄影'),
        ]
    if ev.startswith(('winter', 'summer', 'spring', 'autumn')):
        return [
            _A('safe', title, '推薦區＋不推薦區＋特賣陷阱提醒', '頻道做過秋特，同格式可以直接套'),
            _A('contrast', '特價陷阱：這些「史低」其實不划算', '拿歷史價格與內容量對照', '觀眾最怕買貴'),
            _A('pz', '預算 NT$1,000 怎麼買最爽？', '用台幣預算排組合，順便自嘲購物車', '台灣觀眾用台幣想事情'),
        ]
    if 'tga' in ev and t['type'] == 'ranking':
        return [
            _A('safe', title, '每個主要獎項給預測與理由', '頒獎前一週搜尋最多'),
            _A('contrast', '最可能爆冷門的 3 個獎項', '用評分與話題度找出「應該贏但可能不會贏」的', '有立場比較好看'),
            _A('pz', '如果我是評審：PZ 版年度遊戲', '講自己真心的選擇，就算跟大家不一樣', '玩家視角、有立場'),
        ]
    if 'tga' in ev or t['type'] == 'showcase':
        return showcase(t)
    if 'blackfriday' in ev:
        return [
            _A('safe', title, '遊戲、主機、周邊分區推薦', '黑五是購物檔期'),
            _A('contrast', '這些黑五特價是假的', '比對平常價格，戳破假折扣', '觀眾最怕被騙'),
            _A('pz', 'PZ 的黑五購物車：我自己會買什麼', '把自己的購物清單攤開來，順便自嘲', '真心推薦最有說服力'),
        ]
    if t['id'].startswith('season:month:'):
        m = title.split(' ')[0]
        games = t.get('games') or []
        k = max(1, min(3, len(games) // 4)) if games else 2
        return [
            _A('safe', title, '每款一句話賣點，最後給「首發買／等特價／不用買」三分類', '頻道做過 11 月版，可以直接續做'),
            _A('contrast', f'{m}只有 {k} 款值得首發，其他等特價就好', '敢砍才有立場', f'這個月有 {len(games)} 款值得注意的新作'),
            _A('pz', f'{m}錢包保衛戰：預算 NT$3,000 怎麼花', '用台幣預算排出最划算的組合', '台灣觀眾用台幣想事情'),
        ]
    if t['type'] == 'weekly':
        return [
            _A('safe', title, '每件事 1 分鐘：發生什麼、為什麼重要', '每週固定單元'),
            _A('contrast', '本週最被高估與最被低估的新聞', '有立場地挑一則捧、一則吐', '有立場比較好看'),
            _A('pz', 'PZ 吐槽：本週最扯的一件事', '挑一則最荒謬的大吐特吐，自嘲收尾', '幽默自嘲是頻道風格'),
        ]
    if t['type'] == 'ranking':
        return [
            _A('safe', title, '倒數排名，每款附理由與上市資訊', '年底是榜單影片的旺季'),
            _A('contrast', '被低估的年度好作', '專挑評價高但討論少的', '大家都在講大作，冷門好作有差異'),
            _A('pz', 'PZ 個人版排行（附理由）', '講自己真心的排名，就算跟大家不一樣', '玩家視角、有立場'),
        ]
    return [
        _A('safe', title, '先講最重要的事實', '固定檔期'),
        _A('contrast', '大家都在推的，我反而不推', '有立場地反主流', '有立場比較好看'),
        _A('pz', '台灣玩家角度：該不該跟？', '拉回台灣視角', '台灣視角是差異化'),
    ]


def make(t: dict) -> list[dict]:
    kind = t['type']
    if kind == 'launch':
        return launch(t)
    if kind == 'sleeper':
        return sleeper(t)
    if kind == 'issue':
        return issue(t)
    if kind == 'hardware':
        return hardware(t)
    if kind == 'showcase' and not t.get('event'):
        return showcase(t)
    return season(t)
