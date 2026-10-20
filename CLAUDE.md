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
- 測站：`forreal.py`（玩真的新聞）、`steam.py`（排行、在線、評論、商店資料）、`wiki.py`（Wikidata 對應＋瀏覽量）、`ptt.py`（pttweb.cc 鏡像站，PTT 官網擋 GitHub）、`social.py`（Google 趨勢、Google 新聞台灣版、Reddit RSS）。
- `entities.py` 建遊戲名冊並從標題認遊戲；`signals.py` 算每個測站的回波強度；`topics.py` 產生題目與四項分數；`angles.py` 產生角度三選一。
- 設定檔：`config/channel.json`（路線、權重、製作天數）、`config/videos.json`（頻道做過的影片）、`config/calendar.json`（Steam 官方檔期與大型活動）、`config/keywords.json`（爭議／硬體／發表會關鍵字）、`config/aliases.json`（簡稱與 PTT 叫法）。
- 跨輪資料存在 `state` 分支（快取、在線人數與排名歷史、PTT 文章、題目首次出現時間），每次只留一個 commit。`state` 分支的 `out/` 是最近一次輸出，`runs.json` 是執行紀錄、`health.json` 是各測站狀態。
- 使用者的方向設定在 `user` 分支的 `prefs.json`（App 用遙控鑰匙寫入；build 只讀關注清單）。
- 推到 `main` 時執行 `control/job.json` 指定的工作：`build`／`deploy`（只改介面）／`probe`（實測新來源，原始回應存到 `probe-results` 分支）。
- 雲端 Claude 工作環境連不到這些網站，要實測新來源就用 `probe`。

## 容易改壞的規則
- App 會用使用者的方向設定重算分數：`topics.py` 的 `_deadline_timing`、`total`、`grade` 跟 `site/app.js` 的 `deadlineTiming`、`evaluate` 必須一模一樣（改一邊就要改另一邊）。等級照畫面上顯示的整數判斷。TOP 5 的挑法（`build.pick_top` 與 `evaluate` 最後一段）也一樣。
- 新聞題（硬體、發表會、沒對到遊戲的爭議）的代號靠 `topics_seen.json` 裡記的報導 id 沿用；使用者的靜音、想拍、角度、筆記都掛在題目代號上，不要隨便改代號的編法。
- 「已經做過」：檔期（events）與月份（months）一對一、永遠算；新聞關鍵字（keys）只看製作中或 30 天內上片的影片，更早的只提示「可以做續集」。
- `config/keywords.json` 的英文詞會自動加上「前後不能接英數字」的邊界，要對複數或變化形就自己寫出來；`issue_priority` 決定一則新聞同時符合多個分類時用哪一個切角。
- Steam 活動是太平洋時間早上 10 點開始，台灣是隔天凌晨 1 點：題目與預報一律用 `topics.ev_days` 換成台灣日期。
- 方向設定同步是逐筆合併（企劃、觀看數、靜音、角度、筆記照題目合併；刪除會留 `del` 紀錄），不是誰最後存誰贏。
- 在線人數超過 6 小時沒更新、評論超過 24 小時沒更新就不當成「現在」的數字；快取每輪會清掉太舊的。

## 本機測試
- 把 `probe-results` 分支解開到 scratchpad，`python3 tests/local_build.py <輸出資料夾> <state 資料夾>` 用原始回應跑一整輪（不連網）。
- 介面：把輸出的 `data/` 放到 `copy_site` 產生的資料夾，起本機伺服器，用 Playwright 以手機寬度截圖檢查。

## 介面
- 主題：氣象署雷達回波圖。深夜海色、細海岸線、一格一格的回波色階（青→綠→黃→橘→紅→洋紅→紫），中心是台灣。
- 回波強度＝總分；離中心的距離＝離最晚開工日還有幾天；扇區＝路線。
- 純 HTML／CSS／JS，沒有外部程式庫；數字用 Archivo（Google Fonts），中文用系統字。
