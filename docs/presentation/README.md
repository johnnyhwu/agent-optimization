# Skill Studio 內部分享簡報

30 頁，25–30 分鐘，**投影片內容是英文**（講者備忘維持中文）。
`skill-studio.html` 是本體，用瀏覽器開就能播，**不需要任何伺服器、不連外網**
（字體全用系統內建，論文圖在同層的 `fig/`）。

| 檔案 | 用途 |
|---|---|
| `skill-studio.html` | 投影片本體。要改內容就改這一份 |
| `fig/` | SkillOpt 論文的圖表（來自 `johnnyhwu.github.io` 上那篇中文長文） |
| `assets/` | **你自己補的系統截圖**，見下方 |
| `build_standalone.py` | 打包成一個真正的單一檔案（圖片全部 base64 內嵌）|
| `skill-studio-standalone.html` | 上面那支腳本的產物，**一個檔案帶著走** |

要帶去別台電腦報告，複製 `skill-studio-standalone.html` 一個檔案就夠了。

## 操作

| 鍵 | 作用 |
|---|---|
| `→` `Space` `PgDn` / `←` `PgUp` | 下一頁 / 上一頁（滑鼠左邊 1/4 往回，其餘往前）|
| `Home` / `End` | 第一頁 / 最後一頁 |
| `O` | 總覽，點標題直接跳頁（`Esc` 關閉）|
| `S` | 講者備忘 —— 每頁都寫了要講什麼，投影前自己看，別投出去 |
| `T` | 計時器開關（`Shift+T` 歸零）|
| `P` | 列印 / 存成 PDF |

網址列的 `#12` 就是頁碼，重新整理不會跑掉，也可以直接把某一頁的連結傳給別人。

## 換上你自己的截圖

投影片裡有 5 個灰色虛線框，是留給系統截圖的。把 PNG 放進 `assets/`，**檔名照下表**，
重新整理就會自動換上去 —— 不用改任何一行 HTML。沒放的就維持示意框，不會壞掉。

| 檔名 | 第幾頁 | 建議截什麼 |
|---|---|---|
| `assets/shot-01-run-config.png` | 4 | run history 裡點開某個 run 的唯讀設定面板（endpoints / models / timeout / concurrency）|
| `assets/shot-02-diagnosis.png` | 5 | 錯題的三欄檢視：answer、diagnosis，以及標著 high / med / low 的 span 列表 |
| `assets/shot-03-playground.png` | 6 | Playground：skill 檔案編輯器，旁邊是 Agent → Judge → Trace → Diagnosis 的階段進度 |
| `assets/shot-04-shortlist.png` | 7 | shortlist 轉 eval set 的對話框：unverified 標記、Draft from trace、可勾選複製的既有 set |
| `assets/shot-05-optimize-chart.png` | 8 | 優化 run 的逐 step 訓練 / 驗證折線圖，或某個 step 的並排 skill diff |

截圖用 **16:9 左右的比例**最不會被裁到；框大約是 520×450，太窄或太高的圖會被縮得很小。

放好之後重新打包單一檔案：

```bash
python3 build_standalone.py
```

## 在手機上看

投影片是 1280×720 的橫向版面，所以**手機直立拿的時候會自動把整頁轉 90 度**填滿螢幕 ——
把手機打橫，每一頁就是正的。手機上用點的翻頁：**點右邊往前、點最上面那一條（直立時）或最左邊
四分之一（橫拿時）往回**；按鍵提示列在直立時會自動隱藏。

要在手機上開，有兩條路：

1. **Artifact 連結**（最方便）—— 把 `skill-studio-standalone.html` 發布成 claude.ai 的私人
   artifact，用手機瀏覽器開那個連結就是一份會動的投影片。之後補了截圖要重新發布到同一個網址。
2. **PDF** —— 見下一節。手機原生就打得開，也最適合會議前寄給自己。

⚠️ 把 HTML 當成「檔案」傳到手機上（聊天室附件、雲端硬碟預覽）通常**不會**執行 JavaScript，
會看到一片空白。要嘛用上面的連結，要嘛用 PDF。

## 存成 PDF

按 `P`（或 `Ctrl/Cmd + P`）→ 目的地選「另存為 PDF」→ **版面選橫向、邊界選無、勾選背景圖形**。
每一頁會剛好是一張投影片。講者備忘不會被印出來。

## 內容結構

| 頁 | 段落 | 時間 |
|---|---|---|
| 1–2 | 封面、agenda | |
| 3–12 | Part 1：那個迴圈的四個斷點，以及 Skill Studio 各自怎麼補；系統全貌；成效與邊界 | 8 min |
| 13 | Part 2：切去瀏覽器實機 demo | 5 min |
| 14–28 | Part 3：SkillOpt 演算法逐格拆解、它到底學出什麼、實驗結果、我們的 routing mode 為什麼偏離論文 | 8 min |
| 29–30 | Part 4：探索中的下一步、收尾 | 2 min |

第 18–23 頁是同一張管線圖輪流點亮其中一格（`.pipe` 的 `data-stage`），改圖只要改
HTML 最下面那個 `<template id="tpl-pipe">` 一處。

## 內容出處

投影片上的說法都對得回 repo 裡的文件，要回答細節問題時可以往這裡查：

- 痛點與 Langfuse 的能 / 不能：`backend/docs/spec.md` §1.2、§1.3
- Optimize 實際做到哪裡、刻意不做什麼：`backend/docs/spec.md` §2.3a、§15.1
- 已知風險（診斷準確度未驗證、錯誤未必歸得到單一 span…）：`backend/docs/spec.md` §16
- routing mode 的三個論證：`backend/docs/routing-optimization.md`
- agent server 端要做的兩件事：`backend/docs/agent-server-api.md`
- SkillOpt 演算法的中文說法：<https://datasciocean.com/paper-intro/skillopt/>

`fig/` 裡有四張目前沒被用到的備用圖：`table4.png`（遷移）、`table5.png`（優化器強度）、
`table6.png`（成本）—— 第 27 頁改成用數字講、只留一張看得清楚的圖；以及 `table3.png`
（論文的 component ablation：learning-rate 形式、rejected buffer、epoch 層級 slow / meta update
各自拿掉會掉多少分）。如果現場有人追問「這些機制真的每一個都有用嗎」，`table3.png` 就是答案，
可以臨時貼到第 24 頁去。
