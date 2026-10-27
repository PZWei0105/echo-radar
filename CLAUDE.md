# 回波 ECHO — 給接手的 Claude

使用者 PZ（痞子葳）沒有程式背景，所有回覆用繁體中文、白話。這個 repo 是他遊戲頻道的選題雷達 App（繼《冷眼》《玩真的》之後的第三個資訊 App）。

## 原則（不可違反）
- PZ 說「開工」才動手；討論階段不做任何檔案或改動。
- 評分與角度一律是固定規則，同樣的資料得到同樣的結果；分數一定要附「為什麼」（哪個測站、哪個數字）。
- 不收巴哈姆特（含 GNN）的內容與連結；台灣新聞統計也不計 GNN。
- 頻道規矩：只講有證據的內容，傳聞與人身攻擊不碰；課金手遊宣傳不推；沒玩過的遊戲不准寫成親身經驗。
- YouTube 擁擠度需要 API 金鑰，PZ 決定先不做；不要擅自加。
- 鑰匙（GitHub token）只存在使用者裝置的 localStorage，絕不寫進 repo 或資料檔。

## 怎麼運作
- `.github/workflows/echo.yml` 每小時第 47 分跑 `pipeline/build.py`（玩真的在第 23 分更新，晚一點才拿得到它的新聞）。
- 測站：`forreal.py`（玩真的新聞）、`steam.py`（排行、在線、評論、商店資料；評論用官方 Web API `IUserReviewsService/GetAppReviews`，舊的 `store.steampowered.com/appreviews` 在 2026-10-22 停用了）、`wiki.py`（Wikidata 對應＋瀏覽量）、`ptt.py`（pttweb.cc 鏡像站，PTT 官網擋 GitHub）、`social.py`（Google 趨勢、Google 新聞台灣版、Reddit RSS）。
- `entities.py` 建遊戲名冊並從標題認遊戲；`signals.py` 算每個測站的回波強度；`topics.py` 產生題目與四項分數；`angles.py` 產生角度三選一。
- 設定檔：`config/channel.json`（路線、權重、製作天數）、`config/videos.json`（頻道做過的影片）、`config/calendar.json`（Steam 官方檔期與大型活動）、`config/keywords.json`（爭議／硬體／發表會關鍵字）、`config/aliases.json`（簡稱與 PTT 叫法）。
- 跨輪資料存在 `state` 分支（快取、在線人數與排名歷史、PTT 文章、題目首次出現時間），每次只留一個 commit。`state` 分支的 `out/` 是最近一次輸出，`runs.json` 是執行紀錄、`health.json` 是各測站狀態。
- 使用者的方向設定在 `user` 分支的 `prefs.json`（App 用遙控鑰匙寫入；build 只讀關注清單）。
- 推到 `main` 時執行 `control/job.json` 指定的工作：`build`／`deploy`（只改介面）／`probe`（實測新來源，原始回應存到 `probe-results` 分支）。
- 雲端 Claude 工作環境連不到這些網站，要實測新來源就用 `probe`。

## 容易改壞的規則
- App 會用使用者的方向設定重算分數：`topics.py` 的 `_deadline_timing`、`total`、`grade` 跟 `site/app.js` 的 `deadlineTiming`、`evaluate` 必須一模一樣（改一邊就要改另一邊）。等級照畫面上顯示的整數判斷。TOP 5 的挑法（`build.pick_top` 與 `evaluate` 最後一段）也一樣：照（最低分, 同路線／同題型上限）分輪挑 (70,3)→(58,2)→(58,3)→(58,不限)→(0,2)→(0,不限)，可拍以上挑完才輪到觀望題。門檻與排序用沒四捨五入的 `total`（`score` 是四捨五入到一位小數給人看的，拿來判斷會差 1 分）。
- 「為什麼是現在」（`t.why`）的最後一句一定是時效，跟 `reasons.timing[0]` 同一句；App 的 `whyNow` 靠這個把它換成重算後的時效（過了午夜、改了製作天數）。括號裡只留漲跌幅（含 %）與開頭的（遊戲名），切句只在括號外切（`topics._outside`）。
- 沉睡題只收 Steam 類型是 game、不是軟體、不是長青免費遊戲、有正式名稱的；首發題也不收軟體與沒名稱的。軟體看商店分類代號 50～60（`genre_ids`），舊快取看 Steam 繁中商店的分類名稱（「動畫和建模」「工具」…，不是自己翻的），同時有動作、角色扮演這類遊戲分類的照樣算遊戲（`topics.is_software`）。最多人玩榜上的遊戲也要抓商店資料；第一次抓到的那輪還沒套上名稱，下一輪才出題。同系列新作已經上市時（`topics.same_series`），老遊戲的在線暴增算新作的熱度（《現代戰爭 4》要從《決勝時刻》主程式進去玩），不出「又紅起來了」題。
- 新聞題（硬體、發表會、沒對到遊戲的爭議）的代號靠 `topics_seen.json` 裡記的報導 id 沿用；使用者的靜音、想拍、角度、筆記都掛在題目代號上，不要隨便改代號的編法。
- 「已經做過」：檔期（events）與月份（months）一對一、永遠算；新聞關鍵字（keys）只看製作中或 30 天內上片的影片，更早的只提示「可以做續集」。
- `config/keywords.json` 的英文詞會自動加上「前後不能接英數字」的邊界，要對複數或變化形就自己寫出來；`issue_priority` 決定一則新聞同時符合多個分類時用哪一個切角。
- Steam 活動是太平洋時間早上 10 點開始，台灣是隔天凌晨 1 點：題目與預報一律用 `topics.ev_days` 換成台灣日期。
- 方向設定同步是逐筆合併（企劃、觀看數、靜音、角度、筆記照題目合併；刪除會留 `del` 紀錄），不是誰最後存誰贏。
- 在線人數超過 6 小時沒更新、評論超過 24 小時沒更新就不當成「現在」的數字；快取每輪會清掉太舊的。

## 本機測試（不連網）
- 全部測試：`python3 tests/run_all.py --ui`。整輪後台（`tests/fixtures/raw` 錄好的真實回應＋`tests/fixtures/state` 種子狀態，時間固定在台灣時間 2026-10-20 18:47）、各模組單元測試、App 與後台規則一致（`tests/app_check.mjs`），`--ui` 再用 Chromium 開網站（`tests/ui_check.mjs`：手機 390／桌機 1440 截圖到 `tests/out/`、每題都打得開、掃描按鈕、離線、更新紀錄）。改完任何東西都要全部通過才推。
- 只跑一輪看結果：`python3 tests/local_build.py <輸出資料夾>`。
- 錄好的回應每個網址只有一份，所以離線測試裡每款遊戲的在線人數、維基瀏覽量都一樣；那是測試資料的限制，不是錯誤。要換新的回應：用 `probe` 工作抓，從 `probe-results` 分支取出後 gzip 成 `tests/fixtures/raw/<名稱>.txt.gz`，在 `tests/fake_http.py` 的對照表加一行。

## 介面
- 主題：氣象署雷達回波圖。深夜海色、細海岸線、一格一格的回波色階（青→綠→黃→橘→紅→洋紅→紫），中心是台灣。
- 回波強度＝總分；離中心的距離＝離最晚開工日還有幾天；扇區＝路線。
- 純 HTML／CSS／JS，沒有外部程式庫；數字用 Archivo（Google Fonts），中文用系統字。
- 新的介面檔案要加進 `site/sw.js` 的 `SHELL`（離線外殼）。
- 改了 App 就在 `site/updates.js` 最前面加一筆白話的更新紀錄（改了什麼、在哪裡、怎麼用；不寫技術細節）。App 改版後第一次打開會跳出來一次，「方向」最下面的「更新紀錄」可以再看。
- 待辦、已知問題、已完成都記在 `ROADMAP.md`；大改版要先問 PZ，提案寫進 ROADMAP。
