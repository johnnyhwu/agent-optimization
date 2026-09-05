# Skill Studio — 系統規格與實作現況

> **這份文件是什麼**：本專案唯一的權威技術文件。它同時交代三件事——
> **① 這個系統要解決什麼問題、② 為什麼是這樣設計的、③ 現在到底做了什麼、哪些沒做。**
>
> **自包含**：讀者不需要看過任何前文，也不需要打開 codebase，就能完整理解專案狀況與目標。
> 文中提到檔案路徑只是為了讓要動手的人知道去哪找，不看也不影響理解。
>
> **取代舊版規格**：這份文件原本叫 `spec_v2.md`，寫來取代一份「討論紀錄 + 事後補的實作現況」
> 混在一起的舊文件——那份的前半段保留了後來被推翻的假設，讀錯順序會被誤導。
> 舊文件**已從 repo 刪除**，只在 git 歷史裡（`git show 46b0e58:docs/spec.md`）。
>
> ⚠️ **一個閱讀陷阱**：本專案的**程式碼註解大量引用舊文件的章節編號**
> （`§6.9`、`§6.13`、`§9.18a`…，全 repo 約 179 處）。
> **那些編號指的是已刪除的舊文件，不是這一份**——而且兩份的編號不相通：
> 註解裡的 `§6.13`（三層下鑽的前端 IA）在本文件是 [§10.1](#101-側邊欄的三個-section)，
> 本文件的 §6 講的是 run 的生命週期。看到程式碼裡的 `§` 請當成歷史標記，
> 不要拿本文件的同號章節去對。本文件在對應處會標注「（程式碼註解稱為 §X）」方便對照。
> 本文件內部的 `§` 引用一律指**本文件**的章節。
>
> > 要根治的話得把那 179 處註解改寫成本文件的編號——那是一次獨立的機械式改動，
> > 刻意沒有和這次的文件更名混在一起。
>
> **對照的操作手冊**：repo 根目錄的 `README.md` 是「怎麼跑起來、怎麼接真實服務、怎麼部署」的操作手冊。
> 本文件是設計與實作紀錄，兩者互補不重複。
>
> **`docs/` 底下只有這一份。** 原本另有一份寫給 agent server 團隊的端點契約
> （`agent_server_stage4_endpoints.md`）。給 agent server 實作者的端點契約現在是
> [`docs/agent-server-api.md`](./agent-server-api.md)（英文、自包含、唯一來源），[§17](#17-對-agent-server-端的相依需求) 只留摘要與指標——
> 兩份文件描述同一組端點時，遲早會有一份先過期，而讀者無從得知是哪一份。

**目錄**

| 章 | 內容 |
|---|---|
| [1](#1-背景與問題) | 背景與問題（為什麼要有這個系統） |
| [2](#2-產品願景與分階段策略) | 產品願景與分階段策略（現在在哪一階段） |
| [3](#3-系統架構) | 系統架構、七個 seam、correlation 機制 |
| [4](#4-關鍵設計決策) | 關鍵設計決策與理由 |
| [5](#5-資料模型) | 資料模型（7 張表 + 記憶體 store） |
| [6](#6-一次-eval-run-的生命週期) | 一次 eval run 的生命週期與失敗策略 |
| [7](#7-stage-4playground) | Stage 4：Playground |
| [8](#8-llm-契約judgediagnosis-與-synthesis) | LLM 契約（judge、diagnosis 與 synthesis） |
| [9](#9-api-全表) | API 全表與權限 |
| [10](#10-前端資訊架構) | 前端資訊架構、三個關鍵機制、排版陷阱與設計系統 |
| [11](#11-權限身分與並發) | 權限、身分（Keycloak SSO）與並發 |
| [12](#12-設定總表) | 設定總表（環境變數） |
| [13](#13-如何執行從-fake-到-real) | 如何執行、從 fake 到 real、**部署形態** |
| [14](#14-測試與驗證現況可信度地圖) | **測試與驗證現況（可信度地圖）** |
| [15](#15-明確尚未做的) | **明確尚未做的** |
| [16](#16-已知風險與未解問題) | 已知風險與未解問題 |
| [17](#17-對-agent-server-端的相依需求) | 對 agent server 端的相依需求（**完整端點契約**）|
| [18](#18-給接手者的下一步建議) | 給接手者的下一步建議 |

**名詞表**（全文一致使用）

| 名詞 | 意思 |
|---|---|
| **agent** | 待評估的對象：一個 stateless 的 domain agent，公司內部既有系統，**不在本 repo 內** |
| **agent server** | host 該 agent 的 HTTP 服務：一個 OpenAI chat completions 端點，外加選配的 skills 端點。**不在本 repo 內** |
| **skill** | 開發者手寫的「某類問題的作戰手冊」。agent 每次先挑一個 skill，再照它 tool-calling |
| **trace** | agent 回答一題所產生的完整執行紀錄，存在 Langfuse。由多個 **span** 組成 |
| **span** | trace 內的一步。實務上就是一次 LLM 呼叫（tool calling 或最終回答生成） |
| **eval set** | 一組題目。每題含 question、期望答案、期望推理流程、所屬 skill |
| **run** | 對某個 eval set 的一次完整評估執行。**是歷史紀錄**，不可重寫 |
| **judge** | LLM-as-judge：拿 agent 的回答與期望答案比對，判 correct / incorrect |
| **diagnosis** | 對錯題的 trace 做的 LLM 分析，指出可疑的 span 與原因。**是線索，不是判決** |
| **attempt** | Playground 裡的一次單題試打。**是拋棄式實驗**，不落庫 |
| **seam** | 一個外部依賴的抽換介面（Python Protocol），fake 與 real 兩套實作並存 |
| **correlation_id** | 平台為每題產生的 id，注入 agent metadata，agent 用它當 Langfuse trace id |

---

## 1. 背景與問題

### 1.1 既有環境（本 repo 之外）

- 有一個 **stateless domain agent**：每次收到問題都重新初始化，沒有跨 request 記憶。
  它的推理邏輯是 **tool-calling → tool-calling → … → 生成回答**。
- agent 有多個開發者預寫好的 **skill**，每個 skill 對應一類 domain-specific 問題。
  使用者問到某領域時，agent 先讀取對應 skill，再開始一連串 tool calling。
- agent 由一個 **agent server** host。每次執行都把完整 trace 送進 **Langfuse**；
  一個 trace 內有多個 span，每個 span 是一次 LLM 呼叫。
- Langfuse 上看得到每個 span 的 input / output / model / token 用量 / cost / latency /
  起訖時間 / parentObservationId / level / statusMessage / metadata。

> **歷史註記**：早期設計假設 agent server 走 Google A2A(Agent-to-Agent) protocol。
> **後來 agent server 端改為 OpenAI chat completions**（平台專屬欄位收在 `skill_studio` 底下），
> 本平台也隨之改用純 HTTP client。correlation 機制不變。舊文件 §1.1／§6.2 描述的 A2A 已非現況。

### 1.2 原本的做法與痛點

開發者原本自建了一個 external eval server：Langfuse 觸發 → server 把每題打進 agent →
拿到 response → 用 LLM-as-judge → 得到 score → 回傳，於是 Langfuse 上能看到每題 correct / incorrect。

**痛點**：對於被判錯的題目，開發者必須**人工去翻該題背後的 trace，逐個 span 檢查是哪裡出錯**。
一天查十題，每題翻十幾個 span，這件事極度耗時。

> 開發者要的不是「知道哪題錯了」——那已經有了。他要的是**「這題為什麼錯、錯在哪一步」**。

### 1.3 Langfuse 能給什麼、不能給什麼

已查證 Langfuse open-source codebase 的結論：

**✅ Langfuse 可以直接當後端支撐的**
- 抓完整 trace + span tree：`GET /api/public/traces/{traceId}`、
  `GET /api/public/v2/observations?traceId=…`。**足以在外部 UI 完整重建 span 列表**，
  並顯示等同 Langfuse span detail 的所有資訊。
- 存 eval set：Dataset Item 有 `input`、`expectedOutput`，加上完全自由的 `metadata` JSON。
- 把題目連到 trace：Dataset Run Item 用 `traceId`。
- 對單一 span 標記正確/錯誤 + 原因：Scores API 支援 `observationId` + `comment` + `source`。
- 內建 LLM-as-judge（可指向自訂 `baseURL` 的 self-hosted 端點）。

**❌ Langfuse 沒有、必須自建的**
- 「分析錯題 → 定位錯誤 span → 給出原因」的**分析引擎**。
- 想要的那套**客製 UI**：即時進度、per-span 標記與熱點、可編輯標註、三欄下鑽。
  Langfuse 的 trace UI 無法疊加自訂的 per-span 資訊。
- **Skill 自動優化（SkillOpt）**整套，以及改 skill 重跑實驗、skill 版本管理。
- ⚠️ Langfuse **沒有**「trace 完成 → 主動 webhook 通知外部」的原生機制。
  原本那條「Langfuse 觸發 → 打自建 server」其實是開發者自己接的 glue。

**→ 結論（已定案並實作）**：做一個**獨立 app + 自有 DB**，把 Langfuse 當 trace 的資料骨幹，
透過 public API 讀 trace。**不 fork Langfuse UI**。orchestration 由新平台主導，不依賴 Langfuse 觸發。

---

## 2. 產品願景與分階段策略

### 2.1 完整願景（六個功能）

1. **上傳 eval set**：每題含 `question`、`ground_truth_response`、
   **`ground_truth_reasoning_process_description`**（一段自然語言、**粗粒度**的理想推理流程描述），
   以及該題所屬的 `skill`。
   > 為什麼要 reasoning description：讓系統能自動定位錯誤 span，而開發者**不必**把完整的
   > reasoning ground truth 逐步 label 出來（那太花時間）。
2. **觸發 evaluation + 即時進度**：按下 eval → 進度條即時呈現每題 score / correct or incorrect。
3. **錯題的 trace 錯誤定位（核心價值）**：點錯題 → 呈現該題 trace → 標出可疑 span →
   點 span 看 input / output / token 以及**「為什麼他可能是錯的」**。
4. **人工修正標註**：開發者可修改錯誤原因、把 span 重標為正確、把另一個 span 標為錯誤。
   這些人工修正會成為後續 skill 優化的高品質訊號。
5. **Skill Evolving（自動優化 skill）**：參考 SkillOpt 演算法。選多個 eval result → 合併去重 →
   依 skill 分組 → 跑優化 → 得到新的 optimized skill → 可存回 agent server。
6. **Skill 實驗（重跑）**：讓 agent 用改過的 skill 重新執行相同題目，看 trace 有無不同。

### 2.2 為什麼分階段（重要的判斷）

整個系統的價值集中在 2.3（錯誤定位）與 2.5（SkillOpt），而**兩者都建立在同一個脆弱假設上**：

> 「錯誤可歸因到單一 span，且該錯誤可靠改 skill 修好。」

這個假設有明確的裂縫（詳見 [§16](#16-已知風險與未解問題)）：compounding / emergent error、
錯誤其實在 tool 或 base model 而不在 skill、多條同樣正確的路徑。
因此**刻意不一次做完**，改採分階段交付，把不確定性高的功能後推。

### 2.3 階段地圖與現況

| 階段 | 內容 | 狀態 |
|---|---|---|
| **Stage 1** | 錯題自動抓 trace → LLM 白話診斷可疑 span → 跳轉該 span 顯示 input/output/token。**不做**機率、熱點、人工重標、SkillOpt | **✅ 已實作**（POC 完整可跑）|
| **Stage 2** | per-span 出錯機率熱點、reasoning ↔ span 的 step 拆解軟對齊、可編輯標註 / 重標 span（並寫回 Langfuse Score）| ❌ 未開始 |
| **Stage 3** | **Optimize**：SkillOpt 自動優化——把 skill 文件當可訓練參數，用 epoch / step / learning rate / validation gate 訓練它，產出可下載的 optimized skill 目錄 | **✅ 已實作**（見 §2.3a）|
| **Stage 4** | **Playground**：單題即時試打 + per-request **workspace override**（agent 的 config 與 skill 檔）的迭代沙盒，加上把試出來的題目**升級成新 eval set** 的 shortlist | **✅ 已實作** |

**Stage 4 不在原本的三階段藍圖裡**，是後來新增的。它補的是 Stage 1 動線末端的缺口：
開發者看完診斷、心裡有了「如果 skill 這樣改應該就會對」的假設之後，
**原本沒有任何便宜的方式驗證那個假設**——唯一的路是改 eval set、跑一整個 run。
Stage 4 就是那條便宜的路。它刻意**不碰** Stage 3 的難題（不寫回、不做「驗證改善」的自動判定）。

它後來還長出第二個用途：在 playground 問的題目，通常是**現有 eval set 沒有的**——
那正是它被拿到這裡問的原因。shortlist（§7.6）讓那幾題不必被手抄進上傳檔才留得下來。

> **Stage 1 的價值主張**：它解決 §1.2 痛點的約 80%，而且不需要機率、熱點或 SkillOpt。
> Stage 1 的診斷準確度在真實資料上跑起來之後的觀察，**是決定要不要投入 Stage 2 的關鍵依據**。

#### 2.3a Stage 3（Optimize）實際做到哪裡

Stage 4 補的是「手動驗證一個假設」，Stage 3 補的是**把那個迴圈自動化**：改 → 量 → 再改，
原本每一輪都要人手動跑。演算法沿用 [microsoft/SkillOpt](https://github.com/microsoft/SkillOpt)
（vendored，見 `backend/app/optimizer/VENDORED.md`），**只有兩件事沿用本系統**：打 target agent
取得 final response，以及 LLM judge 評分。反向傳播（reflect）、gradient 聚合、learning rate
裁切、skill 更新、validation gate 全部來自 SkillOpt。

**已實作**

| 部分 | 內容 |
|---|---|
| 資料 | 七張 `optimization_*` 表（migration `0009`）。**不動任何既有資料表**，Optimize 指向 eval 資料，eval 資料從不指回來 |
| 兩種模式 | `isolated`（只送目標 skill、優化 body、保護 frontmatter）與 `routing`（送全部 skill、優化 frontmatter description、保護 body）。gate 在 routing 模式多一道 activation 守門 |
| routing 已離開 SkillOpt 的演算法 | 被優化的是一行 description 而不是一份 body，所以 minibatch、merge 與 trajectory 各自的價值都變了：routing 每個 step 只做**一次** analyst 呼叫、成功與失敗**一起看**、送的是一份路由 confusion matrix 而不是 trajectory，並且不會走到 merge 與 ranking。三個論證與其代價寫在 [`docs/routing-optimization.md`](routing-optimization.md)。`isolated` 完全不受影響 |
| 迴圈 | step 0 baseline → 每個 step：訓練 minibatch rollout → reflect → aggregate → clip 到 learning rate → apply → 驗證 rollout → gate。可取消、可續跑（backend 重啟後是 `interrupted` 而非 `failed`）|
| 提前結束 | 四個條件、一套機制（`optimizer/stopping.py`）：訓練批次／驗證 split 的系統錯誤率連續超標、連續 N 步沒有新的最佳分數、驗證分數達標。預設值來自環境變數，wizard 可改，run 頁上以「Stops when」列出並附即時計數，結束後 `optimization_runs.stop_reason` 記下是哪一個。**系統錯誤只花掉那一個 step**：訓練批次超標就整步略過（不呼叫 analyst、不買驗證 rollout），驗證 split 超標就把候選原封不動丟掉（`gate_action='reject'`, `gate_reject_reason='val_errors'`）——沒有分數就沒有 gate 可言。超標的 rollout 不寫入任何 hard/soft，所以它既不會畫在圖上、不會進 skill hash 快取、也不會被 gate 讀到。唯一仍會讓整個 run `failed` 的是 baseline 量不準，因為之後每個數字都是跟它比較 |
| activation 偵測 | 兩個策略（tool call 路徑 + skill 內容逐字比對），**不注入任何 token**；`activation = A OR B`，兩者都測不出來時回報「未知」而不是「否」|
| 觀測 | 六步 wizard、逐 step 圖表、Part 1（rollout + 送給 analyst 的 prompt 與截斷帳本）、Part 2（並排 diff + 未套用的 edit + 答案硬編告警）。圖表的 y 軸預設貼合資料（最小跨度 20 個百分點，非全幅時軸標題註明 zoomed）、canvas 依 step 數加寬到每個 step 至少 20 單位（畫布高度固定，出現水平捲軸不會改變圖的大小）、legend 可開關單一系列、方向鍵可釘住 step；滑鼠停留的讀數是圖上方一條固定高度的列（不浮在圖上、不遮資料點，也是鍵盤操作唯一看得到讀數的地方），gate 判定的用字只有 `optimize_gate_label.js` 一處；header 另有這個 run 的總耗時與結束條件 |
| 產出 | zip（skill 目錄 + `manifest.json`，含 warnings），**人工放回 agent server** |
| 刪除 | `DELETE /optimization/runs/{id}`，**只有建立者**（與 cancel / resume 同一條線），且 `running` / `pending` 一律 409——背景 task 已經啟動但還沒把狀態翻成 `running` 的那段窗口，刪掉會讓它拿著不存在的 id 繼續買 agent 呼叫。刪除走 `services/deletion.py` 的 bulk delete（最深的先刪），不走 ORM cascade：一個 run 的子列是萬級的 |

**刻意不做**（見 §15.1）：skill 自動寫回 agent server、test split、整份重寫模式、
多次取樣壓抑溫度雜訊。slow update / meta skill 已接線但預設關閉。

> **答案硬編是這一段的主要風險，防線有三層**：analyst prompt 明文禁止（沿用上游）、
> held-out validation split（結構性防線）、以及 diff 上的逐字比對告警——後者的計數在候選寫入時
> 就算好並存在 step 列上，因為 run 總覽頁在跑的時候會反覆重載，讀取時才算等於每次重載都做一輪
> 全 run 的 diff。

### 2.4 產品邊界：skill 是唯一的一級受詞

動詞可以一直加——量（Evaluation）、試（Playground）、練（Optimize）、削（Compaction）、
餵（Data Curation）——但**受詞永遠是 skill**。

任何功能提案先問一句：**它的受詞是 skill 嗎？** 是就進來，不是就該去別的系統。
Data Curation 通過這一題（eval set 是 skill 優化時的訓練資料，只是從另一端接近同一個受詞）；
優化 prompt、tool description、memory 則不通過——那是另一個系統。

這條界線目前守得住：`routing` 模式優化的是 frontmatter 的 description，那仍在 `SKILL.md` 裡面。
會第一個把它撐破的，是「優化 agent 怎麼選 skill」再往外走一步的需求——真的走到那一步時，
要改的是這一節，不是默默讓它失效。

> 這條約束是產品名的來源：本系統對外叫 **Skill Studio**，與部門既有的 **Skill Marketplace**
> 配對——工作室做，市集賣。名字綁的是受詞，所以動詞增加時名字不會過期。

---

## 3. 系統架構

### 3.1 拓樸

```
                          ┌──────────┐
                          │ Keycloak │  OIDC（AUTH_MODE=keycloak，見 §11.2）
                          └────┬─────┘
        瀏覽器 ──登入─────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│ Eval Platform（本 repo，獨立 app）                            │
│                                                              │
│  nginx（僅部署形態，見 §13.3）                                  │
│    /        → 打包好的靜態 bundle                              │
│    /api/*   → 轉給 backend（開發形態沒有這一層）                 │
│                                                              │
│  Frontend (React + Vite)                                     │
│    側邊欄三個 section（Evaluation / Playground / Optimize）      │
│    ├── Evaluation：三層下鑽（卡片 → run 歷史 → 三欄詳情）        │
│    ├── Playground：單題試打 + agent workspace 編輯 + shortlist   │
│    └── Optimize：SkillOpt 訓練迴圈 + 圖表 + rollout/diff（§2.3a）│
│                                                              │
│  Backend (FastAPI async)                                     │
│    ├── Orchestrator：讀 eval set → 逐題打 agent → judge →      │
│    │                 等 trace → 診斷 → 落庫 → SSE 推進度        │
│    ├── Playground：單題版本，狀態只在記憶體                      │
│    ├── Optimizer：rollout → reflect → gate 的訓練迴圈，背景執行   │
│    └── 七個 seam（fake / real 各一套）+ 身分 seam（§11.2）        │
│                                                              │
│  App DB (PostgreSQL)：Langfuse 沒有的概念 + 指回 Langfuse 的索引  │
└───┬───────────────┬─────────────────┬────────────────────────┘
    │ 驗 token      │ 讀 trace         │ POST chat completions（HTTP）
    │ （JWKS）      │（HTTP public API）│
    ▼               ▼                 ▼
┌──────────┐   ┌──────────┐    ┌──────────────────┐
│ Keycloak │   │ Langfuse │◀──trace 寫入──│ Agent Server │
└──────────┘   └──────────┘    │  (stateless agent)│
                               └──────────────────┘
   ┌──────────────────┐
   │ 員工目錄 HR API   │ ← 分享時查核 username（§11.3）
   └──────────────────┘
```

**技術棧**
- **Backend**：FastAPI（async）+ SQLAlchemy 2（async, asyncpg）+ Alembic（migration 用 sync psycopg）
  + Pydantic v2。run 進度用 **SSE**（`sse-starlette`）。對外整合用 `httpx`（agent / Langfuse）
  與 `openai` SDK（OpenAI 相容端點）。
- **Frontend**：React 18 + Vite，**純手寫 CSS 設計系統**（無 UI 框架、**無 router 套件、無狀態管理庫**），
  含 light/dark 主題。導航狀態放在 **URL 的 hash** 裡，由一支約 50 行的 `useHashRoute.js` 解析
  （見 §10.1）——不是 router 套件，但也不再是 `App.jsx` 裡的 `useState`。
  字型（Inter / Space Grotesk / IBM Plex Mono）以 `@fontsource` **打包進 bundle**，
  不打 CDN，因為這東西是 docker-compose 部署、必須離線可用。
- **身分**：`keycloak-js`（前端，Authorization Code + PKCE）+ `PyJWT[crypto]`（後端驗簽章）。
  兩者都只在 `AUTH_MODE=keycloak` 時才動作（§11.2）。
- **DB**：PostgreSQL 16，schema 由 Alembic migration 建立。
- **部署形態**：`db` / `backend` / `frontend` **各自一個 container**，
  由 `docker-compose.yml` 加一份疊加檔編排（開發 / 部署兩套，見 §13.3）。
  host 端唯一需求是 docker（含 compose）——不需要 host 的 Python venv 或 node_modules。

### 3.2 七個 seam（最重要的一節）

每個外部依賴各藏在一個 **Python Protocol** 後面，**fake 與 real 兩套實作都已存在**，
由七個環境變數逐一切換（`*_IMPL=fake|real`）。**預設七個都是 `fake`**，所以不接任何外部服務
也能跑完整 demo；要接真的可以一個一個開，不必一次全換。

| Seam | 介面 | 假實作 | 真實實作 |
|---|---|---|---|
| `AgentClient` | `call(question, correlation_id, user_id, tags, workspace=None) -> AgentResponse` | 睡 1–3s，回假 response | `POST AGENT_CHAT_URL`，body 見 §3.3。有設 `AGENT_API_KEY`（或使用者自己的憑證）時多送一個 authorization header，沒設就一個都不送 |
| `JudgeClient` | `judge(question, response, ground_truth) -> Verdict` | 睡 0.5–1s，二元判定 | OpenAI 相容端點，LLM 同時吐 verdict + score + comment |
| `TraceClient` | `fetch_trace(correlation_id) -> Trace \| NotReady` | 前 2 次 poll 回 NotReady，之後給假 trace | Langfuse，**兩條讀取策略依序嘗試**（見 §3.5） |
| `DiagnosisClient` | `diagnose(trace, gt_reasoning, judge_verdict \| None) -> dict` | 睡 2–4s，回 §8.2 的 JSON | §8.2 四段式 prompt + 輸出驗證 + span_index 越界剔除 |
| `SynthesisClient` | `synthesize(trace, question, agent_response) -> str` | 依假 trace 生出編號步驟 | §8.3 的 prompt，**與 judge/diagnosis 共用同一個 LLM client** |
| `WorkspaceClient` | `get_workspace()`、`get_version()` | 罐頭的四個 skill 檔 | `GET AGENT_SKILLS_URL`（`get_version()` 走同一支，只取 `version`）。**沒設 URL 時 `build_seams` 回 `None`**，那是「只有 chat 端點」這個受支援的組態，不是錯誤。chat 端點的憑證**只在同源時**才會一併送到這裡（`integrations/real/agent_auth.py:same_origin`）|
| `OptimizerClient` | `chat(system, user, ...) -> (text, usage)` | 依 `failure_summary` 生出確定性的 patch，走得到 accept / reject / 多檔 diff 三條路徑 | OpenAI 相容端點；vendored 的 reflect / aggregate / clip 全部只透過這一支呼叫模型 |

> **為什麼是 `WorkspaceClient` 而不是 `SkillClient`**：skill 在 agent server 上**是一個目錄**
> （`SKILL.md` 加上它的 `references/`），而且決定 agent 行為的另一半住在旁邊的 `config.json`。
> 一個「名稱 → 一段文字」的介面表達不出 reference 檔，也表達不出「如果 model 換大一點呢」
> 這個同樣常見的假設。**一次讀完整份 workspace**，也順便讓快照天然一致——不會拿這一分鐘的
> config 配上一分鐘前的 skill 還宣稱那是同一份。

**兩個軸是分開的**：
- **`*_IMPL` 決定 fake / real**，是全域主開關。
- **端點決定於「哪一次執行」**：每個 run（或 playground attempt）帶自己的 base URL / 模型 / timeout，
  `build_seams(config, secrets)` 每次都建新的 client 實例。
  > 為什麼不是全域可變設定：`trigger_run` 開背景 task 時沒有鎖，若改動全域 settings，
  > **兩個併行的 run 會互相污染端點**。

**`WorkspaceClient` 是選擇性建構的**（`build_seams(..., include_workspace=True)`）：
> `WORKSPACE_IMPL=real` 但沒設 base URL 會 raise，而 run 路徑完全不讀 agent 的 config 或 skill 檔。
> 若無條件建構，一個設錯的 workspace seam 會讓**觸發 run 與看 trace 全部 500**。
> 只有 Playground 的 workspace 端點會要求它。
>
> `SynthesisClient` 則是無條件建構的：它跟 judge / diagnosis 共用同一個 LLM client，
> 沒有自己的端點可以設錯。

**假層的可控觸發（demo / 測試用）**：假層會辨識題目文字裡的標記——
`⟦timeout⟧` → 該題 agent「逾時」變 failed；`⟦wrong⟧` → judge 判 incorrect；
`⟦caveat⟧`（放 reasoning 內）→ 診斷帶 caveat。其餘題目以文字 hash 決定約 30% incorrect。
**真實實作不認得這些標記**（真 agent 沒有理由認得）。

### 3.3 correlation 機制（整個錯誤定位的前提）

**問題**：平台打完 agent 之後，怎麼知道該題對應 Langfuse 上哪一條 trace？

**解法（已定案並實作）**：**correlation id 注入**。平台為每題產生一個 `correlation_id`，
放進 request 的 `skill_studio.trace_data`，**agent server 用它當 Langfuse trace id**，事後平台用它反查 trace。

一次 agent 呼叫的完整 request body：

```json
{
  "model": "default",
  "messages": [{"role": "user", "content": "<題目文字>"}],
  "stream": false,
  "skill_studio": {
    "trace_data": {
      "trace_id":   "<correlation_id>",
      "session_id": "<correlation_id>",
      "user_id":    "<觸發者的 subject>",
      "tags":       ["eval_<eval set 名稱>"]
    },
    "timeout_s": 115,
    "skills": { "billing/SKILL.md": "…", "billing/references/refunds.md": "…" }
  }
}
```

`model` 是常數，agent 可以完全忽略——它只是因為 OpenAI 的 request schema 要求它，
少了它，擋在 agent 前面的 gateway 會用一個沒人選擇省略的欄位名把請求打回來。
平台專屬的東西全部收在 `skill_studio` 這一個 key 底下，而**不是** OpenAI 自己的
`metadata`（規範是最多 16 組 512 字元的字串對，裝不下一個 skill 檔）。

- `trace_id` 與 `session_id` **是同一個值**：每題都是自己的 correlation 單位，
  所以也是自己的 Langfuse session。
- `tags` 在 eval run 是 `["eval_<eval set 名稱>"]`，在 Playground 是 `["playground"]`。
- **`skill_studio.timeout_s` 是給 agent server 的執行預算**（每次呼叫都送，契約見 [`docs/agent-server-api.md`](./agent-server-api.md)）。
  它是這次呼叫的 `AGENT_TIMEOUT_S` **減掉一個固定的 5 秒 margin**（`SERVER_TIMEOUT_MARGIN_S`，
  寫在 `integrations/real/agent.py`——它是機制常數，不是逐環境的旋鈕），
  平台自己的等待上限（httpx timeout 與 §6.2 的 `wait_for`）仍然是完整的 `AGENT_TIMEOUT_S`。
  > 兩端都需要一個期限，而且**不能是同一個數字**。agent server 本來就有自己的上限；不告訴它
  > 平台的設定，它就只會用內建預設值——這正是「在 UI 把 timeout 調大卻沒有任何效果」的原因。
  > 但送**一樣**的值只是把「誰先放棄」變成 race，而兩種結果差很多：server 超時是一個
  > **帶原因的回應**，平台超時只是一條被切斷的連線。所以刻意讓 server 先到期。
- **`skill_studio.skills` 只在 Playground 改過 skill 檔、或 Optimize 在跑 rollout 時才出現**；
  eval run 的 request body 連這個 key 都不會多。
- **`skills` 是這次呼叫的完整檔案集**，整份取代 agent 的目錄，不是 patch
  （完整契約見 [`docs/agent-server-api.md`](./agent-server-api.md)）。
  只有取代表達得出「把某個 reference 檔刪掉試試看」——也因此 `{}`（這次不用任何 skill）
  與 key 不存在（用你自己的）是兩件不同的事。
- 回應：一般的 chat completion，答案讀 `choices[0].message.content`（字串或 content-parts
  陣列都收）。`finish_reason: "length"` **仍然算答案**，只是標記成被截斷；`usage` 有就記。
  空回答視為失敗（判一個空字串會產生毫無意義的 incorrect，反而蓋住真正的問題）。
  舊協議為了沒有標準可指而接受的兩種寬鬆形狀——裸 JSON 字串與 `text/plain`——**已經移除**：
  有了標準之後，每多收一種形狀就多一條讓 gateway 的雜訊被當成答案去評分的路。

> ⚠️ **這是 repo 外的相依**：agent server 必須讀 `skill_studio.trace_data.trace_id` 並用它當
> Langfuse trace id。**沒有這一步，平台無從找回自己剛觸發的 trace**，整個錯誤定位功能失效。

### 3.4 資料落點分工

**原則：Langfuse 是 trace/span 的真相來源，app DB 不複製 trace 內容。**
app DB 只存 Langfuse 沒有的概念 + 指回 Langfuse 的索引（`correlation_id`）。
span 的 input/output/token 全文於檢視時**即時**向 Langfuse 抓，不落 app DB。

| 概念 | 存哪 |
|---|---|
| eval set / 題目 / 期望答案 / 期望流程 / skill tag | **app DB**（`eval_sets` / `questions` / `question_skills`）|
| 每題對應哪條 trace | **app DB** 的 `question_results.correlation_id` |
| 題目 correct/incorrect 與 judge 分數 | **app DB** `question_results` |
| 診斷結果（含 caveat）| **app DB** `span_analyses`（機率與 caveat 都是本平台專屬概念）|
| trace / span 的 input / output / token | **Langfuse**（唯讀，檢視時即時抓）|
| skill / skill 版本 / SkillOpt run | 未建（Stage 3）|
| Playground attempt | **記憶體**，完全不落庫（見 §7）|

> **與原始設計的差異**：原設計打算把 eval set 寫進 Langfuse Dataset、把 verdict 寫回
> Langfuse Score。**兩者都尚未做**——目前 app DB 是唯一真相，Langfuse UI 上看不到本平台判的分數。
> 這是刻意的範圍取捨，不是遺漏（見 §15）。

### 3.5 Langfuse 讀取的兩條策略（真實環境踩過的坑）

實際接上自架 Langfuse 時遇到 `Unknown table expression 'events'` 錯誤。
**這是 Langfuse 部署端的問題，不是本平台的**：我們呼叫的是公開 REST 端點，
錯誤訊息裡的 SQL 是 Langfuse server 自己對它的 ClickHouse 產生的。自架版本約 3.152.0 起
會查一張屬於 v4 wide-observations schema 的 `events` / `events_core` 表，而該表的
production migration 尚未釋出（langfuse#11924、#12223、discussion#12777）。

> **官方 Python SDK 幫不上忙**：`langfuse.api.*` 是同一組 REST 端點的產生式 client，
> 會撞到完全相同的 server 端查詢。因此**不引入該依賴**，維持自寫的 httpx client。

**本平台的避險**：兩條讀取策略依序嘗試，先拿到 observation 的獲勝——
1. `GET /api/public/traces/{id}`（回 `TraceWithFullDetails`，其 `observations` 欄位與列表端點同型）
2. `GET /api/public/v2/observations?traceId=`（分頁式）

兩者由 Langfuse 內部**不同的查詢**服務，所以其中一條壞掉時另一條有機會可用。
- **每條策略各自的 NotReady 語意**：單一 trace 端點的 404 = 尚未 ingest（→ NotReady），不是失敗。
- **只有每一條都失敗才 raise**，而且**把每一條的原因都帶上**——fallback 絕不能把主要路徑的
  失敗原因藏起來。
- `LANGFUSE_TRACE_READ_STRATEGY`（`auto` / `trace_api` / `observations_api`）可在部署確認正常後
  釘死其中一條，省掉多餘的第一次請求。
- 前端認得這個 ClickHouse 簽章（以及 401、連不上），紅色 banner 顯示白話說明
  「這是 Langfuse 自架的已知問題，不是 eval 平台的錯」與該怎麼修，**原始 SQL 收進可展開區塊**。

> ⚠️ 誠實的但書：fallback 是**避險**。如果兩個端點都以同樣方式壞掉，只有修部署能解決——
> 但至少畫面會直說是這麼回事。

---

## 4. 關鍵設計決策

每個決策都附「**為什麼**」與「**不這樣做會怎樣**」。這一節是理解整份程式碼的鑰匙。

### 4.1 診斷是「線索」，不是「判決」

Stage 1 刻意**不給機率**，輸出用**不確定語氣**：
「最可疑的是 span 4，因為其 SQL 結果少了 X 欄位；但也可能上游 span 2 的檢索就漏了」。

- **`suspects` 是允許多個的陣列**：在資料結構層直接容納「不確定 / 多個可疑點」，
  不逼 LLM 選一個。第一名排最前，前端預設跳它。
- **`confidence` 用 high / medium / low 三檔文字，不用數字百分比**。
  > 完全不給強弱，前端只能平鋪、開發者不知先看哪個；三檔文字給了排序線索又不製造「73%」的假精確。
  > Stage 2 要做機率熱點時再升級為連續值。
- **語氣約束寫進 system prompt，是硬約束**。
  > 不寫進 prompt，模型多半會斬釘截鐵指單一 span——那正是最危險的失敗模式：
  > 開發者會信任一個假精確的答案。
- **`caveat` 是逃生口**：讓 LLM 有地方講「錯不在單一 span / 不在 skill 可控範圍」，
  而不是被 schema 逼著硬指一個 span。

### 4.2 caveat 是跨階段訊號，必須落庫

`caveat` 不只是 UI 標記：
- **Stage 1**：UI 顯眼標出（放在 `overall_diagnosis` 旁、trace 檢視頂部，不埋進單一 span 細節），
  等於直接告訴開發者「別浪費時間在這條 trace 上找 skill 的錯」。
- **Stage 3**：有 caveat 的題目**預設不自動納入** SkillOpt 樣本，標為「需人工確認」。
  > 理由：SkillOpt 假設「改 skill 能修」，而 caveat 恰恰在說「這題改 skill 也修不好」，
  > 硬納入只會污染優化樣本。
- **落庫要求**：caveat 在診斷生成當下就存進 app DB 的**獨立欄位**（不是埋在 JSONB 裡），
  否則 Stage 3 得重跑一次診斷才知道哪些題有 caveat。
  這是「Stage 1 少做功能，但資料結構別做窄」的具體落點。

### 4.3 粗粒度對齊（最低限度用法）

Stage 1 **不做** step 拆解軟對齊；直接把整段 `ground_truth_reasoning` 連同整條（截斷後）trace
丟給 LLM，問「相對於此期望流程，trace 哪裡偏離」。

> 粗但夠用，而且能在真實資料上**看清對齊到底多不準**——這個觀察是決定是否投入 Stage 2 的依據。
> 先做精細對齊等於在還不知道基線多差的情況下投入工程。

### 4.4 截斷：砍 body 不砍 span，而且只在診斷路徑

- **保留所有 span，絕不按 span 數截斷**。
  > 根因常在前面，但讓錯誤「現形」的症狀證據常在後面（例：span 2 檢索漏欄位，單看正常，
  > 直到 span 7 產生空結果才現形）。砍掉後段 span 會砍掉診斷證據。
- 只對**單一 span 內部超長的 input/output body** 截斷（保留頭尾、中間省略）。
- **只套用在「餵給診斷 LLM 之前」**（門檻 `SPAN_BODY_MAX_CHARS`，預設 800）。
  那裡是 context window 的硬限制。
- **檢視路徑（開發者點開 span 看）完全不截斷**。
  > 曾經套在檢視路徑上，結果砍掉的正是開發者點開 span 想看的證據，還讓 JSON 變成無法 parse 的碎片。
  > 長度改由 UI 用「**收合**」處理，不是用「切掉」。

### 4.5 診斷的生成時機與快取

流程是 **判分 → poll/backoff 等 trace ready → 生成診斷 → 存 DB**。

- **生成時機前移到 eval 當下**：開發者事後點開該題**直接讀 DB**，即時、不重跑 LLM（省時省錢）。
- **必須掛在 trace ready 之後**：Langfuse ingestion 是非同步的，agent 剛回應時 trace 可能還沒落地。
  > 「生成診斷」不可與判分同時發，否則會偶發性地存進空診斷。
- **Stage 1 的重算觸發只有一個**：開發者手動點「重新診斷」。
  > trace 是 immutable 的一次執行，自動重算幾乎不會發生。（人工重標 span 是 Stage 2。）
- **是否自動診斷，由每個 run 自己決定**（`runs.config.diagnosis_enabled`，預設值來自
  `DIAGNOSIS_ENABLED`）。答錯一題就是一次額外的 LLM 呼叫，只想看 pass rate 的 run
  沒有理由付這筆錢。
  > 三個細節是設計的重點：**`False` 是選擇而不是留白**——其他欄位用空字串表示「沒選」，
  > 布林值若沿用同一條規則，取消勾選會被環境變數的 `True` 蓋掉；**config 裡沒有這個 key 的
  > run 照樣診斷**，否則這個功能上線的當下，整個既有歷史會安靜地停止診斷，而畫面上看起來
  > 會像診斷模型壞了；**關掉不等於不能診斷**，單題的手動重新診斷不受影響，所以這個開關決定
  > 的是「不主動花這筆錢」，不是「永遠不能問」。
- 關掉診斷的 run，題目頁會明說「這個 run 關閉了 trace diagnosis」，與「診斷失敗」
  （`diagnosis_error`）是兩種不同的訊息——只有其中一種值得重試。

### 4.6 `question_id`：跨 run 對齊的地基

- 上傳時**選填**；未提供時由系統生成一個 **immutable id**（`q_` + 8 hex）。
- **不採 content hash**。
  > 改題目文字會使 hash 變動，而那會破壞跨 run 對齊——同一題在改文字前後會被當成兩題。
- **eval set 建立後不可新增或刪除題目**，題目內容只能透過系統介面修改（`question_id` 保持不變）。
  > 這保證同一個 set 下**所有 run 永遠基於同一份固定的 question_id 集合**，
  > cross-run 對齊不會有對不上的情況。要增刪題目請另建一個新的 eval set。
- **改題後舊 run = 歷史快照**：透過介面改題只影響**之後**的新 run；
  既有 run 的結果與診斷保持原樣（反映它執行當下的 ground truth 真相），不標 stale、不重算。
- 為什麼這是 Stage 1 就要定的地基：三種 incorrect mode 與卡片上的 regression 明細
  **全都依賴「同一題跨 run 可對齊」**。

### 4.7 逐 run 設定 + 金鑰只進不出

- 按「Run eval」會先開設定對話框：run 名稱 + agent / Langfuse / LLM 三區的端點、模型、timeout、
  concurrency。每個 run 把自己實際用的設定存進 `runs.config`（非機密）與 `runs.secrets`（金鑰）。
  > 因此兩個 run 可以打不同的 agent server 或用不同的 judge model，
  > 而且事後看 trace / re-diagnose 時**會沿用該 run 當初的端點**——trace 存在哪，
  > 不必然是環境變數今天指的地方。
- **留白欄位在觸發當下就寫死成環境變數的值**。
  > `runs.config` 存的是「有效值」而非「使用者改過的差異」。否則一個空欄位事後無從分辨是
  > 「當初用了 env 的值」還是「根本沒設」，而今天的 env 也無法作證當初的內容。
- **`config` 與 `secrets` 刻意分成兩個 DB 欄位**：沒有任何 response model 讀 `secrets`，
  所以「金鑰不外流」是**結構上的保證**，而不是靠人記得維護白名單。
- **沿用舊 run 的金鑰**：前端只送 `reuse_secrets_from_run_id`，由後端 server-side 複製；
  且**金鑰與其端點綁定**——若 `llm_base_url` / `langfuse_host` 被改掉，對應金鑰就不會被沿用，
  必須重新輸入。
- `*_IMPL=fake` 的區塊在對話框中**變灰並標示不會生效**。
  > 否則填了半天卻跑出假資料，是最容易踩的坑。
- **對話框在按下按鈕前就先探測 agent server**，而且**兩個端點的檢查機制刻意不對稱**——
  差別是成本。
  - **skills 端點**：一次讀取，免費，所以維持**自動探測**（`POST /agent/skills`，
    網址輸入 debounce，與分享對話框查員工目錄同一個 hook）。探測有自己的
    `AGENT_PROBE_TIMEOUT_S`（預設 5 秒）——借用 `AGENT_TIMEOUT_S`（120 秒，那是答一題的
    預算）會讓一台卡住的 agent 把對話框鎖住兩分鐘。
  - **chat 端點**：一次真實提問，要花一次 model 呼叫，所以**只能手動觸發**，
    外加「按下 Run eval 時順手測一次」——而且只在這組 URL 還沒被證明過時才測。
    每次都測會讓每一次開跑都多花一次呼叫與數十秒；完全不測，就是打錯字要花掉一筆
    run row、一整組 `question_results` 和每題一次 agent 呼叫才會被發現。
  > **成功時不打斷，直接開跑。** 一個為了報好消息而停下來的對話框，會讓這個檢查
  > 讀起來像障礙，下一個人就會去找關掉它的方法。失敗才展開連線設定——**不做自動捲動、
  > 不搶 focus**：探測是在對話框已經在畫面上之後才回來的，自動展開會讓表單在游標底下跳動，
  > 還會把剛剛才收起來的面板重新打開。
  > **skills 端點壞掉不擋 Start**：eval run 不送 override、不讀 trace，壞掉的 skills 端點
  > 只讓它少一個覆蓋率警告。只有 chat 端點確定失敗才擋。
- 探測成功時順手拿到 agent 上的 skill 清單，**與這個 eval set 的 skill tag 比對**
  （`GET /eval-sets/{id}/skills`）。決策 6 把 tag 與 agent 上的目錄名當成同一個名字，
  比對是精確比對；只差大小寫的情況會被單獨點名，因為那是唯一一種「看起來完全正確」的失誤。
  > 這只是**警告，不擋 Start**：tag 缺漏有合法情境（agent 用別的名字路由、set 只標了一半），
  > 而「連不上」是確定性的錯誤。沒有 tag 的題數另外報，否則一個只標了兩題的 set 會誠實地
  > 回報「全部齊備」。
  > `AGENT_IMPL` 或 `WORKSPACE_IMPL` 任一為 fake 時，讀到的 skill 是罐頭資料，此時整個探測
  > 標示為 simulated：不做覆蓋率警告，也絕不擋 Start。

### 4.7a 個人預設值（user global settings）

§4.7 講的是「一個 run 自己的設定」。這一節講它的**起點**是誰決定的。

在此之前，三個頁面的每一張表單都從 `.env` 開始。這對一個部署是對的，對一個人是錯的——
每個 run 都指向自己 agent server 的人，一天要重打同一個位址十幾次，而「Run eval」對話框
對昨天沒有記憶。右上角使用者選單裡的設定頁就是他把這件事說一次的地方。

**哪些 key 進得來：兩個條件同時成立。**
`config.py` 裡有對應的環境變數，**而且**前端目前已經有控制項可以為單一 run 覆寫它。
兩半都必要，而且互相推不出來——
`OPTIMIZER_SCHEDULER` 有環境變數但沒有任何畫面提供它（設了也看不到作用）；
wizard 的「Trajectory budget」有控制項但預設值是 `reflection.py` 的常數（等於從後門發明一個部署設定）。
目前 25 個：連線/模型 10 個、金鑰 2 個、Optimize 13 個。
清單與**每一個排除項的理由字串**都在 `app/settings_catalog.py`。

> 排除清單和進來的清單一樣重要。`SCRIPT_MAX_QUERIES` 那一組是**圍堵邊界而不是偏好**，
> 使用者能自己調高的沙箱限制就不是限制。半年後沒有理由字串就分不出「還沒做」和「不該做」。

**三層，而且疊合只發生在一個地方。**

```
系統值 (.env → Settings)  ⊕  使用者值 (user_settings)  →  /defaults 端點  →  表單帶入  →  明確送出
                                                          ↑ 只有這裡
```

`run_config.defaults()`、`hyperparams.algorithm_defaults()`、`StopPolicy.from_config()`
**三個函式絕對不可以知道呼叫者是誰**。它們看起來是疊合的自然位置——第一個就是對話框的預填來源——
但 `resolve()` 也呼叫它，而 `resolve()` 決定一個 run 實際跑什麼；`resolve_algorithm()` 呼叫第二個；
optimizer engine 每一步呼叫第三個。把 subject 塞進任何一個，同一個 POST 就會因為誰送的而產生不同的 run，
而且是在一個沒人會想到要打開的檔案裡決定的，現有測試還會全綠。
疊合因此住在 `services/user_settings.py`，只有兩支 `/defaults` 端點呼叫它。
`tests/test_user_settings_isolation.py` 從三個角度釘住這件事：函式簽名不得長出 subject、
那三個模組的原始碼不得出現 `user_settings`、空的 request 仍然解析成環境值。

**疊合看 key 在不在，不看真假值。**
`diagnosis_enabled=False`、`early_stop_patience=0`（「永不早停」）、
`early_stop_target_score=None`（「不設目標」）全都是使用者可以做的選擇。
`if value:` 會吃掉四種，`if value is not None:` 會吃掉三種——
`hyperparams.py` 和 `stopping.py` 各自為了同一個 bug 被改寫過一次。

> 最細的一個：`stopping._number` 把 `None` 讀成「未設定，用環境的值」。對一個 **run 的 config** 是對的，
> 對一個**使用者的預設**是錯的——部署瞄準 0.9 的話，使用者就永遠無法把自己的預設設成「不瞄準」。
> 所以環境先解析成一個普通 dict，使用者的值再按 key 存在疊上去，永遠不經過 `StopPolicy.from_config`。

**讀寬鬆，寫嚴格。** 存進去時合法的值，可能因為之後改版而變不合法。
存檔用 400 拒絕；讀取則丟掉那個 key、回報在 `invalid` 裡、繼續——
因為讀的這支端點是**每一個頁面都會載入的**，一個過期的偏好該讓使用者少一個欄位，而不是少一個畫面。

**金鑰走另一條路，而且沒有選擇。**
金鑰永不回傳給瀏覽器，所以表單一定送空值，一定由後端注入。這條線就是 `runs.config` / `runs.secrets`
既有的分欄線。四條規則（`services/user_secrets.py`）：

- **加密儲存，fail closed。** `SETTINGS_SECRET_KEY` 沒設就整個關閉，絕不退化成明文。
  （`runs.secrets` 是明文，那是可以接受的：一個下午的 key。一個**沒有到期日**的預設是另一種爆炸半徑。）
- **端點綁定。** 存的時候連同當下的 `llm_base_url` / `langfuse_host` 一起存；注入時比對這次 run 的端點，
  不同就不注入。沒有這條，在對話框裡把 base URL 改成別的位址，後端就會把使用者的 key 送過去。
  §4.7 的「沿用舊 run 的金鑰」早就有這條規則，這裡是同一條規則套用到一個活得更久的儲存。
  **代價是刻意的摩擦**：改了端點就要重打，這是功能不是缺陷。
- **`AUTH_MODE=fake` 硬性停用。** 那個模式的身分是呼叫者自己設的 header。存與注入兩端都擋，
  不是只在 UI 隱藏——從 keycloak 切回 fake 的部署，資料列還在，必須停止使用。
- **解密失敗不是故障。** 輪替或遺失金鑰降級成「沒有這把金鑰」並在設定頁說明；
  丟例外會讓每個頁面都載入的那支端點掛掉。

三個建立路徑（`runs.py`、`playground.py`、`optimization.py`）呼叫**同一個** `user_secrets.inject`，
所以端點綁定不會在兩個畫面成立、第三個不成立。

**介面：空白就是「沒有意見」。**
設定頁的欄位預設是空的，灰色 placeholder 是這個部署的值。打字＝覆寫，清空＝還原。
不需要三態標記，也不需要 reset 按鈕——「有沒有字」就是狀態。
兩種欄位做不到這件事，所以改用三段式控制：勾選框沒有空狀態（`diagnosis_enabled` 等三個），
而 `early_stop_target_score` 的空白已經是「不瞄準」。
三個功能頁**沒有**任何逐欄標記，只有一行「已帶入你的預設值 · 編輯」，而且只在真的有覆寫時出現。

**兩種提示，都不是錯誤。**
`seen_keys` 記錄這個使用者看過哪些 key；「新的」必須是「你沒看過」而不是「你沒設過」，
否則第一次進來就是 25 個徽章，等於沒有提示。資料列**在第一次打開設定頁時建立**，
當下所有 key 一次寫進 `seen_keys` 當基準線——所以只有之後新增的才會是新的，
而從沒打開過設定頁的人沒有資料列，也沒有小紅點。
`system_at_set` 記錄每個覆寫在**當時**的系統值；管理員換掉 `LLM_BASE_URL` 之後，
覆寫過它的人會安靜地繼續打向那台已經消失的機器，而畫面上沒有任何東西解釋為什麼只有他壞掉。
這是兩種提示裡真正會咬人的那一種。

**未來新增 key 不會被忘記，由三道測試保證。**

| 方向 | 位置 | 抓什麼 |
|---|---|---|
| A | `backend/tests/test_settings_catalog.py` | `Settings` 上每個欄位、三個 defaults dict 的每個 key，都必須在 `CATALOG` 或帶理由的排除表裡 |
| B | `frontend/src/settings_catalog.test.js` | 表單上每個欄位名都必須在 catalogue 或帶理由的 `NOT_A_SETTING` 裡；**反向**也查：catalogue 提供的每個 key 都必須真的有控制項 |
| C | 同 A 檔 | catalogue 的每個 key 都要在 `.env.example` 裡有變數（抓「加了欄位卻沒有環境變數」） |

catalogue 以產生的 JSON（`frontend/src/settings_catalog.json`）送到瀏覽器，
另有一個後端測試斷言該 JSON 與 `CATALOG` 同步——兩邊任一改了另一邊會紅。

**完全向後相容。** `user_settings` 是空的時候，每一張表單開出來的值與這個功能存在之前逐欄相同。

### 4.8 Playground 完全不落庫

**attempt 是一次拋棄式實驗；run 是一筆歷史紀錄。** 不落庫換到三件事——
不用 migration、不用權限列、eval 歷史裡不會混進「這個 run 是真的嗎」的模稜兩可。
代價只有一個，而 UI 直說了那一個：**backend 重啟就沒了**。詳見 §7。

### 4.9 判分與診斷在 Playground 是選填的，而「選填」意思是那個呼叫不會發生

- 給了**期望答案** → 才跑 judge。給了**期望流程** → 才跑 diagnosis。
  > 一個試打題目的開發者常常兩者都沒有，硬要求會讓這條「便宜的路」重新變貴。
- **測試斷言的是「呼叫次數為 0」，不是「verdict 是 None」**。
  > 後者在「呼叫了但把結果丟掉」的情況下也會通過，而那是一筆真實的 LLM 帳單。

### 4.10 workspace override 是 per-request，而且平台無法保證它生效

- 改過的 config 與 skill 檔只影響**這一次呼叫**，不寫回 agent server。
  > 寫回需要版本控制與 rollback（Stage 3 的範圍）。
- ⚠️ **平台無法自動驗證 agent 真的採用了 override**。
  唯一的證據是：注入的文字會出現在該次 trace **第一個 span 的 system message** 裡，
  而 span 檢視就是照 chat-completions 形狀渲染的，所以**看得到**。
  這句話寫在 UI 的說明文字裡，**不假裝有自動驗證**。
  > 假層走同一條路徑（把 override 接在假 trace 的 system prompt 後面），所以純 Docker 的 demo
  > 也看得到「override 有沒有送到」長什麼樣子。

### 4.10a 編輯的基準：快照 + 版本字串

Playground 的編輯器同時握著**兩份東西**：agent server 給的快照，與開發者改出來的工作副本。
兩份都留才回答得了「這個欄位還原成什麼」與「我到底改了什麼」。

**送出前會先問一次版本**（同一支 skills 端點，只取 `version`）。與快照不同時跳對話框，
讓開發者選「重新讀取（丟掉編輯）」或「照樣送出」。
> 拿一份中途被別人改掉的 skill 去問問題，得到的結論不能信——**而且事後看不出來**。
> 版本檢查失敗（agent server 沒回應）不擋送出：那只損失檢查，不該損失實驗。

### 4.11 錯誤必須看得見，而且要能分辨種類

這是接真實服務後最重要的一類設計：

| 兩種情況 | 若不分辨會怎樣 |
|---|---|
| 「Langfuse 還在 ingest」 vs 「Langfuse host 打錯 / 401」 | 兩者都是「沒有 span」。永遠顯示「生成中」會讓一個設定錯誤看起來像 trace 永遠差幾秒就到 |
| 「這題還沒送給 agent」 vs 「trace 抓不到」 | 對一個不可能存在 trace 的 correlation_id 發請求，壞掉的 Langfuse 會吐出一個**跟上次一模一樣的新錯誤**，看起來像舊錯誤被重播 |
| 「診斷模型掛了」 vs 「根本沒送去診斷」 | UI 上完全看不出差別，開發者會以為系統沒在做事 |
| 「這題 failed」 vs 「這題 failed，原因是 X」 | 一個光禿禿的 `failed` 標籤在接真 agent 後等於沒有資訊 |

因此：`NotReady` 與 `TraceFetchError` 是**兩個不同的型別**；
`trace_error` / `diagnosis_error` / `error_message` 三個原因欄位都**落庫**；
trace 檢視有**五種狀態**（見 §6.4）；錯誤訊息帶 host + HTTP 狀態碼 + response body 前 200 字。

### 4.12 樂觀鎖，不做即時協作編輯

- 每個可獨立編輯的實體帶 `version`（`questions`、`eval_sets`）。
  前端開編輯時記住當下 version，提交時後端 `UPDATE … WHERE id=? AND version=?`；
  未命中（他人已改過）→ 回 **409**，前端提示「已被他人修改，請重新載入」。
- **衝突粒度 = 單列**：A 改第 3 題、B 改第 5 題互不衝突。
- 不做 OT/CRDT 即時協作——與這個系統的規模不成比例。
  讀同步靠「下次進入該 set / 重新載入」自然拿到最新。

---

## 5. 資料模型

### 5.1 App DB：七張表

所有主鍵都是 `uuid`，`server_default gen_random_uuid()`（migration 先 `CREATE EXTENSION pgcrypto`）。
所有 status / verdict / role 欄位都是**純 `Text`，不用 PG enum**——新增一個狀態值不需要 migration。

**1. `eval_sets`**

| 欄 | 型別 | 說明 |
|---|---|---|
| `id` | uuid pk | |
| `name` | text | |
| `description` | text null | |
| `source_format` | text | `'csv' \| 'jsonl' \| 'python'`——**開發者實際上傳的內容形式**。CSV 在前端就被轉成 JSONL，Python script 的輸出在後端被轉成同一組 row，後端 payload 恆為 JSONL，此欄是唯一保留原始形式的地方 |
| `metadata` | jsonb | 開發者自訂的 metadata key-value。**單一 JSONB 欄位**，未建 keys 表——key 量不大，「既有 key 自動帶出」以掃描 JSONB 支援。ORM 屬性叫 `meta`（`metadata` 在 declarative Base 上是保留字）|
| `version` | int | 樂觀鎖 |
| `judge_system_prompt` | text null | 本 set 的 judge system prompt。**NULL = 用程式碼的預設**（§8.1a），不是預設值的副本 |
| `judge_user_prompt` | text null | 同上，user 那半（template，含三個佔位符）|
| `judge_prompt_verified_at` / `_verified_model` | timestamptz null / text null | Verify 通過的時間與所用模型。**任一半 prompt 一改就清空** |
| `judge_prompt_reviewed_at` | timestamptz null | owner 最後一次看過判準的時間。NULL 時卡片與第二層的齒輪上亮提示點。刻意**不是**「跟預設不同才亮」——幾乎每個 set 都用預設，那樣的提示一週內就會被當成裝飾 |
| `created_at` / `updated_at` | timestamptz | |

**2. `questions`**（stable question_id 的家）

| 欄 | 型別 | 說明 |
|---|---|---|
| `id` | uuid pk | 內部 pk（改文字不變，內部關聯不斷）|
| `eval_set_id` | uuid fk → eval_sets (CASCADE) | |
| `question_id` | text | §4.6 上傳時生成、immutable、使用者可見/可下載 |
| `question` / `ground_truth_response` / `ground_truth_reasoning` | text NOT NULL | 三者皆必填 |
| `version` | int | 樂觀鎖（衝突粒度 = 單題）|
| `created_at` | timestamptz | |
| | | `unique (eval_set_id, question_id)` |

**3. `question_skills`**（skill 是 list of str）

| 欄 | 說明 |
|---|---|
| `question_pk` | uuid fk → questions.id (CASCADE) |
| `skill_name` | text |
| `ordinal` | int——保留順序；**目前只用 ordinal=0**，但型別先設為陣列，未來支援一題多 skill 不必改 schema |
| | pk `(question_pk, ordinal)` |

**4. `runs`**（一次 eval 執行）

| 欄 | 型別 | 說明 |
|---|---|---|
| `id` | uuid pk | |
| `eval_set_id` | uuid fk (CASCADE) | |
| `triggered_by` | text | 誰觸發（owner 或 viewer 皆可）|
| `name` | text null | 開發者給的標籤；未設時 UI 退回 `started_at` |
| `config` | jsonb NOT NULL default `{}` | 該 run 的**非機密**有效設定（§4.7）|
| `secrets` | jsonb NOT NULL default `{}` | 該 run 的金鑰。**沒有任何 response model 讀它** |
| `status` | text | `running \| completed \| failed \| cancelled` |
| `cancel_requested` | bool | 按下停止的**耐久**旗標，撐得過重啟 |
| `started_at` / `completed_at` | timestamptz | |
| `pass_rate` / `total_count` / `correct_count` | numeric / int | 完成時算好存著，首頁卡片趨勢直接讀，不每次聚合。**cancelled / failed 的 run 留 `pass_rate=NULL`**——半個 run 的通過率會拖低趨勢線，而原因與 agent 無關 |
| `error_message` | text null | 整個 run 以 failed 收場的原因 |

**5. `question_results`**（regression 對齊核心）

| 欄 | 型別 | 說明 |
|---|---|---|
| `id` | uuid pk | |
| `run_id` | uuid fk → runs (CASCADE) | |
| `question_pk` | uuid fk → questions.id | **刻意沒有 ON DELETE CASCADE**（鎖定的 set 本就不刪題）|
| `correlation_id` | text | 指回 Langfuse（不存 trace 本身）|
| `agent_response` | text null | **agent 實際回答的內容**。只存 verdict 的話，接真 agent 後等於看不到「被評的東西是什麼」|
| `verdict` | text null | `correct \| incorrect`（未判分前 null）|
| `judge_score` / `judge_comment` | numeric / text null | |
| `status` | text | `pending \| done \| failed \| cancelled`（容許 run 部分完成）|
| `error_message` | text null | 該題失敗/中止的**原因** |
| `failure_kind` | text null | 哪一步失敗：`agent \| judge \| judge_invalid`。分出 `judge_invalid`（judge 回覆 parse 不出來）是因為它指的地方不一樣——其他失敗是 agent 或網路，這一個幾乎都是本 set 的 judge prompt（§8.1a）。舊資料為 NULL，照樣畫成 `failed`：那些 run 是真的不知道，硬猜會讓新的「未判分」統計對歷史說謊 |
| `agent_latency_ms` | int null | agent round-trip 實測耗時 |
| `trace_ready` | bool | trace 是否已確認可查 |
| `trace_error` | text null | trace 抓不到的原因（§4.11）|
| `diagnosis_error` | text null | 診斷失敗的原因（§4.11）|
| `created_at` | timestamptz | |
| | | `unique (run_id, question_pk)` |

> `(question_pk, verdict)` 跨多個 run join → 算出 regression（哪題從 correct 變 incorrect）。
> `status` 讓某題 agent timeout / judge 失敗 / trace 一直 not ready 時，run 不整個卡死。

**6. `span_analyses`**（診斷結果）

| 欄 | 型別 | 說明 |
|---|---|---|
| `id` | uuid pk | |
| `question_result_id` | uuid fk (CASCADE) | `unique`（1:1）|
| `overall_diagnosis` | text | |
| `caveat` | text null | §4.2 跨階段訊號；**獨立成欄**（非埋 JSONB）便於 Stage 3 篩選 |
| `raw_llm_output` | jsonb | 完整診斷 JSON，含 `suspects[]`。**整包存、不拆子表**——UI 讀整包 render，無跨題查 span 需求；Stage 2 要做機率熱點 / 跨題聚合可疑 span 時再拆 |
| `generated_at` | timestamptz | |
| `model_used` | text | 記生成用的 model，日後微調精確度要用 |

**7. `eval_set_roles`**（權限）

| 欄 | 說明 |
|---|---|
| `eval_set_id` | uuid fk (CASCADE) |
| `user_subject` | text——來自登入身分 |
| `role` | text——`owner \| viewer` |
| | pk `(eval_set_id, user_subject)` |

**8. `eval_set_scripts`**（由 Python script 產生的 set 的 provenance，§10.3）

| 欄 | 說明 |
|---|---|
| `id` | uuid pk |
| `eval_set_id` | uuid fk (CASCADE)，**unique**——set 建立後即鎖定，因此每個 set 至多一次執行 |
| `source` | text——script 全文。不設長度上限：在「已經跑成功之後」才因長度被拒是最糟的時機 |
| `source_sha256` | text——兩個 set 是否出自同一份 script，不必 diff 全文 |
| `db_host` / `db_port` / `db_name` / `db_user` | 讀了哪個資料庫、以誰的身分 |
| `row_count` / `executed_by` / `executed_at` | |

> **沒有 password 欄位，也不會有。** 憑證只存在於那一個 request 之中：用完即忘，
> 不入庫、不入 log、不回傳。獨立成表而非掛在 `eval_sets` 上，是因為
> `_build_cards`（首頁）刻意被改寫成只讀有限列數，把一整份 script 掛上去會抵銷它，
> 而卡片根本不顯示這欄。

**未建的表**：`skills`、`skill_versions`、`skillopt_runs`（Stage 3）；
`eval_set_metadata_keys`（改用單一 JSONB）；`playground_*`（刻意不落庫，§7）。

### 5.2 八個 Alembic migration

| Revision | 內容 |
|---|---|
| `0001_stage1_schema` | 上述前 7 張表 |
| `0002_real_integration` | `question_results.agent_response` / `error_message` / `agent_latency_ms`、`runs.error_message`。假資料時代不需要，接真實服務後「看得到 eval 結果」少不了它們 |
| `0003_run_config` | `runs.name` / `runs.config` / `runs.secrets`——逐 run 設定（§4.7）|
| `0004_run_lifecycle` | `runs.cancel_requested`、`question_results.trace_error` / `diagnosis_error` |
| `0005_list_indexes` | 三個索引（見下）|
| `0006_judge_prompt` | `eval_sets` 的五個 judge-prompt 欄位、`question_results.failure_kind`（§8.1a）。兩者都**可為 NULL 且不回填**：prompt 回填等於凍結今天的措辭，`failure_kind` 回填等於替不知道的歷史編一個答案 |
| `0007_question_started_at` | `question_results.started_at`——左欄計時器要有一個「從何時算起」。同樣可為 NULL 且不回填：那些列真的不知道自己何時開始 |
| `0008_eval_set_scripts` | `eval_set_scripts` 表（§5.1 第 8 張）——由 Python script 產生的 set 的 provenance。**head 是這一個** |

**慣例**：檔名 `NNNN_snake_name.py`，`revision` 字串**等於檔名 stem**（不是 hash）；
`down_revision` 指向前一個 stem；**autogenerate 不使用**（`target_metadata = None`），
migration 全部手寫；每個檔案有長篇 docstring 解釋**每個欄位為什麼存在**；
`upgrade()` 與 `downgrade()` 都寫。

**`0005_list_indexes` 的三個索引**——在此之前 schema **除了主鍵與 unique 之外一個索引都沒有**：

| 索引 | 為什麼需要 |
|---|---|
| `eval_set_roles(user_subject)` | 首頁第一個查詢是「這個人看得到哪些 set」，但該表主鍵是 `(eval_set_id, user_subject)`，用 subject 單獨查用不到主鍵索引 |
| `runs(eval_set_id, started_at DESC)` | run 列表與每張卡的聚合（run 數、趨勢、最新兩個 run）都靠它 |
| `question_results(run_id, verdict)` | 算 incorrect 數的聚合 |

> 不用 `CONCURRENTLY`：資料量還小，而且它無法在 Alembic 的交易內執行。

### 5.3 Playground 的記憶體 store（沒有表）

一個 module-level `OrderedDict`，key 是 attempt id，value 是一個 dataclass，
含 question / 兩個選填 ground truth / workspace override / **override 當下的 skill 檔快照** /
有效 config / secrets / correlation_id / status / phase / agent 回答與延遲 / verdict /
trace 物件 / 診斷 / 三個錯誤欄位。

> **為什麼要存快照**：`skills` 是整份取代（§3.3），所以少了基準就無從分辨
> 「哪幾個檔案真的被改過」——每個檔案看起來都像改過。這份基準是後端自己跟 agent server 要的，
> 不信瀏覽器送來的值，而且有獨立的 5 秒 timeout：它只是一行摘要，不該讓人等兩分鐘才知道
> 問題送出去了。

- **per-subject 上限 `PLAYGROUND_MAX_ATTEMPTS_PER_USER`（預設 20），超過淘汰最舊**。
  > 上限不是裝飾：一個 attempt 握著一整條 trace，真實 agent 是**數百 KB** 的 span body。
  > 無上限的記憶體 store 會一次一個 attempt 地吃掉 process 的記憶體。
- **絕不淘汰還在跑的 attempt**——那會讓它的背景 task 變成孤兒。
- **單 process 假設**：與 SSE hub 相同的限制。多 worker 部署要先有共享 bus。

---

## 6. 一次 eval run 的生命週期

### 6.1 流程

`POST /eval-sets/{id}/runs` 建立 `runs`(status=running，存下該次的 name / config / secrets) 後，
**立刻回 201**，並開一個背景 asyncio task：

```
用 build_seams(run.config, run.secrets) 建出這個 run 專屬的 client
  → 讀定 question 快照（之後改題不影響這次 run）
  → 一次把整份快照的 question_results 全部建好（status='pending'）
  → 送出 SSE run_started（帶 total）
  → 每題（併發上限 = config.concurrency，預設 1 嚴格序列）：
        publish question_started
        ① agent（correlation_id 進 metadata）→ publish question_answered
        ② judge → 寫 verdict/score/comment → publish question_judged
        ③ poll trace（指數退避）→ 標 trace_ready / trace_error → publish question_traced
        ④ 若 incorrect：抓 trace → 截斷 → 診斷 → 寫 span_analyses（含 caveat）
        publish question_done（帶 has_analysis）
  → 算好 pass_rate / total_count / correct_count 存回 runs
  → 送出 SSE run_completed（帶 status）
```

**兩個容易被忽略但重要的細節**：
- **所有 result 列在第一次呼叫 agent 之前就建好**。
  > 原本是逐題建立，於是慢 agent 執行時左欄會「一題一題冒出來」，看起來像這個 eval set 只有一題。
  > 附帶好處：SSE snapshot 的 `total` 從第一秒起就是對的。
- **`question_judged` 不能等到最後才送**。
  > 判分完到 `question_done` 之間隔著 trace poll 與診斷，接真實服務時那是**數十秒**——
  > 那段時間題目會一直停在「judging…」。

### 6.1a 「抓到了」不等於「抓完了」（程式碼註解稱為 §6.12a）

上面第 ③ 步的 poll 只回答得了「這條 trace 存在嗎」。**「這條 trace 完整嗎」是另一個問題，
而 Langfuse 沒有那支端點**——ingestion 不只是慢，它是**逐筆進來的**，
所以第一次讀到「有 observation 了」的那一刻，trace 可能還在長。

而輸掉這場競速的，**系統性地就是最後一個 span**：agent 多次 tool calling 之後的最終回答生成，
它結束的瞬間就是 HTTP response 回來、平台開始去找 trace 的瞬間。

**因此第一筆非空的讀取不被直接採信，而是重讀到 span 數不再增加為止。**

- **成長是唯一繼續讀的理由**：一次沒有新增 span 的讀取就結束等待，所以沒有東西在路上時的
  代價固定是**一次額外請求**。
- **settle 只能加、不能減**：重讀比較短、回 NotReady、或整個失敗，一律沿用手上那份。
  一個在確認讀取上壞掉的 trace store 不該讓人賠掉已經讀成功的 spans。
- **長度相同的重讀仍然採用後者**：Langfuse 是先建 observation 再補 output 的，
  所以同樣的 span 在較晚的讀取裡 body 比較完整。
- `TRACE_SETTLE_DELAY_S` / `TRACE_SETTLE_MAX_READS` 調整窗口；設 `0` 次即回到舊行為。

> **為什麼這件事比「少看到一個 span」嚴重**：診斷（§8.2）就是拿 trace 對照期望流程找分歧。
> 一條缺了最後一步的 trace，會讓模型**自信地診斷一個從未發生的失敗**。
> 而 eval run 的 trace **檢視**路徑每次都重讀 Langfuse（§3.4），所以畫面上的缺漏會自己補上、
> 很難被發現——**落庫的那份診斷卻沒有第二次機會**。
> Playground 更嚴重：attempt 握著的是當初那一份，沒有任何東西會再讀一次（§7.3）。

### 6.2 失敗策略

> 假層永遠不會 raise，所以這整段在假資料時代是無效程式碼。接真實服務後它是最重要的一段。

| 情境 | 行為 |
|---|---|
| 單題失敗（agent 不通 / judge 回不了合法 JSON / timeout）| 該題 `status='failed'` 並**寫下 `error_message`**，run 繼續跑其餘題目（partial completion）|
| judge 呼叫失敗 | **絕不預設為 correct**——那會灌水通過率。該題記為 failed、`verdict` 留 null |
| 診斷失敗 | **不影響該題判定**。verdict 才是結果，診斷是加值；原因寫進 `diagnosis_error`，owner 可事後手動 re-diagnose |
| trace store 暫時抓不到 | 不算該題失敗，只是 `trace_ready=false`，並把原因寫進 `trace_error` |
| 任何非預期例外 | 把 run 收成 `status='failed'`、寫 `runs.error_message`、**並送出 SSE 終止事件**。run 不會卡在 `running` 讓前端無限等待 |
| **backend 整個重啟**（部署 / crash / OOM）| run 是 in-process 的背景 task，重啟後 `status='running'` **再也沒有東西會改它**——UI 一直轉圈，按中止又因為「已終結」被拒。因此 **backend 啟動時會把所有 `running` 的 run 收成 `failed`** 並寫明原因（見 §13.3）|
| 使用者按下中止 | 見 §6.3 |

**其他執行控制**
- **timeout**：agent 呼叫包 `asyncio.wait_for`（`AGENT_TIMEOUT_S`），client 自身另有 httpx timeout。
  **同一個預算也會隨 request 送給 agent server**（`skill_studio.timeout_s`，比平台自己少
  固定 5 秒，見 §3.3）——否則 agent server 只會用它內建的
  上限，平台這邊把 timeout 調大就毫無作用。**server 超時應回 5xx**，而 5xx 走的是
  `AgentHttpError`、**不在重試名單裡**，所以該題直接判 failed，不會再燒兩次同樣的時間。
- **重試**：對暫時性錯誤（timeout / 連線錯誤 / OSError）做**有上限的指數退避**重試
  （`AGENT_MAX_RETRIES` / `LLM_MAX_RETRIES`，預設各 2 次）。**4xx 這類必然重現的錯誤不重試**。
- **併發**：`RUN_CONCURRENCY`（預設 1 = 嚴格序列）以 `asyncio.Semaphore` 控制同時打 agent 的題數。
  >1 時 `question_done` 事件順序不再固定，但前端以 `question_pk` 索引，不受影響。
  DB 寫入以一個 `asyncio.Lock` 序列化——**一個 AsyncSession 不是併發安全的**。

### 6.3 中止一個 run

- **持久旗標 + in-process 事件**：`runs.cancel_requested` 是耐久的真相（給 UI 讀、撐得過重啟）；
  一個 `asyncio.Event` 是「立刻」的機制。
  > 只查 DB 旗標的話，正在 `await` 真 agent 的那一題最久要等 `AGENT_TIMEOUT_S`（預設 120s）才會停，
  > **而停止鈕存在的理由正是那種時候**。
- agent 與 judge 呼叫都與 cancel event 賽跑（`FIRST_COMPLETED`），event 先到就 `task.cancel()`。
  實測按下中止到 run 變 `cancelled` 約 **44ms**。
- **狀態語意**：進行中的那題 → `cancelled` + 原因；尚未開始的題目 → 留 `pending`
  （run 因此誠實地讀作「停在 N/M」）；**已判分的題目結果一律保留**（成本已經付出去了），
  只跳過 trace poll 與診斷。
- **權限**：owner 可中止任何 run；**viewer 可中止自己觸發的 run**——
  既然允許 viewer 觸發 run，能開就必須能關。

### 6.4 trace 檢視的五種狀態

`GET .../results/{rid}/trace` 回傳的 `trace_state`。**分清楚它們是這支端點的主要價值**：

| `trace_state` | 意思 | UI | 會呼叫 trace store 嗎 |
|---|---|---|---|
| `ready` | 抓到了 | 顯示 span 列表 | 會 |
| `generating` | agent 已回答，但 Langfuse ingestion 還沒落地 | 「產生中，重試中」+ Retry。**若 run 當下有失敗原因也一併附註** | 會 |
| `not_started` | **agent 還沒被問到這題** | 「等待 agent」 | **完全不呼叫**（§4.11 的第二列）|
| `no_trace` | 該題 failed / cancelled，沒有 trace 可抓 | 說明沒有 trace | 不會 |
| `error` | trace store 讀不到（host 錯、401、逾時、server 端 SQL 錯誤）| 紅色 banner + 白話說明 + 原始錯誤收在可展開區塊 | 會 |

**`trace_ready=false` 時照樣嘗試抓一次**：
> 那個旗標只記錄 run 當下的結果。不重試等於讓一個設定錯誤永遠顯示「generating」。

### 6.5 SSE 事件表

| 事件 | 何時送出 | 用途 |
|---|---|---|
| `snapshot` | 訂閱當下 | 晚加入的訂閱者補當前狀態（total / done / correct / status）|
| `run_started` | 所有 result 列建好後 | 帶 `total` |
| `question_started` | 開始打 agent 前 | 左欄轉灰（`pending`）|
| `question_answered` | agent 回答後 | 左欄轉白（`answered`，「judging…」）|
| `question_judged` | 判分寫入後 | 左欄轉綠/紅 |
| `question_traced` | trace poll 結束後 | `trace_ready` / `trace_error` 定案 |
| `question_done` | 該題全部完成 | 帶 `has_analysis`（診斷此時才寫完）|
| `run_completed` | run 結束 | 含 `status`，可能是 `cancelled` / `failed` |
| `ping` | 15 秒無事件 | 保持連線 |

五個 `question_*` 事件的 payload 相同：`question_pk / phase / verdict / status / error_message / failure_kind /
trace_ready / has_analysis / trace_error / diagnosis_error / done / total / correct`。

- 前三個欄位是左欄「灰 → 白 → 綠/紅」的來源。`phase` 由後端**同一個函式**推導
  （不落庫），REST 與 SSE 共用，所以兩邊的顏色不可能對不上。
- **`phase` / `verdict` / `trace_ready` / `has_analysis` 四個合起來是中欄重抓 trace 的「指紋」**
  ——這是三欄詳情能即時更新的機制（見 §10.2）。

**SSE 實作**：一個 in-memory pub/sub hub，key 是不透明 UUID（run id 或 playground attempt id）。
端點模式：**先 subscribe 再開 generator**（否則授權到第一次 yield 之間的事件會遺失）→
先送 DB 推導的 snapshot → 15 秒 keepalive → 收到終止事件就 break → `finally` unsubscribe。
`EventSource` 不能帶 header，所以身分走 `?subject=`。

---

## 7. Stage 4：Playground

### 7.1 它解決什麼

Stage 1 的診斷告訴你 trace 在哪裡出錯。下一個念頭通常是
「**如果 skill 說 X 而不是 Y，這題就會對**」，或「**如果 model 換大一點呢**」——而在 Stage 4
之前，驗證這個假設的唯一方式是改一個 eval set、跑一整個 run。Playground 是那條便宜的路：
**一題、一份可改的 agent config 與 skill 檔，按一次就跑**。

而在 playground 問的題目，通常是**現有 eval set 沒有的題目**——那正是它被拿到這裡問的原因。
所以還有第二件事：把值得留下的那幾題**收進 shortlist，再變成一個新的 eval set**（§7.6）。

### 7.2 範圍

| 做了 | 沒做（刻意）|
|---|---|
| 單題即時試打：問題 → agent → trace → span 檢視 | attempt **不落庫**，沒有 migration |
| per-request **workspace override**：改 agent 的 config 值、改／增／刪 skill 檔 | **不寫回 agent server**（Stage 3）|
| 選填的 judge（期望答案）與 diagnosis（期望流程）| **不做「一按跑 N 次取多數」**——一次一次手動跑 |
| 本 session 的 attempt 清單 + 切換 + clone 回編輯區 | **不做並排 diff / skill diff** |
| **shortlist → 建立新 eval set**（§7.6）| **正式 eval run 不支援 workspace override**（只有 playground 有）|
| 從三欄詳情把題目帶進 playground | **不做多輪對話**（agent 是 stateless，chat 端點是單次呼叫）|
| 中止進行中的 attempt | **不改既有 eval set**——它建立後就鎖定（§4.6）|

### 7.3 一次 attempt 的流程

```
建立 attempt（config 在此刻寫死成有效值）→ 存進記憶體 store → 開背景 task → 201 立刻回

① agent（tags=["playground"]，有 workspace override 就帶上）→ phase=answered
② 有期望答案才 judge                              → phase=judged
③ poll trace，並 settle 到 span 數不再增加（§6.1a）  → phase=traced
④ 有期望流程且 trace 有到才 diagnose               → phase=diagnosed
```

> **attempt 握著的就是那一份 trace**：attempt detail 直接回傳它，不像 eval run 的 trace 檢視
> 每次重讀 Langfuse（§3.4）。所以第 ③ 步的 settle 在這裡格外重要——
> 這裡讀短了，就是**這個 attempt 一輩子都短**（`Draft from trace` 與重新診斷用的也是同一份）。
> 沒填期望答案時第 ② 步整個跳過，第一次讀取緊貼在 agent 回應之後，競速最激烈。

**與 eval run 的四個差異**

| | eval run | playground attempt |
|---|---|---|
| judge | 必跑 | **只有給了期望答案才跑** |
| diagnosis | 只診斷 judge 判錯的題 | **只看有沒有給期望流程**（可以沒有 verdict）|
| judge 失敗 | 該題 `failed` | **不算失敗**，答案與 trace 仍然值得看，原因記在 attempt 上。<br>（沒有任何東西在聚合這些數字，所以沒有通過率可以被灌水）|
| 落庫 | 全部 | 完全不落庫 |

**沒有 verdict 的診斷**：`diagnose()` 的 `judge_verdict` 型別放寬為 `Verdict | None`。
prompt 的第四塊**照樣存在**，只是改寫成
「沒有判分：未提供期望答案，所以什麼都沒被評分。**不要假設最終答案是錯的**」。
> 把整塊拿掉才是錯的做法——模型會自行推論「答案錯了」，然後去找一個可能不存在的故障。

### 7.4 agent workspace 的讀取與 override

- **從哪台來**：從**使用者在 connection bar 連上的那台**（§10.1），不是後端 env 的
  `AGENT_SKILLS_URL`。三個地方共用這個答案，少一個就會錯：workspace 快照、送出前的版本檢查、
  以及 `create_attempt` 算「改了哪些檔案」用的 baseline。留白才 fallback 到 env。
- **從哪來**：`WorkspaceClient` 一次讀完整份（`WORKSPACE_IMPL=real` 打
  skills 端點），或一份罐頭 workspace（`fake`；skill 目錄名對齊 seed 的
  skill tag，且 `billing` 帶一個 `references/` 檔，因為「skill 是一個目錄」正是舊模型表達不出
  的東西）。回傳 `skills`（扁平的 `{相對路徑: 檔案內容}`）與選填的 `version`；
  `version` 缺席時平台改用 skill 檔的 hash 推導（前綴 `sha256.`），UI 會標示那是推導來的。
- **讀不到要大聲**：讀失敗回 **503 + 原因**，絕不回一份空 workspace。
  > 「這個 agent 沒有 skill」與「你的 URL 錯了」長得一樣的話，開發者會默默地憑記憶重打一份 skill，
  > 然後測到錯的文字。空 workspace 本身是合法答案；**形狀不對**才是失敗。
- **agent 沒有任何 skill 是合法狀態**：Evaluation 照常跑（題目有 skill tag 時出現覆蓋率提醒），
  Playground 照常連得上，skill 面板是一個可以直接新增檔案的空清單。
  只有 Optimize 會擋——沒有 skill 就沒有東西可以優化。
- **override 怎麼傳**：`skill_studio.skills`（見 §3.3），整份取代，
  且 `{}`（這次不用任何 skill）與 key 不存在（用 agent 自己的）是兩件不同的事。
- **怎麼確認生效**：見 §4.10。

### 7.5 權限

路徑上沒有 `eval_set_id`，所以 eval set 的 owner/viewer guard **用不上**。規則是
「**attempt 屬於建立者**」，別人一律 **404 而非 403**。
> scratch work 是私有的，所以「某個 id 上是否存在一個 attempt」也不是別人該知道的事。
> 404 也是 backend 重啟清掉 store 之後會看到的東西，UI 就是這麼說明的。

### 7.6 Shortlist：把 playground 的題目變成 eval set

在 playground 問的題目通常是既有 eval set 沒有的。在 shortlist 之前，留下它的唯一方法是
手動抄進一份上傳檔——實務上就是**留不下來**。

**流程**：attempt 列的 `+` 把它加進 shortlist → 頁首的 `Shortlist N` 開一個對話框
（左：清單；右：逐題編輯；下：新 eval set 的欄位）→ 建立。

**shortlist 存的是複本，而且住在瀏覽器**

| 為什麼不存 attempt id | 為什麼不存後端 |
|---|---|
| attempt 有 per-user 上限會被淘汰，backend 重啟也會全清。存 id 的話，**正好在人迭代最兇的時候**條目會失效 | attempt 本來就不落庫（§4.8）。差別在於 shortlist 是離開 scratch 的那座橋，被一次重啟清掉特別難受——localStorage 免 migration、扛得住重啟、依 subject 分 key |

加入當下複製的是「建立一題所需的全部東西」：題目、答案、流程，加上對話框要用來示警的來源旗標。

**對話框是審閱步驟，不是表單**——兩個捷徑會安靜地摧毀正在被建立的東西：

| 捷徑 | 後果 | 因此 |
|---|---|---|
| 直接把 agent 的回答當期望答案 | 這題等於在主張「agent 現在的答案就是對的」：**它永遠會過，也永遠抓不到答案是錯的**。這是有用的 regression baseline、無用的品質標準，而兩者在通過率裡長得一模一樣 | 預填但掛上琥珀色 `unverified` 標記，欄位下方把後果寫成一句話；使用者一改動標記就消失 |
| 自動用 trace 合成期望流程 | 診斷（§8.2）就是拿 trace 對照期望流程找分歧。**流程若是從同一條 trace 生出來的，就永遠找不到分歧** | 放一個 `Draft from trace` 按鈕，由使用者自己觸發；文案明說「草稿描述的是那一次做了什麼，請改寫成每次都該發生什麼」 |

**還有一個這個系統特有的坑**：帶著 workspace override 跑出來的 attempt，它的答案是
**目前部署的 agent 產不出來的**（skill 寫不回去是 Stage 3）。對話框對這種 attempt 顯示警示、
列出改過的 config path 與檔名，並說明要自己去 agent server 套用。**警示但不阻擋**——
刻意這樣提升也是合理的操作。

**建立時可以一併複製既有 eval set 的題目**

eval set 建立後就鎖定（§4.6），所以「舊題目加上這幾題新的」只可能是一個**用兩邊組出來的新 set**。
對話框列出你有權限讀的 set，勾選就複製它們的題目。

- **複製在 server 做**（`POST /eval-sets/from-shortlist`），權限檢查、去重與 id 政策都留在後端，
  也省掉「為了上傳而先把五百題下載下來」。
- **question_id 一律重新產生**（含複製進來的）。
  > 兩列都自稱是 `q_1a2b3c4d`，在其中一邊被編輯的那一刻就開始各說各話。
- **重複的題目文字會被跳過並計數**，數字寫在成功訊息裡。
  > 被勾選的兩個 set 常常同源；同一個 run 裡問同一題兩次是成本，不是資訊。
  > 安靜地少幾題才是最糟的。
- shortlist 的題目排在複製進來的前面，所以文字相同時**留下的是使用者剛編輯過的那一份**。
- skill tag 一併複製；來源 set 一個字都不會被改到；讀不到的 set 在**寫入任何東西之前**回 404。

---

## 8. LLM 契約（judge、diagnosis 與 synthesis）

### 8.1 Judge

- **輸入**：`question` + `ground_truth_response` + agent 的回答。
  > `question` 是契約的一部分：真實 LLM judge 需要題目本身當 context 才判得準。
- **輸出**（強制 JSON）：`{"verdict": "correct"|"incorrect", "score": 0.0-1.0, "comment": "…"}`。
  `score` 是「答案正確的信心」且必須與 `verdict` 一致；`comment` 要說出決定性的理由——
  判錯時要指名哪個事實錯了或漏了。
- **判準**：**判實質不判字面**。傳達相同事實、數字與結論就算對；措辭、順序、多餘的無害細節不算錯；
  漏掉、寫錯、矛盾的事實才算錯。
- **`JUDGE_SCORE_THRESHOLD`**（選填）：設 0–1 數字則改由分數推導 verdict。
  > 這樣調 pass/fail 門檻不用改 prompt。
  > 它是**部署層**的設定，卻會覆寫模型自己給的 verdict——所以判準編輯畫面上會把
  > 目前生效的門檻顯示出來：改寫了 `score` 的語意、門檻卻還停在部署層，是會安靜出錯的組合。

### 8.1a Judge prompt 可由 eval set 的 owner 改寫

上面那份 prompt 是**預設值，不是固定值**。每個 eval set 可以有自己的 judge prompt，
system 與 user 兩半都可改；改的權限是 **owner only**（§11.1 有完整的理由）。

**兩個方向，都是刻意的：**

| 存在哪 | 空值的意思 | 為什麼 |
|---|---|---|
| `eval_sets.judge_system_prompt` / `judge_user_prompt` | **NULL = 用程式碼裡的預設** | eval set 是**活的設定**。之後改良了預設 prompt，沒有客製過的 set 應該自動受益；建立時把當下的字複製進去，等於把這週的措辭凍進每一個 set |
| `runs.config.judge_system_prompt` / `judge_user_prompt` | 觸發時**寫入全文** | run 是**歷史紀錄**。一份已完成的 run，它的 verdict 只有對著當時的判準才有意義，所以存文字而不是指標 |

> 這兩條看起來矛盾，而且兩條都對。`run_config.resolve` 裡留了註解說明，
> 免得下一個人「順手統一」掉其中一個。

**User prompt 是 template。** 必須含 `{question}`、`{ground_truth}`、`{agent_response}`
三個佔位符。**代換用字串取代，不用 `str.format`**——judge prompt 裡到處都是 JSON 大括號
（`{"verdict": ...}` 正是每份這種 prompt 都會要求的形狀），`format` 會直接炸掉或把它吃掉。

> 缺 `{ground_truth}` 是這個功能最貴的失敗模式：**它不會報錯**，只會拿沒有標準答案的
> 提示去判每一題，然後回一個看起來完全正常的 pass rate。所以檢查是**逐鍵即時**做的，
> 不藏在按鈕後面。

**Verify prompt**：拿使用者挑的那一題判**兩次**——用該題自己的期望答案（應判 `correct`）、
再用一個刻意矛盾的答案（應判 `incorrect`）。

> 只判一次只能證明「回覆 parse 得動」，證明不了 prompt 還分得出對錯；
> 而一個「什麼都判 correct」的 prompt parse 得完美無缺，
> 要等一整個 run 回來 100% 才會有人發現。
>
> 負向那筆刻意是**具體且與期望答案相反**的敘述，不是「我不知道」之類的空話：
> 一個只擋得掉明顯空答案的 judge，仍然可能對所有「看起來像答案」的東西照單全收。

Verify **不強制**（`JUDGE_IMPL=fake` 時根本無從驗起，會回 409），
驗證結果記在 `judge_prompt_verified_at` / `_model`，**任一半 prompt 一被編輯就作廢**——
描述著已經不存在的文字的徽章，比沒有徽章更糟。
驗的是「送來的」prompt（這樣才能在存檔前先驗），但只有在**送來的文字等於已存的文字**時
才會蓋上徽章：徽章描述的必須是 run 真的會用的東西。

**指紋**：`judge_prompt_fingerprint` 是 system + user 的短雜湊，隨 run 一起存。
同指紋的 run 是同一套判準判出來的，pass rate 可比；不同就不可比，run 列表上會標色。

> 這是「版本功能」真正想給的那一半，而**不需要版本表**。過去用過的每一份 prompt
> 本來就躺在各個 `runs.config` 裡，點開 run config 就看得到；
> 沒有的是「版本列表 + 一鍵還原」。

**新的失敗種類 `judge_invalid`**：judge 回了東西但 parse 不出來。
它在 DB 裡仍然是 `status='failed'`、沒有 verdict、不算 pass、**仍留在 pass rate 的分母**
（未判分的題目是未知，偷偷縮小分母會讓「judge 壞掉的 run」看起來比「judge 正常的 run」健康）；
但它在 UI 與 `RunOut.judge_invalid_count` 上被分出來，因為它指向的地方不一樣——
其他失敗是 agent 或網路的問題，這一個幾乎都是這個 eval set 自己的 judge prompt，
而那是 owner 唯一能去修的東西。`llm_max_retries` 不會放大它：
`LlmOutputError` 不在 `RETRYABLE` 裡，同一份壞 prompt 每題只燒一次（外加 `complete_json`
自己那一次修復重試）。

**Playground 是例外**：一個 attempt 不屬於任何 eval set，沒有共用的 pass rate 要維持，
所以那裡的 judge prompt 完全自由編輯。從 run 帶過來的題目會連同那個 run 凍結的 prompt
一起帶過來（composer 上會寫明它從哪來），從首頁直接進 playground 則是系統預設。

### 8.2 Diagnosis（程式碼註解稱為「§6.9 契約」）

**輸入：固定四塊，順序不可變**

1. **任務框架 + 語氣約束（system）**：明確要求「你在提供線索、不是下判決；不確定就說不確定；
   可指出多個可疑點；若懷疑錯不在單一 span 或不在 skill 可控範圍，寫進 `caveat` 而不要硬指一個」。
2. **期望流程**：整段 `ground_truth_reasoning`。
3. **實際 trace（截斷後）**：span 陣列，每個帶 `index / tool_name / status / input / output`
   （＋有值時的 `status_message`）。**`index` 必給**，output 要靠它指回 span。
4. **judge 判錯結果**（verdict + comment）。
   > 讓 LLM 知道「最終答案錯在哪」能大幅收斂搜尋方向；幾乎免費且有效。
   > 沒有 verdict 時（Playground）這一塊改寫成「未判分」，**不是移除**（§7.3）。

**輸出：強制 JSON，不接受自由散文**

```json
{
  "overall_diagnosis": "一兩句白話總結：這條 trace 大致在哪裡開始偏離期望流程",
  "suspects": [
    {
      "span_index": 4,
      "confidence": "high | medium | low",
      "reason": "白話說明為何可疑，以及相對期望流程偏離在哪",
      "evidence": "引用該 span input/output 裡的關鍵片段"
    }
  ],
  "caveat": "可選：懷疑錯不在單一 span(compounding) 或不在 skill 可控範圍(tool/base model) 時填此"
}
```

**真實實作額外做的三件事**
1. **input 依上述四塊固定組裝**。
2. **餵給 LLM 的 trace 先套 §4.4 截斷**（保留所有 span，只截超長 body）。
3. **`span_index` 對照實際送出的 span 驗證，越界的 suspect 直接丟棄**。
   > 前端會自動跳到 `suspects[0].span_index`，LLM 幻覺一個 index 就會讓開發者跳到不存在的 span。
   `confidence` 不在 high/medium/low 之列時正規化為 `medium`。

**LLM 輸出解析**（judge 與診斷共用）
- 要求 `response_format: json_object`（相容性比 `json_schema` 高，很多 self-hosted 端點只支援前者；
  端點若整個拒絕就自動退回不帶該參數）。
- 回來的內容以 Pydantic model 驗證。**解析失敗會把模型自己的輸出與錯誤訊息回丟、給它一次修復機會**；
  再失敗就 raise——**絕不默默塞一個預設值**。
- 也會處理模型自作主張加上的 ```json code fence。

### 8.3 Synthesis（shortlist 的「Draft from trace」）

**它做什麼**：把一條 trace 變成一份 step-by-step 的「agent 做了什麼」，當作期望流程的**草稿**。

**為什麼需要它**：`questions.ground_truth_reasoning` 是 NOT NULL——從 playground 升上來的題目
**沒有它就建不出來**。而一個剛看著 agent 答對的開發者，不該被要求憑記憶重打一份他螢幕上就看得到
的流程。

**輸入**：題目 + agent 的回答 + 截斷後的 trace（與 §4.4 同一套截斷）。
> 回答要一起送，因為最後一步要描述「呈現了什麼」，而最後一個 span 的 output 常常是 tool 結果、
> 不是使用者看到的那句話。

**輸出**（強制 JSON）：`{"reasoning_process": "1. …\n2. …\n3. …"}`

**顆粒度是這個 prompt 的重點**（也是最容易做壞的地方）：

- 一個有意義的動作一個編號步驟：讀了哪個 skill、呼叫了哪個 tool、產出最終答案。
- tool 呼叫要寫**哪個 tool、要了什麼、回來什麼**——用一句話，不是逐字稿。
- **不要**重貼完整 payload、row dump、長 SQL、id 或 timestamp。
- 重複的相同呼叫合併成一步（「對三個月份各查了一次 invoices」）。
- 3–8 步。

**兩個刻意的限制**

1. **只在按鈕上跑，不自動跑。** 草稿描述的是 agent *做了什麼*；那是不是它*該做*的，是只有人能下的
   判斷。自動生成也等於為一堆沒人要的草稿付 LLM 帳單。
2. **不寫回 attempt。** 端點只回傳文字。寫上去等於把一次觀察到的執行，變成下一次執行被評分的標準
   ——而沒有人同意過這件事。

**它不是另一個外部依賴**：`SynthesisClient` 與 judge / diagnosis 共用同一個 LLM client 與同一套
JSON 修復流程，只多一個 `SYNTHESIS_MODEL`。

---

## 9. API 全表

互動式文件由執行中的 backend 提供：`/docs`、`/redoc`、`/openapi.json`。
**這三個和其他 API 一樣需要身分**——`AUTH_MODE=fake` 時是透明的（header 有預設值），
`keycloak` 模式下瀏覽器直接開會 401（一次導覽帶不了 `Authorization` header），
要讀 schema 用 `curl -H "Authorization: Bearer …" …/openapi.json`。

**`GET /health` 是唯一不需要身分的端點**——容器與反向代理的探活要用它。

**權限標記**：`R` = owner 或 viewer；`O` = 僅 owner；`—` = 只需登入身分；
`C` = 僅該 attempt 的建立者。

| 端點 | 權限 | 說明 |
|---|---|---|
| `GET /health` | **公開** | 唯一不需身分的端點 |
| `GET /users` | — | `fake` 模式回可切換的假身分名單；`keycloak` 模式回空陣列（前端據此隱藏切換器）+ 目前身分 |
| `GET /users/lookup?username=` | — | 分享前對員工目錄查核（§11.3）。查無此人 → **404**；目錄連不上 → **200 但 `verified:false`**，前端警告卻放行 |
| `GET /me` | — | 目前 subject 與其在各 eval set 的角色。**UI 不用它 gate 權限**（§11.4）——每個 eval set 的 payload 自己就帶 `my_role` |
| `GET /run-config/defaults` | — | run config 對話框的預填值（env 來源）+ **`*_IMPL` 現況** |
| `POST /agent/skills` | body | 起飛前檢查中**免費的那一半**，打字時就能自動送。**是 POST 但沒有副作用也不花錢**——它可能夾帶 agent 憑證，而 query string 裡的憑證就是 access log 裡的憑證。**三種結果都是 200**，答案放在 `check.ok` 的三態裡：`true` 有清單、`false` 帶 agent server 的原話、**`null` 代表沒設 skills 端點**——那是受支援的組態，不是錯誤。「這台沒有 skill」「你的 URL 打錯」「你沒給 URL」必須是三件事。用自己的 `AGENT_PROBE_TIMEOUT_S` |
| `POST /agent/chat-probe` | body | 貴的那一半：一次真實提問。**絕不自動觸發**。`with_override` 順便驗 skill override 有沒有生效，`with_trace` 驗 trace 讀不讀得回來；三項各自是獨立的 check，因為三個畫面對它們的嚴格度不同 |
| `POST /agent/conformance` | body | 整份驗收清單，給剛寫完 server 的人。包含正常使用永遠碰不到的三項：空 skills map、路徑穿越、override 落地 |
| `POST /eval-sets` | — | 建立（payload 恆為 JSONL + `source_format`）；建立者 = owner；可帶 `shares`；`source_format='python'` 時可帶 `script`（provenance，**無 password 欄位**，多帶會被拒）|
| `POST /eval-sets/script/validate` | — | 上傳 `.py` 的**靜態**檢查（有無 `main`、參數）；不執行、不連 DB、不需憑證 |
| `POST /eval-sets/script/run` | — | 在 sandbox 中執行 script，回傳預覽 row + warning + 上限告知 + stdout/stderr。**script 失敗回 200 帶 `error`**，不是 4xx——traceback 與 print 輸出正是呼叫它的目的。憑證用完即忘 |
| `GET /eval-sets/templates/{python\|csv\|jsonl}` | — | 三種上傳格式的可用範例檔 |
| `GET /eval-sets` | — | 我有權限的卡片。分頁 + 篩選：`?limit&offset&q&metadata_key&metadata_value&sort`，回 `{items,total,has_more}` |
| `GET /eval-sets/metadata/keys` | — | 掃 JSONB 得既有 metadata key |
| `GET /eval-sets/{id}` | R | 單一卡片 |
| `PATCH /eval-sets/{id}` | O | 改 name / description / metadata / **judge prompt**（樂觀鎖 → 409）|
| `POST /eval-sets/{id}/judge-prompt/verify` | O | 拿本 set 的某一題，用**送來的**（未必已存檔的）prompt 判兩次：期望答案本身應判 `correct`、刻意矛盾的答案應判 `incorrect`。`JUDGE_IMPL=fake` → 409 |
| `POST /eval-sets/{id}/judge-prompt/reviewed` | O | 記錄「owner 已看過本 set 的判準」，消掉新 set 上的提示點。不帶版本——它記的是一個動作而非一次編輯 |
| `DELETE /eval-sets/{id}` | O | 刪整個 set（含所有 run / 結果 / 診斷）；底下有 running run → 409（先中止）|
| `PUT /eval-sets/{id}/roles` | O | **整批覆寫**分享名單（操作者本人永遠保留 owner）|
| `GET /eval-sets/{id}/questions` | R | 題目清單 |
| `GET /eval-sets/{id}/skills` | R | 本 set 的 skill tag 與各自的題數，外加沒有 tag 的題數。run config 對話框拿它與 agent 上的 skill 比對（§4.7）。自成一支而非在前端掃 `questions`：後者要為了幾個名字把每題的題幹與 ground truth 全部拉過來 |
| `PATCH /eval-sets/{id}/questions/{qpk}` | O | 改題（樂觀鎖 → 409；`question_id` 不變）|
| `POST /eval-sets/{id}/runs` | R | 觸發 run；body 帶 `name` / `config` / `secrets` / `reuse_secrets_from_run_id`，全部可省略 |
| `GET /eval-sets/{id}/runs` | R | run 列表（含 `incorrect_count` / `judge_invalid_count` / `config` / `credentials_set` / `cancel_requested`）；分頁 `?limit&offset&q` |
| `GET /eval-sets/{id}/runs/{run_id}` | R | 單一 run |
| `POST /eval-sets/{id}/runs/{run_id}/cancel` | R\* | \*owner **或該 run 的觸發者**；非 running → 409 |
| `DELETE /eval-sets/{id}/runs/{run_id}` | O | running → 409（先中止）|
| `GET /eval-sets/{id}/runs/{run_id}/progress` | R | **SSE** 即時進度 |
| `GET /eval-sets/{id}/results` | R | 左欄題目清單；`?run_ids=..&mode=union\|intersection\|last_n&last_n=` |
| `GET /eval-sets/{id}/results/{rid}/trace` | R | 中+右欄：即時抓 trace（完整 body）+ 讀 DB 的診斷 |
| `POST /eval-sets/{id}/results/{rid}/re-diagnose` | O | 手動重診斷（避免 viewer 產生 LLM 成本）|
| `GET /eval-sets/{id}/export/preview` | R | 下載對話框的檔案預覽：各檔真實列數 + **實際欄位名**（由寫檔用的同一組欄位定義供給）|
| `GET /eval-sets/{id}/export` | R | 下載本體；`?questions&runs&traces&fmt=csv\|jsonl&run_scope=all\|latest\|latest_n\|selected&run_ids=`。只選一個檔 → 直接回該檔；多檔 → zip + `manifest.json`；全不選 → 422 |
| `POST /eval-sets/from-shortlist` | — | 用 shortlist 的題目 + 複製既有 set 的題目建立新 set（§7.6）；讀不到的來源 set → 404（**寫入前**檢查）；沒有任何題目 → 422 |
| `POST /playground/workspace` | body | 指定那台 agent 的 config（已移除機密）+ 全部 skill 檔 + 版本；失敗 → **503 + 原因**。前端的 **Connect** 就是這一支。是 POST 而非 GET，因為它可能夾帶 agent 憑證 |
| `POST /playground/workspace/version` | body | 只有版本字串，送出前的過期檢查用；**必須問快照來源的同一台**。同上，POST 是為了憑證 |
| `POST /playground/attempts` | — | 建立 + 起背景 task，201（回 detail）|
| `GET /playground/attempts` | — | 我的 attempt 清單（新到舊，**不分頁**——store 本來就有上限）|
| `GET /playground/attempts/{id}` | C | 詳情，含與 run 相同形狀的 trace payload |
| `POST /playground/attempts/{id}/cancel` | C | 非 running → 409 |
| `DELETE /playground/attempts/{id}` | C | running → 409（先中止）|
| `POST /playground/attempts/{id}/re-diagnose` | C | 無 trace / 無期望流程 → 409；模型失敗 → **502 + 模型自己的錯誤訊息** |
| `POST /playground/attempts/{id}/synthesize-reasoning` | C | 用 trace 生一份期望流程草稿（§8.3）；無 trace → 409；模型失敗或回空字串 → **502**。**不會寫回 attempt** |
| `GET /playground/attempts/{id}/progress` | C | **SSE** |

**幾個回傳值的重點**
- `GET /results` 每題回 `agent_response` / `error_message` / `agent_latency_ms` /
  `verdict` / `judge_score` / `judge_comment` / `status` / `trace_ready` / `has_analysis` /
  `is_incorrect` / **`phase`** / **`run_label`**。
  > `run_label`：多選 run 時這一列是**跨 run 挑出來的代表**，可能來自比正在看的那個 run 更舊的 run。
  > 不標出來很容易誤認。
- `GET .../trace` 回 `spans[]`（含 `status_message`）、`analysis`、`trace_error`、`diagnosis_error`、
  `agent_response`、`ground_truth_response`、`ground_truth_reasoning`、`error_message`、
  以及 §6.4 的 `trace_state`。
  > **Playground 的 attempt detail 沿用同一個 payload 形狀**——這是整個整合最省的一步：
  > 前端的中欄與右欄元件零修改就能渲染。
- **金鑰永不外流**，owner 也一樣。只透過 `credentials_set`（slot 名稱 `llm` / `langfuse`）
  顯示某個 slot 有沒有值。**agent server 自己的金鑰根本不會經過這裡**：契約不再回傳 config。
- `GET /playground/attempts` 每筆回 `workspace_overridden` /
  `edited_skill_files`（相對路徑陣列），讓清單一眼看出這次不是跑在 agent 自己的 skill 上。
- `POST /eval-sets/from-shortlist` 回 `{id, question_count, duplicates_skipped}`。
- **匯出（§6.13 卡片動作）**：`questions.*` 的欄位名是**上傳的那一組**
  （`ground_truth_reasoning_process_description`、單數的 `skill`），不是 API 的
  （`ground_truth_reasoning`、複數的 `skills`）——用 API 的名字匯出，檔案再上傳會 422，
  而使用者會以為是自己的檔案壞了。`test_export.py` 把匯出結果直接餵回 `parse_jsonl` 釘住這件事。
  > 每個檔都多帶 `eval_set_id` / `eval_set_name`：`question_id` 只在單一 set 內唯一
  > （`UniqueConstraint(eval_set_id, question_id)`），下載→編輯→重新上傳之後兩個 set 常常撞號。
  > 系統內部不受影響（所有 join 都走 `question_pk`），但檔案是拿去 pandas / Excel join 的，
  > 只用 `question_id` 會安靜地把不相干的題目併在一起。**匯出檔的關聯鍵＝資料庫的唯一鍵。**
  > 這兩欄對重新上傳是免費的：CSV 與 JSONL 的 parser 都只按名字取欄位，不認得的直接略過。
  >
  > `question_id` **保留**（`from-shortlist` 則刻意產生新的——一個是複製、一個是衍生），
  > 兩者都記在 `manifest.json` 的 `question_id_policy`。
  > **憑證不可能進到檔案裡**：run 列經過 `RunConfig`，它沒有任何憑證欄位也會丟掉未知的 key，
  > 所以就算金鑰被誤存進 `config` 也匯不出去；只有 slot 名稱透過 `credentials_set` 出現。
  > 分享名單（`eval_set_roles`）是 PII，永不匯出。

---

## 10. 前端資訊架構

### 10.1 側邊欄的三個 section

```
側邊欄（可收合成圖示）
├─ Evaluation ← 目前所在
├─ Playground
└─ Optimize          ← Stage 3（§2.3a）

Evaluation（三層下鑽）              Playground                        Optimize
├─ 首頁：eval set 卡片              ├─ 編輯區（問題 + 四個面板）        ├─ 左欄：run 清單
│   run 數、最近通過率、趨勢小折線、  ├─ phase stepper                  ├─ 新 run：六步 wizard（整頁）
│   regression 摘要數、成員數、      │  （Agent→Judge→Trace→Diagnosis） ├─ 總覽：逐 step 圖表 +
│   下載、齒輪                      ├─ 三欄：attempt 清單 │            │   釘住的 step 卡 + 下載
│                                  │        trace+診斷 │ span 細節    ├─ Part 1：一個 step 一個 split
├─ 中層：某 set 的 run 歷史         └─ Shortlist 對話框（§7.6）        │   （依 analyst 呼叫分組）
│   多選 run + union/intersection                                    └─ Part 2：skill 並排 diff
└─ 底層：三欄詳情
    題目清單 │ trace + 診斷 │ span 細節
```

**Optimize 的兩層深頁刻意占滿寬度**，不塞進 run 清單旁邊的右半邊：Part 1 自己就是兩欄
（分組題目清單 + analyst 面板，面板裡還開得出兩欄 span 檢視），Part 2 是三欄文字
（檔案樹 + 並排 diff）。擠在半頁裡，並排 diff 的每一行都會 wrap，而它存在的唯一理由
就是把兩邊對齊。

**run 清單那一欄是 sticky 的**（`top` 讓過 `--topbar-h`，本身超長就自己捲）。右欄是 header
＋圖表＋step 表，高度是左欄的好幾倍，而全 app 只有 `.main` 一個捲動容器——兩欄一起捲的結果
是「往下讀 step 表」等於「把 run 清單捲出畫面」，要換一個 run 得先捲回最上面。

**Playground 先連線，才開始工作（connection bar）**

編輯區之上有一條常駐的 **Target agent** bar：填 `Agent Base URL` 與 `Agent Timeout`、按
**Connect**，Playground 才會動。**Connect 這個動作就是 `POST /playground/workspace`**——
一次呼叫同時證明「連得到」「講的是 skills 端點的契約」並取回 `version` / `skills`，
所以不需要另一支 health 端點（多一支就是多一份會過期的東西）。

**為什麼 agent 不能只是 `Endpoints & keys` 裡的兩個欄位**：LLM base URL、judge model 這些是
**送出一題時**的參數，填錯了下一次送出就知道；agent 的位址是**整個畫面的前提**——
`Agent config` 與 `Skill files` 兩個面板的內容**是從那台 server 讀來的**，沒有它就沒有內容。

> 這不只是資訊架構問題，舊版是一個**沉默的資料錯誤**：workspace 的兩支端點沒有吃前端的
> agent URL（`build_seams(include_workspace=True)` 沒帶 config），一路 fallback 到後端 env 的
> env 的那台，而題目卻送去表單裡打的那台。改了 URL 之後：編輯區顯示的是 A 的 skill、
> override 送去 B、「改了 N 個檔案」拿 A 當 baseline 算、送出前的版本檢查拿 A 比 A——
> 一個**永遠不會失敗的檢查**在守護一個跑在 B 上的實驗。現在兩支端點都收
> `agent_skills_url` / `agent_timeout_s`（留白仍然 fallback 到 env），`create_attempt` 取 baseline
> 也改讀 `body.config` 的那台。

- **gate 的是 composer，不是整頁**。attempt 活在後端記憶體、依 subject 分、和連哪台 agent 無關，
  所以未連線時**左欄清單、三欄檢視、Shortlist 全部照常可用**——為了回頭看一小時前的 trace 而
  被迫先連線是荒謬的。被鎖住的只有：問題輸入框、送出鍵，以及 `Agent config` / `Skill files`
  這兩個內容來自 agent 的面板。`Expected answer & process` 與 `LLM & Langfuse` 不鎖。
- **連上之後 URL 變唯讀，要改得按 Change agent**，並在有編輯時先問。換 agent 等於整份快照作廢，
  留著編輯就會變成「拿 B 的檔案跟 A 的快照做 diff」。
- **env 有 `AGENT_CHAT_URL` 就自動連一次**；`WORKSPACE_IMPL=fake` 直接顯示 `Fake agent` 且完全不 gate。
  否則所有既有的單 agent 部署與純 Docker demo 都會平白退化成「每次多按一顆按鈕」。
- **clone 一個舊 attempt、或從 run 帶題目過來時，agent 欄位會被擋下來**：其餘 config 照抄，
  但不靜默改連線，而是顯示「那個 attempt 跑在 X」+ 一顆切換鍵。靜默改連線正是上面那個 bug 的另一種長相。
- 失敗**原樣顯示 agent server 給的理由並留在畫面上**（不是 toast）：§7.4 的「這台沒有 skill」
  與「你的 URL 錯了」必須分得出來，而只有它自己給的那句話分得出來。

**Playground 的編輯區是一排對等的面板切換，一次只開一個**

`Agent config` · `Skill files` · `Expected answer & process` · `LLM & Langfuse`

原本不是這樣：agent 自己的 `config.json` 藏在一顆叫 **Config** 的按鈕後面，而**這個平台**要打的
端點藏在一顆叫 **Settings** 的按鈕後面，兩排長得一模一樣的按鈕、兩個都叫某種 settings。
現在每顆按鈕都直說自己在編輯什麼（第四顆原本叫 `Endpoints & keys`，agent 那塊上移到 connection
bar 之後，剩下的就只是這個平台的下游服務，名字也就照實改），而且：

- **一次只開一個面板，且每個面板都有高度上限**。編輯區坐在三欄之上，兩個面板同時展開會把
  三欄——以及送出按鈕——推出視窗底部。
- **編輯數量的 badge 留在按鈕上（琥珀色）**，面板收起來也看得到「下一題不會跑在 agent 自己的
  workspace 上」。
- **config 用收合樹渲染，不是攤平的表單**。攤平之後，區分 `tools.sql_query` 底下的 `enabled` 與
  `tools.vector_search` 底下的 `enabled` 的，只剩名稱旁邊一行小灰字。樹是開發者本來就在用的
  心智模型：群組預設收合並標示值的數量，一列一個值讓控制項對齊同一條邊，
  **有編輯的群組會自己展開**（clone 帶進來的 override 不會被藏在收起的三角形後面）。

**為什麼是三層**：開發者長期使用、會累積很多 eval set，每個 set 又跑過很多 run。
單一頁面裝不下，而且**麵包屑 + 一鍵返回是必做而非 nice-to-have**——
一天查十題，每次從頭點會崩潰。

**為什麼是側邊欄，不是頂層分頁條**：section 切換原本用的是 `.segmented` 藥丸元件，
而**同一顆元件也用在頁內篩選**（run 歷史的 incorrect mode、題目清單的 All / Wrong）。
一個視覺同時代表「換到另一個 section」和「篩掉這一頁的一部分」，切換起來就會覺得怪。
側邊欄把兩者分開：**持續可見的目的地**用側邊列，**這一頁的篩選**才用藥丸。
side rail 由一份 section registry（`SideRail.jsx` 的 `SECTIONS`）驅動——
**加一個 section 是加一筆資料，不是在 `App.jsx` 多長一層條件分支**。
Stage 3 的 **SkillOpt 就是第三個 section**（`Optimize`）。它原本掛著 `soon: true`、不可點，
接上時就是把 registry 那筆的 `soon` 拿掉——**加一個 section 真的只是加一筆資料**，
`App.jsx` 沒有為它長出第二層條件分支，這一節的說法在實作時被驗證過了。

**導航狀態在 URL 裡**（hash，因為前端是靜態 bundle、沒有 server-side rewrite，
深層 path 重整會 404，深層 hash 不會）：

| hash | 位置 |
|---|---|
| `#/evaluation` | eval set 卡片 |
| `#/evaluation/{esId}` | 該 set 的 run 歷史 |
| `#/evaluation/{esId}/runs/{id,id}?mode=&n=` | 三欄詳情（含 incorrect mode）|
| `#/playground` | playground |
| `#/optimize` | 佔位頁 |

這不只是整潔問題：**上一頁會逐層退回**、**重整不會掉位置**、
**一條錯題的三欄詳情是可以貼給別人的連結**——最後這點是三個裡最有價值的，
因為「你看這題」正是這個工具被使用的方式。
URL 只帶 id，所以 set 名稱由 `GET /eval-sets/{id}` 補；從卡片點進去時物件已在手上，
會直接帶過去、不重打一次。無法解析的 hash 一律回首頁**並改寫網址列**——
`#/nonsense` 停在網址列上、畫面卻是首頁，等於一條會被複製出去的假連結。

> 這一節刻意**不引入 router 套件**。三個 section、四種 hash 形狀，
> 用 `hashchange` + 一支解析函式就夠了；相依愈少，這個 POC 愈好交接。

**三種 incorrect 判定 mode**（只影響底層**左欄哪些題被列為 incorrect**，不影響其餘部分）：

| mode | 定義 | 用途 |
|---|---|---|
| **union**（寬鬆）| 任一 run 錯即算錯 | 全面盤點「曾經出過問題」的題 |
| **intersection**（嚴格）| 所有選中 run 都錯才算錯 | 找「頑固、穩定會錯」的題——**這批最該投 SkillOpt** |
| **last_n** | 最近 N 個 run 都錯才算 | 找「最近才開始錯」的 regression |

三個 mode 全依賴 §4.6 的穩定 `question_id`。

### 10.2 三個關鍵機制

**① 三欄詳情的即時更新（最容易被誤解的一段）**

- **三欄全部跟著 SSE 走**，不只左欄。
- 開啟中的那一題是**用 id 記住、每次 render 從清單重新查**的（而不是點擊當下複製一份）。
  > 複製一份的話：SSE 進來時清單裡的物件被重建，但手上那份還指著舊的，
  > 由它衍生的 verdict、按鈕可用性全部凍結。
- 中欄與右欄的內容全部來自 `GET .../trace` 這一包 payload。它會在**指紋**
  （`phase|verdict|trace_ready|has_analysis`）改變時重抓——**事件驅動，不是輪詢**。
- **重抓時不清空畫面**，只在標題列顯示一個小圓點。
  > 清空會讓一個正在執行的題目在每個事件到來時閃回空狀態。
- **不搶走開發者手動選的 span**：只有換題、或診斷第一次出現時才自動跳到 `suspects[0]`。
  > 每次刷新都跳的話，正在讀某個 span 的人會被硬拉走。
- 手動 Retry 與 re-diagnose 走一個獨立的 nonce。
  > 重新產生的診斷不會改變 `has_analysis`，光靠指紋看不出來。

**② span payload 的結構化渲染（不截斷，改用收合）**

Langfuse 存的是 agent SDK 交給它的東西，沒有 schema 可驗；但一個 LLM generation 實務上就是
chat-completions 的請求／回應——進去是 `{"tools": […], "messages": […]}`，出來是一則 assistant
message。右欄照這個形狀渲染：tools 一個可收合區塊，messages 每則一個可收合列，
列頭是 **role 色籤**（system / user / assistant / tool）+ 一行摘要 + 字數；
assistant 的 `tool_calls` 另外以工具名 + 重新縮排後的 arguments 呈現。

- **兩條規則**：**(1) 認得就渲染，認不得也要能看**——每個分支都 fallback 到 pretty-print JSON；
  **(2) 收合，不切斷**——每個區塊右上角有 **Pretty | JSON** 切換，JSON 模式是完整未截斷的原始 payload。
- **預設展開狀態**：tools 收起、所有 message 收起、**只展開最後一則**與 Output。
  > 最後一則是這個 span 真正在講的事，其餘是需要時才追溯的脈絡。
- 假層的 trace 也做成同樣的 chat 形狀，所以純 Docker 的 demo 就能驗證這條渲染路徑。

**③ 分頁與清單效能**

畫面渲染只是小的那一半，**真正會讓 app 卡住的是後端查詢**：

| 端點 | 原本 | 現在 |
|---|---|---|
| `GET /eval-sets` | 每個 set 三個查詢，其中一個把該 set **所有 run 的所有 `question_results`** 撈出來，只為了算兩個 run 的 regression | 整頁一次算完的聚合查詢，**查詢數固定** |
| `GET /runs` | 每個 run 一個 `COUNT`（N+1）| 一次 `GROUP BY run_id` |

實測（真 Postgres 16，60 個 eval set、其中一個 80 個 run、共 31,520 筆 `question_results`）：
`GET /eval-sets` 從 **180 查詢 / 209.5 ms → 6 查詢 / 47.4 ms**；
`GET /runs` 從 **80 查詢 / 44.8 ms → 3 查詢 / 4.0 ms**。

- **regression 只需要最新兩個 run**，把 verdict 載入限制在那兩個 run 上。
- **趨勢線只取最近 20 個 run**（window function）。
  > 趨勢是「最近走向」的一瞥，不是檔案庫；沒有上限的話，一個長壽的 eval set 會為了畫 120px 的
  > SVG 而載入它的全部歷史。
- **篩選 / 排序在 SQL 做**：只篩已載入的那一頁，會讓搜尋結果取決於使用者捲了多遠。
- 前端是**無限捲動 + Load more 按鈕**。按鈕不是裝飾：IntersectionObserver 對鍵盤操作不會觸發，
  頁面不捲動時也永遠不會觸發；「Showing N of M」計數回答「還值不值得繼續捲」。
- 分頁 hook 解三個具名問題：**追加時以 id 去重**、**丟棄過期回應**（改了篩選條件後舊請求可能後到）、
  **擋掉重複的併發載入**。`refresh()` 只重讀目前已顯示的範圍，
  所以刪一筆資料不會把捲到一半的清單縮回第一頁。

### 10.3 上傳介面

- 開發者**上傳檔案**（JSONL 或 CSV），檔案在**前端解析**成一張**可編輯的預覽表格**
  （欄位：question / ground_truth_response / reasoning / skill(s) / question_id）。
- 表格可就地編輯、可增刪列（**鎖定規則只在 set 建立之後生效**，所以上傳當下可自由增刪）。
- 按 Create 時前端把表格**重新序列化為 JSONL** 送給後端，並附上 `source_format`。
  > 因此**後端只有一條 JSONL 寫入路徑**；CSV 的 quoting / 換行由前端處理。
- `question_id` 留白代表由後端生成 immutable id。
- **預覽表格分頁**（20 / 50 / 100，預設 20）：不分來源一律套用。列全部留在記憶體、
  全部送出，只有 render 的視窗受限——一份 3,000 列的 set 若整張表都畫出來，
  等於一次掛上一萬多個 `<textarea>`。

**由 Python script 產生（`source_format = 'python'`）**

- 使用者上傳**單一 `.py`**，內含 top-level `main(database_handler)`，回傳 list of dict
  （欄位契約同下表）。系統在 sandbox 中執行它，結果進入**同一個預覽表格**——
  之後的路徑（編輯、Create、鎖定、`question_id` 生成）與檔案上傳**完全相同**。
- `database_handler` 只有一個方法 `run_sql(sql, params=None) -> list[dict]`。
- **憑證不進入 sandbox**：`run_sql` 是回到 server process 的 RPC，連線由 server 持有，
  因此**唯讀、statement timeout、單查詢列數上限、查詢次數上限**都在 script 碰不到的
  地方強制執行。詳見 `backend/app/services/script_runner.py` 的 module docstring。
- **兩段式檢查**：`POST /eval-sets/script/validate` 只做靜態解析（有沒有 `main`、
  參數是不是唯一的 `database_handler`），不執行、不連線；靜態檢查過了 UI 才顯示
  資料庫連線欄位。沒有人應該為了得知自己漏寫 `main()` 而先輸入正式庫密碼。
  `POST /eval-sets/script/run` 才執行。
- **靜態檢查不是安全機制**（不做 import 黑名單）；隔離全部由 runner 負責。
- **可用套件是一份允許清單**：標準函式庫之外只有 `pandas` 與 `tabulate`（numpy 隨
  pandas 而來），清單住在 `backend/requirements-scripts.txt`，裝在與 server 相依樹
  分開的 `/opt/scriptlibs`，由 runner 以 argv 傳給 sandbox child 加進 `sys.path`
  （不能走 `PYTHONPATH`——child 跑在 `-I` 下會忽略它）。這不放寬任何一條隔離：
  sandbox 管的是 script 碰得到什麼，不是它能 import 什麼。
- **數值函式庫在 sandbox 內單執行緒執行**（`OPENBLAS_NUM_THREADS` 等四個變數由
  `_child_environment()` 設定）。numpy 的 OpenBLAS 預設**每個核心開一條執行緒**，
  而 `RLIMIT_NPROC` 連執行緒一起算、且是跨主機 per-uid 計數：8 核機器上 `import pandas`
  會直接撞上限，OpenBLAS 印完訊息後對自己送 SIGINT，使用者看到的是 import 那行的
  `KeyboardInterrupt`。這裡的工作量（≤50,000 列）用不到 BLAS 平行化，而後端只有
  單一 uvicorn worker，讓每支上傳的 script 吃掉所有核心也不對。
- 上限打到時：查詢層（列數、statement timeout）**丟例外進 script**，不靜默截斷——
  用半份資料算出來的 eval set 看起來正常但是錯的；最終輸出上限（3,000 列）則截斷
  並在 UI 上顯著告知。
- 部分失敗比照 JSONL：好的列進預覽，壞的列成 warning。
- **provenance**：script 全文、sha256、目標 DB 的 host/port/name/user、執行者、列數
  存進 `eval_set_scripts`（見 §6.14）。**沒有 password 欄位，也不會有**。

**上傳欄位契約**

| 欄位 | 必填 | 說明 |
|---|---|---|
| `question` | ✅ | |
| `ground_truth_response` | ✅ | 理想答案 |
| `ground_truth_reasoning_process_description` | ✅ | 粗粒度自然語言理想流程，診斷對齊依據 |
| `skill` | ✅ | **list of str**。目前只處理第一個，但型別先設為陣列 |
| `question_id` | 選填 | 跨 run 穩定的 id；未給則系統生成 |

CSV 的欄名同上表，`skill` 儲存格可為 JSON 陣列字面值或以 `,` / `;` / `|` 分隔的字串。

### 10.4 兩個排版陷阱（同一個錯誤的兩種長相）

兩者都是**用手量 chrome，而不是讓結構自己算出來**。手量的數字沒有人在維護，
而且它壞掉的時候不會報錯，只會歪掉一點點。

**① 三欄的高度**

三欄高度原本硬編碼 `calc(100vh - 210px)`——那個數字編碼了「topbar + 麵包屑 + meta 行 + 狀態列」。
新增頂層分頁條時，**它改變了所有既有頁面的 chrome 高度**（實測溢出 13px）。
第一次的修法是把它換成 CSS 變數 `--chrome-h`，**但那個變數從來沒有任何地方設定過**——
所有頁面一路吃 fallback 的 `320px`，等於換了個寫法繼續猜。
Playground 則乾脆放棄視窗推導、固定 `62vh`，因為它的編輯區展開兩個面板時高度會變三倍。

現在**沒有任何視窗算式**：視窗是框（`.app` 高 `100vh`），`.main` 負責捲動，
高度由一條 flex 鏈往下發（`.main` → `.page` → `.page-fill` → `.three`），
欄位自己內部捲動。

> 關鍵細節：`.page` 上的 **`min-height: 0` 才是讓高度「確定」的那一行**。
> 少了它，column flex container 會退回 intrinsic sizing，底下每個 `flex: 1`
> 都解析成 max-content——三欄就會長成題目清單那麼高，而不是視窗那麼高。
> 這是量測出來的（`.three` 高 1140px / 視窗 900px），不是推論出來的。

同一條鏈也順便解掉了 playground 的特例：編輯區展開時 `.three` 縮到它的樓地板
（`min-height`），總高超過視窗，於是**頁面捲動**——不需要為它另訂一個 `62vh`。

**② 麵包屑的幾何**

當時的 header 堆疊裡，麵包屑是**唯一沒有 `max-width` + 置中**的元素
（已被取代的 `.tabbar` 和 `.container` 兩者都有）。
視窗超過約 1450px 之後，它貼在視窗左緣、底下的內容置中——差距隨視窗變寬而變大。
它同時也沒有 wrap 或截斷，所以一個長的 eval set 名稱會把整頁推出水平捲軸。

修法**不是**再給它一份相同的 `max-width`（那只是讓兩份數字暫時相等），
而是把它移進和內容同一個 `.page` 容器裡——**錯位在結構上就不可能發生**。
名稱另外截斷在 32ch、完整名稱留在 `title` 裡。

> 順帶修掉的：麵包屑原本是**沒有 `href` 的 `<a>`**，鍵盤完全走不到；
> 現在是 `nav` / `ol` / 真正的連結，最後一項帶 `aria-current="page"`。

### 10.5 設計系統的 token

顏色、圓角、陰影本來就是 token，**字級與間距不是**（散落 10/11/12/13/14/15/16/17/20/24px
與 7/9/11/12/14/18/22px）。現在補上 `--text-*`（依用途命名，不是依尺寸）、
`--space-1..7`、`--fw-*`，以及 `--page-max` / `--gutter` / `--rail-w`——
麵包屑那個 bug 的根源就是「頁面幾何被抄了好幾份」。

字型過去只在 CSS 裡寫著 `--font: "Inter"`，但**從來沒有任何地方載入它**，
所以標題在 macOS 是 SF、在其他系統是 system-ui。現在三種字型都打包進 bundle：
標題與數字用 **Space Grotesk**、介面用 **Inter**、payload 與 trace 用 **IBM Plex Mono**
（取代十處硬寫的 `ui-monospace, "SF Mono", Menlo`——這個 app 的主要內容就是 payload，
它在每台機器上長得不一樣是實際問題，不是美感問題）。

> **尚未做完**：token 只套用在這次動到的表面（側邊欄、topbar、麵包屑、page shell、
> `.page-head`、`.toolbar`、`.card`）。較深的元件內部（`.upload-table`、`.payload`、
> `.spanrow`）仍帶著自己的數值。這是刻意的——為了統一而攪動 700 行 CSS，
> 會把導航這件事本身埋掉。

---

## 11. 權限、身分與並發

### 11.1 角色

只有兩種角色，掛在 **eval set 層級**：

| 角色 | 可以 | 不可以 |
|---|---|---|
| **owner** | 全部 write（改題、改 metadata、改分享名單、**改 judge prompt**、刪 run / set、觸發 re-diagnose）+ 全部 read + 執行 eval | |
| **viewer** | 全部 read（含三欄錯誤診斷詳情、**讀得到本 set 的 judge prompt**）+ **執行 eval** + 中止自己觸發的 run | 改任何內容、**改 judge prompt**、刪 run / set、**觸發 re-diagnose**（避免 LLM 成本）|

> **「run config 上哪些是 viewer 可以改的？」答案是「全部」**，而這正是 judge prompt
> 被放在 eval set 而不是 run config 的理由。run config 上的每一欄回答的是「連到哪裡、
> 跑多快」——那是呼叫者自己的事；judge prompt 回答的是「什麼算對」，那是這個題庫的事。
> 若人人可帶自己的判準，同一個 set 兩次 run 的 pass rate 就不可比，而整個第二層
> （趨勢、regression、多 run 比較）都建立在可比之上。放在 eval set 也讓它直接沿用
> 既有的 `require_owner`，**不必發明欄位級權限**——那會打破「授權檢查集中在兩個依賴裡」
> 這條規則，而且欄位級的比對（空字串？只差空白？前端沒送？）正是容易寫出漏洞的地方。
>
> 執行面上，`POST /runs` **維持 R**（§6.16 允許 viewer 觸發 run），
> 但 `run_config.resolve` 對這三個欄位是**無條件覆寫**而不是回 403：呼叫者沒有東西
> 要更正，也沒有什麼需要解釋。

- 一個 eval set 可指派多個 owner。
- 授權檢查做成**統一的 FastAPI 依賴**（`require_owner` / `require_reader`），不散在各 endpoint。
- **Playground 不在這個體系內**（§7.5）——它沒有 eval set。

### 11.2 登入的兩種模式（`AUTH_MODE`）

身分是**再一個 seam**，形狀與 §3.2 的七個一致：`AUTH_MODE=fake | keycloak`，預設 `fake`。

| 模式 | 身分從哪來 | 用在哪 |
|---|---|---|
| **`fake`**（預設）| `X-User-Subject` header（或設定檔 `FAKE_USER_SUBJECT`），UI 右上角可下拉切換 | 本機開發、`make test`、seed 出來的 demo、測 owner/viewer 權限矩陣 |
| **`keycloak`** | Keycloak 簽發的 bearer token，subject 取自 `preferred_username` claim | 部署 |

**只有 `current_subject` 認得這個差別。** `role_for` / `require_reader` / `require_owner`
吃的都是一個 subject 字串，兩種模式下一模一樣——這正是原設計「換掉一個依賴即可」的兌現。

**為什麼存 `preferred_username` 而不是 token 的 `sub`**：`sub` 是 UUID。
`eval_set_roles.user_subject` 與 `runs.triggered_by` 本來就是存使用者名稱的 text 欄位、
分享是一個人打同事的帳號、員工目錄也用同一個字串當 key。改存 `sub` 換來的是不可變性，
代價是一次 migration、每個顯示分享名單的畫面都要再查一次姓名、以及一個沒有人讀得懂的資料庫。
**因此這次改動不含任何 migration。** 代價是 username 理論上可被改配；真的發生時後果可回復
（owner 重新分享一次），這才是它是正確取捨而不只是方便取捨的理由。

**大小寫正規化**：`normalize_subject()` 是身分字串進入系統的唯一入口，token 與分享輸入
都走它。`eval_set_roles` 是精確字串比對，一個 `TW12345` 對上一個 `tw12345`
**不會報錯，只會安靜地把 eval set 分享給一個永遠不會登入的帳號**。

**Token 驗證**：JWKS 簽章 + `iss` + `exp` + `aud`。`KEYCLOAK_AUDIENCE` 可設定也可留空關閉——
Keycloak 只有在設了 audience mapper 時才把 client id 寫進 `aud`，否則寫別的（`account` 最常見），
而猜錯會讓**每一張 token 都失敗**。因此驗證失敗的訊息會帶出 token 裡的**實際值**（§4.11）。

**SSE 不用 `EventSource`**：它不能帶 header，身分只能放 query string；而且它重連時會
**重放原始 URL**——access token 只有 60 秒，一次網路抖動就會變成拿著過期 token 無限重試
（症狀是「進度條偶爾卡住，重整就好」）。改為以 `fetch` 讀串流，每次連線都重新取 token。
對外介面與 `EventSource` 相同，所以三個呼叫端各只改一行。

### 11.3 分享

- 上傳時可直接**輸入人名**指定分享對象（subject + role）。
  輸入會先經 `GET /users/lookup` 對員工目錄查核：**查無此人擋下**；
  **目錄本身連不上則警告但放行**——那邊的故障不該讓這邊所有人都不能分享。
- 每張卡片有 **config 齒輪**（僅 owner 見）：一個**分頁**對話框——General（name / description / metadata）、
  Sharing（分享名單）、Judging（judge prompt、門檻顯示、Verify）。
  > 分頁而不是一路往下捲：judge prompt 是兩個大 textarea，疊在 metadata 列下面會把分享名單
  > 推出筆電螢幕，而「捲到找到為止」正是設定從此不再被找到的方式。
  > 齒輪上會在 `judge_prompt_reviewed_at` 為 NULL 時亮一個提示點——意思是
  > 「還沒有人確認過這個 set 怎麼判分」，不是「你的 prompt 是預設值」。
- **第二層（run 列表）也有一顆 Set config**（僅 owner 見），開的是同一個對話框、預設停在 Judging。
  > 想調判準的人正站在結果前面；把他趕回首頁去找卡片是純粹的路徑問題。
  > 但這只解決「方便」——「哪個 run 用的是哪套判準」是另一回事，由每一列上的
  > **judge 指紋 chip** 回答（§8.1a）。
- 每張卡片有 **下載鈕（所有角色都見得到**，run 歷史頁也有一顆）：viewer 本來就讀得到匯出檔裡的每一列，
  擋下載保護不到任何東西，卻正好擋掉最需要它的那群人。對話框的設計見 §9 匯出那段。
- `PUT /eval-sets/{id}/roles` **整批覆寫**分享名單；**操作者本人永遠保留 owner**
  （不可自我鎖出、保證至少一個 owner）。

### 11.4 前端從哪裡讀「我在這個 set 是什麼角色」

**從那個 set 自己的 payload 讀**（卡片列表與 `GET /eval-sets/{id}` 都帶 `my_role`），
不從一份 session 級的角色表讀。

> 原本讀的是進站時抓一次的 `GET /me`（`{set_id: role}`）。**那份表沒有任何東西會讓它失效**，
> 所以在這個 session 裡**新建**的 eval set 根本不在裡面：`my_role` 讀成 undefined，
> 於是自己剛建立、自己就是 owner 的 set，「Edit questions」與「重新診斷」按鈕都不會出現。
> shortlist **每次**都會踩到——建立完就直接導進那個新 set（§7.6），中間沒有重整。
> 上傳建立的 set 同樣中招，只是上傳後通常會先回到卡片列表，比較容易在下次重整後才點進去。
>
> `GET /me` 仍然存在，只是 UI 不再拿它 gate 任何東西：**每個 eval set 的 payload 本來就帶著
> 呼叫者在該 set 的角色**，而那份資料跟那個 set 一樣新。

---

## 12. 設定總表

全部由 `backend/app/config.py`（pydantic-settings）讀取，`docker-compose.yml` 透傳進 backend
container。**金鑰只走環境變數或 repo 根目錄的 `.env`，不會進 image**。

| 變數 | 預設 | 說明 |
|---|---|---|
| `DATABASE_URL` / `SYNC_DATABASE_URL` | 指向 compose 的 `db` | app 用 asyncpg、Alembic 用 psycopg |
| **`AUTH_MODE`** | `fake` | `fake` \| `keycloak`（§11.2）。前端有對應的開關，兩邊必須一致 |
| `FAKE_USER_SUBJECT` | `alice` | 假登入的預設身分（僅 `fake` 模式）|
| `KNOWN_USERS` | `["alice","bob","carol","dave"]` | `GET /users` 回傳的名單（僅 `fake` 模式）|
| `KEYCLOAK_URL` | 空 | **含**部署的相對路徑（例如結尾的 `/auth`），照抄不要自己組 |
| `KEYCLOAK_REALM` / `KEYCLOAK_CLIENT_ID` | `tsmc` / `ai4bi-public` | |
| `KEYCLOAK_AUDIENCE` | `ai4bi-public` | 預期的 `aud`；**留空即不檢查**。猜錯會讓每張 token 都失敗，所以 401 訊息會帶出 token 裡的實際值 |
| `KEYCLOAK_JWKS_CACHE_S` | `3600` | 簽章金鑰快取；遇到未知 `kid` 一律強制重抓，所以這只約束「被撤銷的金鑰還會被信任多久」|
| `HR_API_BASE_URL` | （內部端點）| 分享時查核 username 的員工目錄，key 與 `preferred_username` 相同 |
| `HR_API_VERIFY_SSL` / `HR_API_TIMEOUT_S` | `false` / `5` | 該服務是自簽憑證；裝好公司 CA 之後改 `true` |
| `ROOT_PATH` | 空 | 反向代理剝掉的前綴（nginx 用 `/api`）。不影響路由，只讓產生的 `/docs`、`/openapi.json` 網址帶上前綴 |
| **`SSL_CERT_FILE`** | 空 | 內部 CA 的 PEM 路徑。內部服務（Keycloak / Langfuse / agent server / 員工目錄 / LLM）的憑證由私有 CA 簽發，而 image 只信任 `certifi` 的公開根憑證——不設就是每個對外 HTTPS 呼叫都 `CERTIFICATE_VERIFY_FAILED`。`httpx` 在 `verify=True`（本 repo 每個 client 都是）時會讀它，所以**一個值同時解決五個整合**。⚠️ **它是取代 trust store 不是疊加**，所以檔案必須也含公開根憑證——直接複製 host 的 `/etc/ssl/certs/ca-certificates.crt` 兩者都有 |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | CORS 來源。nginx 單一入口下前後端同源，這段自然失效 |
| `ERROR_MESSAGE_MAX_CHARS` | `2000` | 落庫錯誤訊息的長度上限 |
| `SPAN_BODY_MAX_CHARS` | `800` | §4.4 單一 span body 截斷門檻（**只用於診斷 prompt**）|
| **`AGENT_IMPL`** / **`JUDGE_IMPL`** / **`TRACE_IMPL`** / **`DIAGNOSIS_IMPL`** / **`SYNTHESIS_IMPL`** / **`WORKSPACE_IMPL`** | 皆 `fake` | 每個 seam 各自 fake 或 real，**可逐一切換** |
| `AGENT_CHAT_URL` | 空 | agent 的 chat completions 端點（絕對 URL）|
| `AGENT_SKILLS_URL` | 空 | agent 的 skills 端點（絕對 URL）。**選配**：沒有它 evaluation 照跑，playground、覆蓋率警告與 optimization 才需要 |
| `AGENT_API_KEY` / `AGENT_AUTH_HEADER` | 皆空 | **選配，留白時完全不作用**：沒有 key 就不送任何 authorization header，request 與這兩個變數存在之前一模一樣。認證不屬於 agent server 契約的一部分（`docs/agent-server-api.md` §8），這裡只是讓平台*有能力*送。`AGENT_AUTH_HEADER` 留白 = `Authorization: Bearer <key>`；填 `X-Api-Key` 則送該 header 的原值。key 只在 skills 端點與 chat 端點**同源**時才會一併送過去 |
| `AGENT_TIMEOUT_S` / `AGENT_MAX_RETRIES` | `120` / `2` | |
| `LLM_BASE_URL` / `LLM_API_KEY` | （內部 litellm 端點）/ 空 | **OpenAI 相容**端點，可指向 self-hosted |
| `LLM_TIMEOUT_S` / `LLM_MAX_RETRIES` | `120` / `2` | |
| `SYNTHESIS_MODEL` | `Qwen3.6-27B` | §8.3 的草稿模型；與 judge / diagnosis 共用 `LLM_BASE_URL` |
| `JUDGE_MODEL` / `DIAGNOSIS_MODEL` | 皆 `Qwen3.6-27B` | 兩個用途可用不同模型 |
| `JUDGE_SCORE_THRESHOLD` | 空（採信 LLM 的 verdict）| 設 0–1 數字則改由分數推導 verdict |
| `LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | （內部端點）/ 空 / 空 | HTTP Basic auth |
| `LANGFUSE_TIMEOUT_S` | `60` | |
| `LANGFUSE_OBSERVATION_TYPES` | `["GENERATION","SPAN"]` | 其餘型別（如 `EVENT`）不進 span 列表 |
| `LANGFUSE_TRACE_READ_STRATEGY` | `auto` | `auto` / `trace_api` / `observations_api`（§3.5）|
| `RUN_CONCURRENCY` | `1` | 1 = 嚴格序列 |
| `TRACE_POLL_BACKOFF_S` / `TRACE_POLL_MAX_ATTEMPTS` | `[0.5,1,2,4,8]` / `8` | trace ingestion 等待 |
| `TRACE_SETTLE_DELAY_S` / `TRACE_SETTLE_MAX_READS` | `1.0` / `3` | §6.1a：trace 開始出現後，重讀到 span 數不再增加。設 `0` 次即回到「第一筆讀到的就算數」|
| **`PLAYGROUND_MAX_ATTEMPTS_PER_USER`** | `20` | 記憶體 store 的 per-subject 上限（§5.3）|

> **假層延遲參數集中在另一個檔案**（`app/fake_config.py`）：agent / judge / diagnosis 的 min/max、
> trace not-ready poll 次數、skill 目錄延遲。
> **但 trace poll backoff 與上限不在那裡**——它同時管真實 Langfuse ingestion 的等待，
> 而真實 ingestion 比假層慢一個數量級。

**這些連線設定是「預設值」，不是唯一來源**：`*_IMPL` 是 fake/real 的主開關，
但每次觸發 run（或 playground attempt）會用對話框的值覆寫，並把有效值存進該次紀錄（§4.7）。

---

## 13. 如何執行、從 fake 到 real

### 13.1 跑起來

```bash
SEED=1 ./scripts/dev.sh    # build image → 起 Postgres → migrate → seed →
                           # backend:8000 + frontend:5173
```

或用 `make` 分項：`make db / build / migrate / seed / backend / frontend / test / preflight`。
**host 只需要 docker（含 compose）**。

**seed 假資料**：一個 eval set「Billing Agent Regression Suite」，
角色 alice=owner、bob=viewer、carol=viewer；5 題、3 個 run，通過率 **0.8 → 0.6 → 0.4**
（可見的退步趨勢），使三種 incorrect mode 明顯不同
（union={Q2,Q3,Q5} / intersection={Q2} / last-2={Q2,Q3}）；
含一題帶 **caveat** 的診斷、一題 trace「生成中」狀態、以及一個超長 span body。
> seed 依賴假層（它直接產生假 trace，並在題目文字裡埋 §3.2 的標記），
> 所以 seed 出來的資料是給 **fake 模式** demo 用的。真實模式請自行上傳 eval set 再 run。

### 13.2 逐一帶起真實服務

**每一步都可以獨立驗證，壞掉時範圍很小。**

| 步驟 | 設定 | 這一步該看到什麼 |
|---|---|---|
| **0** | `WORKSPACE_IMPL=real` + `AGENT_SKILLS_URL` | Playground 的編輯器出現**真實的** config 與 skill 檔。**只讀、無副作用，風險最低** |
| 1 | `AGENT_IMPL=real` + `AGENT_CHAT_URL` | 題目打得到真 agent；`agent_response` 有真實回答（判分仍是假的）|
| 2 | 加 `JUDGE_IMPL=real` + `LLM_BASE_URL` / `JUDGE_MODEL` | 通過率與 judge comment 開始有意義 |
| 3 | 加 `TRACE_IMPL=real` + `LANGFUSE_*` | 點錯題看得到真實 span（**前提是 agent server 已套用 correlation_id**）|
| 4 | 加 `DIAGNOSIS_IMPL=real` + `DIAGNOSIS_MODEL` | 診斷、caveat、可疑 span 都由真 LLM 產生 |
| 5 | 加 `SYNTHESIS_IMPL=real` + `SYNTHESIS_MODEL` | shortlist 的「Draft from trace」產出真實的步驟草稿 |
| 6 | agent server 支援 `skill_studio.skills` | 注入的 skill 文字出現在真實 trace 裡；Optimize 的 pre-flight 哨兵句判定為「生效」而不是擋下 run |

**前置檢查**：`make preflight` 會逐一 ping 設為 `real` 的 seam，回報每個 OK / FAIL 與原因。
> 設定打錯時，這比跑一次 eval 才發現快得多。

### 13.3 部署形態

開發與部署是**兩套 compose 疊出來的**，共用一份服務定義：

| 檔案 | 內容 |
|---|---|
| `docker-compose.yml` | 三個服務的共同定義。**刻意不含**任何「開發才有」的東西 |
| `docker-compose.override.yml` | 開發：對外 port、原始碼 bind mount、兩個 reload 迴圈。**compose 會自動載入** |
| `docker-compose.prod.yml` | 部署：`./scripts/prod.sh`（明確指定 `-f`，因此不會載入 override）|

> **為什麼要拆成三份而不是在 prod 覆寫**：compose 疊檔案時，`ports` / `volumes` 這類
> list 欄位是**附加**而不是取代——`ports: []` 拿不掉已經發佈的 port。
> 把開發專屬的東西一開始就不放進 base，才是讓它們在部署時「不存在」而不是「被蓋掉」。

**兩支腳本刻意逐段對齊**，所以 `diff scripts/dev.sh scripts/prod.sh` 本身就是差異清單
（六處，每處在原始碼裡標了 `DIFFERS FROM dev.sh (n/6)`）。
`prod.sh` 唯一不是「同一個指令換個寫法」的步驟是**前置檢查**：部署需要的變數在 compose 裡是
`${VAR:?}`，腳本在 build 任何東西之前先用 `docker compose config --quiet` 驗一次。
檢查交給 compose 而不是自己測，是因為它在「shell 環境 vs repo 根目錄 `.env`」之間的優先順序
很細，自己重寫容易錯得很微妙；但 compose **只會報第一個**缺少的變數，所以腳本另外把完整清單印出來。

**部署形態的四個差別**

1. **frontend 是 `vite build` 的產物 + nginx**（Dockerfile 的 `runner` stage），不是 Vite dev server。
   nginx 監聽 **5173**（與開發同一個網址，所以 Keycloak 只需登錄一組 redirect URI），
   自己送靜態檔、把 `/api/` 轉給 backend。前端因此打的是**相對路徑** `/api`——
   `VITE_API_BASE` 是 build 時燒進 bundle 的，絕對網址會讓每換一個 hostname 就要重 build。
2. **Keycloak 設定走執行時注入**：容器啟動時 `envsubst` 產生 `/config.js`，
   前端從 `window.__APP_CONFIG__` 讀（`src/app_config.js`）。**一個 image 可以部署到任何環境。**
   `/config.js` 與 `/index.html` 必須 `no-store`——被快取住的話，改了環境變數重啟也不會生效，
   而且重整不會好。
3. **backend 沒有 `--reload`、沒有 bind mount、不對外開 port**，migration 在 entrypoint 跑
   （`RUN_MIGRATIONS=1`；`make test` 用 `--no-deps` 所以不能無條件跑）。
4. **db 不對外開 port**，密碼沒有預設值（沒設就拒絕啟動）。

> ⚠️ **單一 worker 是限制，不是還沒優化。** playground 的 attempt store 與 SSE hub
> 都在 process 記憶體裡（§5.3、§15.2）。多 worker 會讓 attempt 隨機 404、進度條隨機不動，
> 兩者都是間歇性失敗。要橫向擴展必須先有共享 bus 與 attempt 落庫——entrypoint 的
> migration 步驟也建立在單一容器這個前提上。

**SSE 在 nginx 後面的必要設定**：`proxy_buffering off`（否則進度條會靜止到 run 結束才一次噴完，
而且不會報錯）、`gzip off`、`proxy_http_version 1.1`、`proxy_read_timeout 3600s`
（§6.5 的 15 秒 ping 讓連線只在確實活著時才撐這麼久）。
`sse-starlette` 本身已送 `X-Accel-Buffering: no`，nginx 認得——上面那幾行是雙保險，
因為這個失敗是安靜的。驗收方式是 `curl -N`：事件必須一條一條冒出來。

**啟動時收尾未完成的 run**：run 是 in-process 的背景 task，backend 重啟後
`status='running'` 的 run 沒有任何東西會收掉它（UI 會一直轉圈，cancel 又因為「已終結」被拒）。
lifespan 啟動時把它們收成 `failed` 並寫明原因。**production 第一次部署就會撞到這個。**

---

## 14. 測試與驗證現況（可信度地圖）

**這一節的目的是讓你知道能信到什麼程度。**

### 14.1 單元測試：295 個

`make test` 跑其中 **267 個**——**不需要 DB 也不需要網路**（外部呼叫一律以 `respx` mock，
LLM 路徑以 monkeypatch）。剩下 28 個（`test_pagination.py` 與 `test_startup_reaper.py` 全部，
加上 `test_shortlist.py` 建立 eval set 的那一半）需要一個真 Postgres，
未設 `TEST_DATABASE_URL` 時自動 skip。

> **測試不經過 HTTP。** 每個測試都是直接呼叫 router 函式並把 `subject="alice"` 當參數傳進去
> （沒有任何測試用 `TestClient` / `AsyncClient`）。所以 §11.2 換掉 `current_subject` 時，
> 既有測試**一個都沒有受影響**——這也是為什麼 `test_auth.py` 必須存在：
> 那條路徑在別的地方完全沒有被執行到。

| 檔案 | 數量 | 涵蓋 |
|---|---|---|
| `test_agent_client.py` | 33 | request body 是一個 chat completion：單一 user message、不送任何取樣參數（那些屬於受測系統本身）、`skill_studio.trace_data`；**`skill_studio.timeout_s` 每次都送、值是 `timeout_s − margin`（逐 run 的 timeout 也會反映進去），且下限是 `timeout_s / 2`——margin 比 timeout 還大時不會送出 0 或負數**；回應解析 `choices[0].message.content`（含 content-parts 陣列）、`finish_reason: length` 保留並標記、`usage` 有就記；空回答與只有 tool_calls 都視為失敗；裸 JSON 字串與 `text/plain` **不再**被接受；307 redirect 會被 follow（實測撞到過）；5xx raise vs 4xx 直接失敗、4xx 顯示 OpenAI error envelope 裡的那句話而不是整包 JSON；逐 run URL / timeout 覆寫；**有 override 時 `skill_studio.skills` 出現、沒有時整個 key 不存在**；`skills: {}` 會被送出（它的意思是「這次不要任何 skill」，而 `{}` 是 falsy——這一條擋的就是真值判斷）；**開頭是 `<` 的 body 不會被當成答案**（200 的 HTML 錯誤頁若被接受，judge 會去評分那段 markup）|
| `test_agent_probe.py` | 21 | 四項檢查的三層判讀：沒回應 → `chat` 失敗且 `override` **未嘗試**（不是失敗）；有回應但沒有 magic 字串 → 只有 `override` 失敗；magic 字串**每次隨機**（寫死的常數遲早會被 hardcode 成通過）；trace 因 ingest 延遲回 `NOT_READY` 時會重試，真的讀不到才失敗，而 trace store 連不上是另一句話；呼叫失敗時**絕不**去問 trace store |
| `test_agent_conformance.py` | 11 | 整份驗收清單，特別是正常使用碰不到的三項：空 skills map 被當成「用你自己的」、override 落地到真實目錄（要兩次呼叫才看得見）、路徑穿越沒被擋；每個 case 都必須有 `why`；空的 skills 欄位**不會** fallback 到 env 的那台 |
| `test_langfuse_client.py` | 27 | 空頁 → NotReady；時間排序與重新編號（**含混用有／無小數秒的 ISO 時間**——字串比較會把 `…:00.500Z` 排在 `…:00Z` 前面；時間讀不懂的 span 仍然保留；同時間以 id 打破平手，使重讀不會重排）；observation 型別過濾；分頁；Basic auth；`usageDetails` 與舊版 `usage` 兩種 token 欄位；ERROR level 映射；401 / 連線失敗 → `TraceFetchError` 且訊息含 host 與狀態碼；**兩條讀取策略**（兩者映出的 span 完全相同、404 → NotReady、auto 命中第一條時不會多打第二條、第一條壞掉會 fallback、**全失敗時兩條的原因都在訊息裡**）|
| `test_judge_and_diagnosis.py` | 24 | verdict 正規化與非法值；門檻覆寫兩個方向；**§4.4 截斷保留所有 span**；越界 `span_index` 剔除；§8.2 四段 prompt 的順序；JSON 修復重試（成功與放棄各一）；**§8.1a 的純函式**——空值回落到內建預設、指紋只跟文字走、佔位符缺漏逐一點名、`{"verdict"...}` 這種 JSON 大括號不會被 `str.format` 吃掉、eval set 的 prompt 真的傳進 judge client |
| `test_orchestrator.py` | 21 | agent 例外只讓該題失敗而 run 仍完成；agent 自報失敗保留原因；**judge 失敗不被當成 correct**、且 `failure_kind` 分得出是哪一步；**judge 回覆 parse 不出來時記成 `judge_invalid`**——不算 pass、仍留在分母、舊資料（NULL）照樣畫成 `failed`；診斷失敗不影響 verdict 且原因落庫；trace store 出錯不讓題目失敗；非預期例外把 run 收成 failed 並送出 SSE 終止事件；重試上限；併發；第一次呼叫 agent 前所有 result 列已建好；中止前未開始的題目留 pending；**中止會放棄進行中的 agent 呼叫**；已判分的結果在中止後保留；五個事件依序送出且帶齊指紋欄位；**送去診斷的是 settle 過的 trace**（§6.1a——落庫的診斷沒有第二次機會）|
| `test_playground.py` | 50 | 四階段依序推進；**沒填期望答案 → judge 呼叫次數為 0**、**沒填期望流程 → diagnosis 呼叫次數為 0**；`judge_verdict=None` 時 prompt 第四塊說「未判分」且四塊順序不變；skill override 傳到 agent；**編輯過的檔案是對著送出當下的快照算的**（沒有基準的話每個檔案都會被算成改過）；空 override 不送出；baseline 跟 agent server 要而不是信瀏覽器；agent 連不上時只損失摘要不損失 attempt；**override 的文字出現在假 trace 第一個 span 的 system message**、沒有 override 的 attempt 則乾淨；四種失敗政策；**中止放棄進行中的呼叫**（30s stub + 2s `wait_for` 斷言）；中止保留已拿到的答案；SSE 事件與指紋；store 上限淘汰最舊**但不淘汰還在跑的**；跨 subject 404；**金鑰不外流的值層級斷言**；五種 trace_state；檢視路徑不截斷；**最後一個 span 還在 ingest 時會等它**（§6.1a），診斷拿到的也是那份完整的，而 trace 本來就完整時只多一次確認讀取 |
| `test_judge_prompt.py` | 14（**全部需 DB**）| **viewer 送的 judge prompt 被丟掉而 run 記下 owner 的**（這個功能的整個權限故事）；`require_owner` 擋改、`require_reader` 仍放行讀與觸發；set 改了之後 run 仍記著當時的全文與指紋；存回預設文字不會把預設釘死；編輯清掉 verified 徽章；Verify 兩個方向都對才算過、**「什麼都判 correct」的 prompt 驗不過**、驗未存檔的編輯不蓋徽章、**缺 `{ground_truth}` 時兩筆都如預期也仍算失敗**；`JUDGE_IMPL=fake` → 409；別的 set 的題目 → 404；manifest 帶著判準 |
| `test_run_config.py` | 22 | `build_seams` 空設定等同純環境變數行為；**三個 judge-prompt 欄位是 eval set 的，`resolve` 一律丟掉 body 送來的值**（有沒有帶 eval set 的 prompt 都一樣）；`defaults()` 刻意不含它們（它們沒有 env 來源），但 `resolve()` 仍然吐出每一個欄位；`*_IMPL` 仍是主開關；逐 run 值覆寫 env；空白欄位退回 env；judge 與 diagnosis 共用同一個 LLM client；`resolve()` 把留白寫死；金鑰沿用的端點配對規則；**金鑰不外流的值層級斷言**（序列化一個帶哨兵金鑰的 model，斷言哨兵不出現在 payload 任何位置——比檢查欄位名可靠）|
| `test_workspace_client.py` | 13 | 整份 workspace 讀取；**config 保持巢狀不被攤平**；空 workspace 合法 vs 形狀不對則失敗；沒有 version 仍可用（只是失去過期檢查）；skills 不是 `{路徑: 文字}` 時報錯並指名是哪一筆；4xx/5xx 帶狀態碼與 body；非 JSON body 不猜；transport 錯誤帶 host；版本端點沒有 version 時報錯（**回空字串會被讀成「沒變」而讓檢查失效**）|
| `test_shortlist.py` | 18（**12 個需 DB**）| synthesis：草稿來自 trace、**不寫回 attempt**、無 trace → 409、模型錯誤原文回傳、空草稿算失敗、別人的 attempt → 404。建立：只用 shortlist 建立；複製既有 set 的題目；**複製件拿到新的 question_id**；skill tag 一併複製；重複題目文字被跳過並計數；同文字時 shortlist 的版本勝出；**讀不到的 set 回 404 且什麼都沒建**；空的建立請求 → 422；建立者是 owner 且分享名單生效；**新 set 第一次被讀取就回報正確的 `my_role`**（owner / 被分享者 viewer / 兩條建立路徑都是——這正是前端 gate 權限用的欄位，§11.4）；沒有角色的人連讀都讀不到 |
| `test_trace_settle.py` | 14 | §6.1a 的 settle：最後才到的 span 會被等到；沒有成長就立刻結束（穩態只多一次請求）；成長時有上限；長度相同時採用較新的一份；**比較短的重讀、NotReady、確認讀取失敗一律不採用**；中止立刻停；`TRACE_SETTLE_MAX_READS=0` 回到舊行為；delay 真的有等；與 poll 的組合（NotReady → 出現 → settle）；**只有 settle 失敗時 `trace_error` 保持 None**（有 trace 就不該亮紅色 banner）|
| `test_run_lifecycle.py` | 11 | cancel 的權限矩陣（owner ✓ / 觸發者 ✓ / 其他 viewer ✗）；非 running → 409；跨 eval set → 404；delete 為 owner-only 且 running 時 409 |
| `test_export.py` | 25 | 匯出（§6.13 卡片動作）。核心是**把匯出結果直接餵回 `parse_jsonl`**——`questions.*` 對外承諾「可重新上傳」，而它用的欄位名是上傳的那組而非 API 的那組，正是日後重構最容易「順手對齊」掉的東西；另有 **provenance 欄位隨行但不破壞重新上傳**、CSV 的 skill 寫成 JSON 陣列字面值（技能名含逗號時才不會被拆開）、Excel 需要的 BOM/CRLF、**results 是每 (run × question) 一列而不是每題一列**（後者會安靜地丟掉除最新以外的所有 run）、跑到一半的題目照樣匯出成 pending、空表仍寫表頭、zip 位元組可重現、**金鑰值層級斷言**（run 同時在 `config` 與 `secrets` 放進哨兵金鑰，搜整包 zip）、manifest 不帶分享名單、preview 的欄位名與寫檔器同源 |
| `test_results.py` | 8 | trace 檢視的狀態機。核心是**`pending` 的題目回 `not_started` 且對 trace store 發出零個請求**（用會記錄呼叫次數的 stub 斷言）|
| `test_deletion.py` | 5 | `delete_run` / `delete_eval_set` 的 DELETE **順序**（子表先於父表，特別是 `question_results` 必須早於 `questions`），以及一個「schema 新增子表卻忘了加進刪除順序」的守門測試 |
| `test_pagination.py` | 11（**需 DB**）| `limit`/`offset`/`total`/`has_more`；翻完所有頁**每張卡剛好出現一次**；只列出有權限的 set；搜尋與 metadata 篩選在 SQL 生效；趨勢受上限；regression 用最新兩個 run。**最重要的兩個是查詢數守門測試**：`GET /eval-sets` 與 `GET /runs` 在 `limit=1` 與 `limit=20` 時發出的查詢數必須**完全相同**——斷言時間會 flaky，斷言查詢數不會 |
| `test_auth.py` | 24 | §11.2 的身分。**fake 模式行為完全沒變**（其餘測試全靠這一點）；keycloak 模式對缺 token／非 bearer／壞簽章／過期／跨 realm 一律 401；用**本地產生的 RSA key 簽 token**、以 `respx` mock JWKS 驗證合法 token 通過；金鑰快取，**未知 `kid` 只重抓一次**（不能變成每個 request 都打 Keycloak）；取 `preferred_username` 而**不是** `sub`（參考實作把這個 claim 拼錯、安靜地 fallback 到 `sub`，那會把 UUID 塞進放使用者名稱的欄位）；**audience 不符時訊息帶出 token 裡的實際值**；`KEYCLOAK_AUDIENCE` 留空即跳過檢查；`normalize_subject` 的大小寫與空白 |
| `test_user_lookup.py` | 11 | 分享前的目錄查核（§11.3）。**「目錄說沒有」與「目錄沒回答」是兩個不同答案**，只有前者擋下——兩者合併在任一方向都是錯的：都擋，那邊一出事這邊全公司不能分享；都放行，打錯字的問題又回來了。含 404 擋下、逾時／連線失敗／非 JSON body 一律 `verified:false` 但放行、**200 但 body 只有 `detail` 也算查無此人**（有些部署這樣回，只看狀態碼會放行）、查核前先正規化 username、fake 模式走 `known_users` 以便離線開發 |
| `test_startup_reaper.py` | 5（**需 DB**）| §6.2 最後一列。`running` 的 run 被收成 `failed` 且**帶得出原因**與 `completed_at`；**已結束的 run 一個字都不能改**（改了就是竄改 §4.6 賴以成立的歷史）；只動 `running` 的；**重跑兩次不會重複改寫**（連續重啟兩次）；空資料庫不算錯誤 |

### 14.2 端到端驗證

> 以下是**歷次開發累積**的驗證紀錄，不是每次改動都全部重跑。慣例是每次補強都跑一輪
> 真 Postgres + 真瀏覽器的檢查，並要求 **0 console / page error**。

**fake 模式**：真 Postgres 16 + 真瀏覽器（Playwright + Chromium）。
走過首頁 → run 歷史 → 三欄詳情三層；卡片分頁與 Load more 追加無重複；搜尋跨全部分頁生效；
多選在追加後仍保留；觸發 run 後停在同一題不做任何切換，中欄自己長出答案 → verdict → trace spans；
手動選的 span 在多次背景刷新後仍是選中的那一個；未開始的題目顯示「等待 agent」而非 trace 錯誤；
中止 44ms 生效；權限矩陣；刪除的 403/409/204；light/dark 兩個主題。

**Playground**：同上環境，**33 項檢查全通過、無 console error**。含既有三層動線迴歸
（三欄沒有被裁切、頁面不再垂直捲動）、從錯題帶入、workspace 載入與編輯、
**送出後留在原地看中欄自己長出答案 → verdict → trace → 診斷**、
**改過的 skill 文字出現在 span payload 裡**、只有問題的 attempt 的兩個階段畫刪除線、
中止、clone、兩個主題。

**workspace 編輯器與 shortlist**：真 Postgres + 真 backend + 真瀏覽器，light/dark 兩主題、
1440 與 1180 兩種寬度。改一個 config 值 → 面板收起來後琥珀色數字還在 → 送出 → attempt 顯示
「sent with an override of 1 config value」；改／刪 skill 檔後**磁碟上的 workspace 沒有被改到、
版本字串沒有變**；下一個沒有 override 的 attempt trace 乾淨。
shortlist：加入 → `Draft from trace` → 勾一個既有 set → 建立 → 跳到新 set 且顯示
「Created with 3 questions」，用 API 確認升上來的那題帶著生成的流程、複製進來的兩題保留 skill tag
且拿到新 id；**重新整理頁面後 shortlist 還在**（localStorage），建立後清空。

**匯出下載**：真 Postgres + 真 backend + 真瀏覽器。
**完整 round trip 走真 HTTP**——匯出 `questions.jsonl` → `POST /eval-sets` → 新 set 的 5 題
與原本逐欄相同，**skill 與 question_id 都保留**（也就順帶造出「兩個 set 共用同一批 question_id」
這個 provenance 欄位存在的理由）；匯出的 CSV 餵給**真正的 `upload_parse.js`**（Node 直跑）
0 parse error、0 validation error，BOM 與 skill 陣列都正確；權限與既有端點一致
（無角色 → 403、viewer → 200）；只選一個檔回檔案本身、多檔回 zip + manifest、全不選 → 422；
`traces.json` 如實回報 4 ready / 1 generating；整包 zip 搜不到任何憑證值。

> 瀏覽器這一輪抓到兩個**測試抓不到**的錯：
> ① 記憶下來的偏好在沒有 run 的 set 上，讓標題宣稱「你會拿到 .zip」但實際只有一個 CSV——
> checkbox 用的是有效選擇、檔名算的是原始選擇，兩者不同步。這個 bug 正好打在這個功能唯一的賣點
> （面板可信）上。② 檔案存下來叫 `export`（沒有副檔名）：跨來源 fetch 讀不到
> `Content-Disposition`，除非後端明講 `expose_headers`。**同源部署（nginx 代理 `/api`）永遠不會遇到**，
> 所以它會一直潛伏到有人把前端指向另一台機器的後端。

**Langfuse 錯誤路徑**：用一個回傳真實 `Unknown table expression 'events'` 500 body 的 mock，
確認兩條策略都被嘗試、錯誤訊息含兩者、瀏覽器顯示白話說明且原始 SQL 收在可展開區塊。

**Keycloak（`AUTH_MODE=keycloak`）**：**已在公司內部環境對接真實 realm 驗證通過**——
開發形態（Vite dev server + `uvicorn --reload`）配上真 Keycloak，登入導轉、換 token、
帶著 bearer token 打 API、SSE 即時更新都正常。
過程中撞到的兩件事都被設計預期到了，各花約一分鐘解決：

| 撞到什麼 | 為什麼一分鐘就解決 |
|---|---|
| `aud` 實際是 `account`，不是 client id | 401 訊息直接把 token 裡的實際值印出來，照著設 `KEYCLOAK_AUDIENCE` 即可（§11.2）|
| 後端讀 JWKS 時 `CERTIFICATE_VERIFY_FAILED` | 錯誤訊息帶了完整 URL 與原因，而 host 上 `curl` 同一個網址是通的——對比直接指向「容器少了內部 CA」，設 `SSL_CERT_FILE` 解決（§12）|

> 這兩件事是 §4.11「錯誤必須看得見，而且要能分辨種類」最直接的回報。

**SSE 串流 client**：`EventSource` 換成 `fetch` 之後（§11.2），parser 以
**`sse-starlette` 自己的 encoder 產生的真實 bytes** 驗證，並切到**每次只讀一個 byte**，
讓每個 CRLF、每個 frame 邊界、每段 JSON payload 都被拆散在多次 read 之間。
> 這一項是補課。第一版的測試是**手寫 `"\n\n"` frame** 餵進去的，於是驗的是作者的假設而不是
> 伺服器的行為——`sse-starlette` 的行尾是 **CRLF**，`"\r\n\r\n"` 裡沒有兩個連續的 `\n`，
> 所以那個 parser **一個事件都派發不出來**，測試卻是綠的。真實 bytes 之外，
> 現在也會先確認測試在舊 parser 上會失敗，才拿它驗新的。

### 14.3 ⚠️ 哪些**沒有**被證明（最重要的一段）

| 項目 | 狀態 |
|---|---|
| **Langfuse 讀取** | ✅ **已對接真環境**，真實 trace 讀得回來也渲染得出來。token 欄位兩種命名都處理過 |
| **Keycloak 登入（`AUTH_MODE=keycloak`）** | ✅ **已對接公司內部真實 realm**（開發形態）：登入導轉、換 token、bearer token 打 API、SSE 都正常。見 §14.2 |
| **員工目錄查核（`GET /users/lookup`）** | ⚠️ 平台這一側有 `respx` 單元測試涵蓋三條路徑（找到／查無此人／連不上）。真實目錄**尚未在本文件更新時完成對接驗證** |
| **nginx 部署形態** | ❌ **尚未在真環境跑過**。單元層面驗過 compose 疊加後的變數解析、`prod.sh` 的前置檢查兩條路徑、`/config.js` 注入鏈；但 **build、啟動、以及 SSE 有沒有被 nginx 緩衝**都還沒實測。§13.3 的 `curl -N` 是第一件該做的事 |
| **agent server（chat 端點）** | ❌ **只用自建 mock 驗過**。證明不了貴方的端點是否真的回一個合格的 chat completion。平台內建 `POST /agent/conformance`（UI 上的 Test your server）就是為了讓 agent 端自己驗這件事 |
| **LLM 端點（judge / diagnosis）** | ❌ **只用 mock 驗過**。證明不了貴方端點是否支援 `response_format: json_object`（被拒會自動退回，但仍未實測）|
| **agent skills（skills 端點）** | ⚠️ **平台這一側只有 respx 單元測試**，兩邊**尚未完成對接驗證**。契約已換成兩個絕對 URL 與 chat completions 形狀，agent server 側需要跟著改（見 [`docs/agent-server-api.md`](./agent-server-api.md)）|
| **`skill_studio.skills`** | ⚠️ 同上。真環境要確認的是它**真的被套用**而不是被接受後丟掉——Optimize 的 pre-flight 會自己驗這一項（§17.2），Playground 則只能靠 trace 裡看得到注入的文字 |
| **synthesis（`SYNTHESIS_IMPL=real`）** | ❌ **只用假層驗過**。真模型產出的顆粒度（會不會貼整段 SQL、會不會寫成十五步）需要真資料才知道，prompt 大概率要調 |
| **診斷品質本身** | ❌ **完全未知**。診斷準確度只能在真實資料上跑起來後觀察——而那正是決定要不要投入 Stage 2 的依據 |
| **`LLM_TIMEOUT_S` 的逐 run 版本** | ❌ 未做。`AGENT_TIMEOUT_S` 與 `LANGFUSE_TIMEOUT_S` 都能逐次調整，唯獨 LLM 的 timeout 仍是全域設定 |

---

## 15. 明確尚未做的

### 15.1 維持 Stage 2 邊界（刻意不做）

per-span 機率 / 熱點著色、人工重標 span、多租戶隔離
（多 agent server / 多 Langfuse project）、編輯的即時讀同步。

**Stage 3 已經實作**（§2.3a），但它自己也有一條刻意不做的線：

| 不做 | 為什麼 |
|---|---|
| skill 自動寫回 agent server | 寫回牽涉版本控制與 rollback，是另一個系統。產出 zip、人工放回，換來的是「這份 skill 是誰在什麼時候放上去的」仍然由人負責 |
| test split | 第三個 split 會讓每一份數字都要再解釋一次它是哪個 split 的。**用本系統既有的 evaluation 功能對 optimized skill 重跑一次**，就是無偏驗證，而且那條路本來就存在 |
| `rewrite_from_suggestions` / 整份重寫 | v1 只做 `patch`。整份重寫的 diff 沒有人讀得動，而 diff 是這個系統的安全機制之一 |
| slow update / meta skill 預設關閉 | 兩者都已接線（`optimizer/longitudinal.py`），由 run config 的 `slow_update` / `meta_skill` 開啟，**預設關閉**。它們在 epoch 邊界跑，不是每個 step——單一 epoch 的 run 沒有邊界可比，兩者都不會執行 |
| 多次取樣壓抑溫度雜訊 | 成本翻倍。改為在 UI 上誠實說明單次取樣的限制 |
| 金鑰加密 | 沿用 `runs.secrets` 的既有明文模式，不新增例外，也不假裝解決了 §15.2 |

### 15.2 Stage 1 / 4 範圍內但確實還缺的

| 缺口 | 說明 |
|---|---|
| **Langfuse 只讀不寫** | verdict 應同時寫成 Langfuse Score（`source=API`），**尚未做**。目前 app DB 是唯一真相，Langfuse UI 上看不到本平台判的分數。eval set 也沒有寫進 Langfuse Dataset |
| **span tree 不重建** | Langfuse 回傳的 `parentObservationId` **完全未使用**，目前以**依 startTime 排序的平舖列表**呈現。樹狀結構留給 Stage 2 的熱點檢視 |
| **`LLM_TIMEOUT_S` 沒有逐 run 版本** | 補法很小：`RunConfig` 加欄位、defaults 加一行、往 client factory 傳進去、對話框加一格 |
| **run config 無法比對** | 唯讀檢視一次只能看一個 run；要並排 diff 兩個 run 的設定還得自己切換 |
| ~~**真登入**~~ | ✅ **已做**：Keycloak OIDC，`AUTH_MODE=keycloak`（§11.2）|
| **run 的金鑰是明文落庫** | `runs.secrets` 存的是使用者在對話框輸入的 LLM / Langfuse 金鑰。§4.7 的「不外流到 response」是結構性保證，但**資料庫裡是明文**。部署形態該把金鑰改成由部署層提供、對話框只留端點與模型 |
| **DB 沒有備份策略** | 只有一個 docker volume。而這個系統的價值主張建立在 §4.6「run 是不可重寫的歷史紀錄」上——**歷史沒有備份，那個主張就不成立** |
| **沒有 structured logging / audit log** | 一個會打五個外部服務、每次呼叫數十秒的 orchestrator，出事時只有 uvicorn 的 access log。有真實身分之後，「誰刪了哪個 eval set」也變得可記錄而且該記錄 |
| **`/health` 不檢查 DB** | 只回 `{"status":"ok"}`。當 liveness 夠，當 readiness 不夠 |
| **`HR_API_VERIFY_SSL` 預設是關的** | 當初關掉是因為容器沒有內部 CA。`SSL_CERT_FILE`（§12）就位之後這個可以也應該改回 `true`——那是唯一還在跳過憑證驗證的地方 |
| **committed 的開發用 DB 密碼** | `docker-compose.yml` 仍帶著 `POSTGRES_PASSWORD` 的開發預設值。部署形態已經要求必填、拒絕沿用，但 repo 裡那個字串還在 |
| **shortlist 只在單一瀏覽器** | 它存在 localStorage（§7.6）。換一台機器或換一個瀏覽器就看不到自己的 shortlist。要跨裝置就得落庫，而那要一張表與一次 migration |
| **升上來的題目沒有血緣紀錄** | 新 set 不會記載「這幾題來自哪個 set / 哪個 attempt」。`questions` 沒有 metadata 欄位，寫進 eval set 的 metadata 又會污染使用者自己的篩選鍵 |
| **Playground 不落庫的連帶限制** | backend 重啟清空 attempt；多 worker 部署會壞（與 SSE hub 同一個限制）|
| **窄視窗只到「能用」** | 側邊欄在 1100px 以下自動收成圖示、三欄在 900px 以下改為堆疊，已驗證 780px 不出現水平捲軸。但這是**桌面工具**，手機尺寸沒有設計過 |
| **設計 token 只套一半** | `--text-*` / `--space-*` 只用在這次動到的表面；`.upload-table`、`.payload`、`.spanrow` 等仍是硬寫數值（§10.5）|

---

## 16. 已知風險與未解問題

按嚴重程度排列，並標注**現在的狀態**。

| # | 風險 | 狀態 |
|---|---|---|
| 1 | **question ↔ trace 的關聯**：eval 系統打 agent 後，如何得知該題對應哪條 trace | ✅ **已解**：correlation id 注入（§3.3）。但這依賴 agent server 端配合 |
| 2 | **Langfuse ingestion 是非同步的**：agent 回應後 trace 不一定馬上可查 | ✅ **已處理**：poll + 指數退避；UI 明確區分「生成中」與「真的沒有」與「讀取失敗」（§6.4）|
| 2a | **ingestion 還是逐筆的**：第一次讀到 observation 時 trace 可能還在長，最後一個 span（最終回答生成）最容易缺席 | ✅ **已處理**：讀到之後 settle 到 span 數不再增加才採信（§6.1a）。🟡 但 settle 窗口是有限的（預設約 3 秒）——ingestion 比它更慢時，playground 的 attempt 仍會凍結一份短的 trace，只能靠 `TRACE_SETTLE_*` 加大窗口 |
| 3 | **粗粒度自然語言 reasoning ↔ 具體 span tree 的對齊是模糊問題**——這是整個定位功能的核心風險。多條同樣有效的路徑可能被誤判；粒度不匹配（ground truth 說「用 SQL tool 取資料」，trace 有多次 tool call / 重試）；**錯誤不一定能歸到單一 span**（compounding / emergent error）| 🟡 **部分承接**：Stage 1 用 `suspects[]` 陣列 + 三檔 confidence + `caveat` 逃生口在資料結構層容納不確定性（§4.1）。但**準確度本身完全未驗證**——這是 Stage 2 是否值得做的判斷依據 |
| 4 | **correct/incorrect 的判準**：LLM judge 可能給連續分數或「部分正確」，二元化門檻要定義 | ✅ **已定案**：LLM 同時吐 verdict + score；另有可選的 `JUDGE_SCORE_THRESHOLD` 由分數推導。🟡「部分正確」的分級**未做** |
| 5 | **skill-selection 錯誤沒被涵蓋**：題目標了「該用 skill X」，但 agent 可能**讀錯 skill**（常見 bug）。若錯在選錯 skill，錯誤歸因與優化對象都會指錯 | 🟡 **未專門處理**。Stage 1 只能靠 `caveat` 粗略承接。原設計建議：額外比對「agent 實際讀的 skill」vs「題目標註的 skill」，不一致時把讀 skill 的那個 span 標為高機率錯誤來源 |
| 6 | **SkillOpt 的施力點假設過強**：假設「錯 → 優化 skill 就能修」，但錯誤可能在 SQL tool、base model 或 skill 以外 | 🟡 **已用 caveat 預先承接**（§4.2）：有 caveat 的題目在 Stage 3 預設不納入樣本 |
| 7 | **重跑實驗需要 agent server 端的新能力**：per-request skill override | ✅ **平台側已做**。契約在本次改版中換了形狀（`metadata.skills`），agent server 側需要跟著改；Optimize 的 pre-flight 現在會**主動驗證** override 有沒有生效並在沒生效時擋下 run（§17.2）|
| 8 | **非決定性讓「trace 是否不同」不可靠**：LLM 有溫度，重跑幾乎必然有差異 | ✅ **設計上承認**：Playground 不做「一按跑 N 次取多數」，也不做自動的改善判定；由開發者自己多按幾次。Stage 3 若要自動驗證改善，應以 score / outcome 比較（甚至重跑 N 次取多數），**而非比對 raw trace diff** |
| 9 | **「存回 agent server」是新平台對 agent server 的寫入耦合**：需要 skill 更新 API + 版本控制 / rollback | 🔴 **Stage 3，未做** |
| 10 | **成本 / 規模**：把整條 trace 餵給 LLM 做錯誤定位，token 成本可能很高 | ✅ **已處理**：只截 body 不砍 span（§4.4）；診斷生成一次就落庫、只有手動才重算（§4.5）|

**仍然開放的問題（給 Stage 2 / 3 的決策者）**

- 🟡 per-span 出錯機率採**各 span 獨立**還是**加總為 1**（假設剛好一個元凶）？兩者的演算法與 UI 完全不同。
  建議獨立機率，以容納 compounding error——但這需要真實資料支持。
- 🟡 reasoning ↔ span 的**軟對齊演算法**、多條有效路徑如何處理。
- 🟡 skill-selection 錯誤與 skill 範圍外錯誤（tool / base model）如何在 UI 與 SkillOpt 中區分。
- 🔴 SkillOpt 的具體輸入 / 輸出契約：需要多少 correct / incorrect 樣本？產出的 skill 格式？
- 🟡 Langfuse 資料 vs app DB 的分工邊界是否維持現狀（§3.4），或某些改放同一邊以簡化。
- 🟡 eval run 的 job queue、大量題目吞吐（目前是單 process 背景 task）。

---

## 17. 對 agent server 端的相依需求

**這些都在本 repo 之外，需要 agent server 團隊配合。**

> **完整契約在 [`docs/agent-server-api.md`](./agent-server-api.md)（英文）。**
> 那份文件是**自包含**的：實作者只要讀它就能動手，不需要知道這個平台的存在，
> 因此它是唯一來源。本節只留下平台這一側需要知道的摘要——契約的任何細節都以那份為準，
> 不要在這裡複述，兩份文件描述同一組端點時遲早會有一份先過期，而讀者無從得知是哪一份。
>
> 歷史沿革：這份契約原本是獨立的 `docs/agent_server_stage4_endpoints.md`，一度被收進本節，
> 現在又拆回獨立文件——差別在於**這次本節不留副本**。程式碼註解裡引用
> `agent_server_stage4_endpoints.md §5.2 / §5.3` 或「§17.3 / §17.4」的地方，指的都是新文件。

### 17.0 需求總表

| # | 需求 | 為什麼必要 |
|---|---|---|
| 1 | chat 端點讀 `skill_studio.trace_data.trace_id`，**用它當 Langfuse trace id** | 沒有這一步，平台無從找回自己剛觸發的 trace，**整個錯誤定位功能失效**，而且 optimization 根本起不來 |
| 2 | skills 端點 → `{skills, version?}` | **選配**。Playground 的連線與編輯起點、覆蓋率警告、Optimize 的 skill 快照由這一支供應；沒有它 evaluation 照跑 |
| 3 | chat 端點讀 `skill_studio.skills`，**只影響這一次呼叫、不落磁碟、不影響其他 request** | Playground 的迭代沙盒與 Optimize 的每一次 rollout 都靠它 |
| 4 | chat 端點讀 `skill_studio.timeout_s` **當這一次呼叫的時間預算**，取代內建的固定上限 | agent server 內部寫死的上限會**蓋掉**使用者在平台上設定的 timeout：調小有效、**調大完全無效**，長題目因此永遠跑不完 |
| 5 | skill 更新 API + 版本控制 / rollback | Stage 3 的「存回 agent server」。🔴 未規劃 |

### 17.1 這一版契約砍掉了什麼，為什麼

| 砍掉 | 原因 |
|---|---|
| `GET /get_config_version` | 它回的字串與 skills 端點的 `version` **是同一個**，分開只為了省頻寬。兩個端點必須在每次部署後保持一致，而不一致時的症狀——過期檢查回答的是另一個時刻——**兩個方向都是靜默的** |
| 回傳的 `config` 與 `redacted_paths` | 只有 Playground 用得到，Evaluation 與 Optimize 都不碰（`optimizer/adapter.py` 刻意送 `config=None`）。它帶來的卻是整份契約最難實作的一條規則：sparse deep-merge ＋ 機密遮罩 ＋「遮罩過的 config 送回去不可以清掉金鑰」。做錯的症狀是 agent 沒有金鑰可用，而那要很久才會被歸因到這裡 |
| `metadata.workspace` 這層包裝 | 裡面只剩 `skills` 一個 key，包裝已無從消歧義 |

代價是誠實的：`version` 現在是**選填**，缺少時平台改用 skill 檔的 hash 推導。
那個 fallback 看不見 model 或 prompt 的變動，也就是說 **isolated 模式下唯一還會影響結果的 drift 偵測不到**——
所以文件把「有能力提供 version 就一定要提供」寫成強烈建議，而 UI 會標示版本是推導來的。

### 17.2 平台如何驗證 override 真的生效

`detect_activation`（`optimizer/detector.py`）只能證明「這個 skill 被讀了」，
不能證明「讀的是我們送的那一份」——候選通常是部署版的小幅編輯，兩者留下的證據無從區分。
因此 agent 若忽略 `metadata.skills`，一個 optimize run 會跑完全程、activation 100%、accuracy 平坦、**零警告**。

Optimize 的 pre-flight 因此會多做兩件事（只有那一次呼叫）：
在送出的 `SKILL.md` frontmatter 之後插入一行只有這次 run 知道的哨兵註解，
並在問題後面附加 `(you must first read the <skill> skill)`，
讓檔案內容無論 agent 是注入 prompt 還是用工具讀檔都會落進 trace。
三值判定：看到哨兵句＝生效；skill 確實被讀到但沒有哨兵句＝**擋下 run**；什麼都沒觀察到＝警告不擋。

細節與 agent 端該預期看到什麼，見 [`docs/agent-server-api.md` §8](./agent-server-api.md#8-the-probe-marker-you-will-see-in-your-logs)。


## 18. 給接手者的下一步建議

**如果你要讓這個系統產生真實價值，按這個順序：**

1. **先接 agent server + LLM（步驟 1–2）**，跑一個真實的小 eval set。
   目標不是漂亮的通過率，而是**確認 correlation 環路通了**：
   `agent_response` 有真實內容，且 `correlation_id` 對得回 Langfuse。
2. **接 `TRACE_IMPL=real`（步驟 3）**，點一題錯的，確認 span 列表與 payload 渲染正確。
   Langfuse 這一段已經對接過真環境，若壞掉先看 §3.5。
3. **接 `DIAGNOSIS_IMPL=real`（步驟 4），然後開始蒐集判斷**：
   診斷指對的比例大概多少？caveat 出現的頻率？多少題其實錯在 tool 而不在 skill？
   > **這一步的觀察是整個專案最重要的一份資料。** 它決定 Stage 2（機率熱點）值不值得做，
   > 也決定 Stage 3（SkillOpt）的前提假設站不站得住。在此之前投入 Stage 2/3 是在賭博。
4. **完成 agent server 那一側的對接驗證**：契約已精簡為兩支端點並換了形狀，
   agent server 需依 [`docs/agent-server-api.md`](./agent-server-api.md) 重新實作一輪，
   其中 `metadata.timeout_s` 若沒做，平台上把 timeout 調得比 agent server 內建上限大是**沒有作用的**，
   跑得久的題目一律在 120s 被砍。
   優先確認的順序是 [`docs/agent-server-api.md` §9](./agent-server-api.md#9-acceptance-checklist)
   的驗收清單，其中 ④ 與 ⑤ 最重要：它們是唯一能證明 `metadata.skills` **真的被套用**
   而不是被接受後丟掉的檢查，而做錯的症狀是一個「成功完成」卻毫無意義的 optimize run。
5. **開 `SYNTHESIS_IMPL=real` 並調 prompt**（§8.3）。假層產出的顆粒度是設計出來的，
   真模型會不會貼整段 SQL、會不會寫成十五步，只有真資料知道。
6. **補 §15.2 的小缺口**時，優先考慮 **verdict 寫回 Langfuse Score**——
   那讓兩個系統的真相一致，成本也不高。

**部署那一側另外有一條線，可以與上面並行**（身分那一層已經在真環境驗過，見 §14.2）：

7. **跑一次 `./scripts/prod.sh`**，這是唯一還完全沒在真環境跑過的一塊（§14.3）。
   最該先確認的是 **SSE 沒有被 nginx 緩衝**——`curl -N`，事件必須一條一條冒出來。
   這個失敗是安靜的：不會報錯，只會讓全 app 的進度條看起來像凍住。
8. **把 §15.2 那幾個維運缺口收掉**，建議順序是
   **DB 備份** → **金鑰改由部署層提供**（`runs.secrets` 目前明文）→ structured logging → audit log。
   第一項最急：這個系統的價值主張建立在「run 是不可重寫的歷史」上。

> ⚠️ **兩件事要分清楚。** 上面 7–8 做完，你會得到一個**部署得起來的** production 系統；
> 1–5 做完，才會知道它**是不是有用的**。§14.3 那張表裡 agent server、LLM 端點、
> workspace 三項仍然只用 mock 驗過，而**診斷品質完全未知**——後者才是這個專案的核心賭注。

**兩件已經想過、但刻意還沒決定的事**（寫在這裡是為了不讓它們默默飄掉）：

- **Data Curation 與 Compaction 的先後。** 傾向 Curation 優先：optimize 的品質上限被 eval set
  的品質鎖死——train / validation split 從那批題目切出來、§15.1 記載
  **還沒有 test split**，而「答案硬編」最結構性的那道防線正是 held-out validation。
  換句話說，**資料品質是目前整個 optimize 迴圈裡最沒有被管理的變數**，
  而 compaction 是在既有品質之上做效率優化。尚未拍板，決定前先看 §14.3 的真實資料。
- **側邊欄的命名一致性與分組。** 現在是 `Evaluation` / `Playground` / `Optimize`，
  兩個名詞夾一個動詞。**觸發條件是加入第四個 section 的那一刻**：屆時一次收成全動詞，
  並考慮分成 Prepare（Data · Evaluation）與 Improve（Playground · Optimize · Compact）兩組，
  而不是繼續往下長成五個平排的項目。現在三個還很舒服，**不要提前動**——
  提前動的代價是每加一個就重排一次。

**如果你要修改程式碼，先讀這四段**：§4（設計決策的理由）、§6.2（失敗策略）、
§10.2（三個前端機制）、§14.3（哪些沒被證明）。
這四段涵蓋了絕大多數「看起來多餘、其實在防某個具體失敗」的程式碼。
