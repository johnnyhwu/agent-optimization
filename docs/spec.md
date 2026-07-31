# Agent Eval 平台 — 系統規格與實作現況

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
> **對照的操作手冊**：repo 根目錄的 `README.md` 是「怎麼跑起來、怎麼接真實服務」的操作手冊。
> 本文件是設計與實作紀錄，兩者互補不重複。

**目錄**

| 章 | 內容 |
|---|---|
| [1](#1-背景與問題) | 背景與問題（為什麼要有這個系統） |
| [2](#2-產品願景與分階段策略) | 產品願景與分階段策略（現在在哪一階段） |
| [3](#3-系統架構) | 系統架構、六個 seam、correlation 機制 |
| [4](#4-關鍵設計決策) | 關鍵設計決策與理由 |
| [5](#5-資料模型) | 資料模型（7 張表 + 記憶體 store） |
| [6](#6-一次-eval-run-的生命週期) | 一次 eval run 的生命週期與失敗策略 |
| [7](#7-stage-4playground) | Stage 4：Playground |
| [8](#8-llm-契約judgediagnosis-與-synthesis) | LLM 契約（judge、diagnosis 與 synthesis） |
| [9](#9-api-全表) | API 全表與權限 |
| [10](#10-前端資訊架構) | 前端資訊架構、三個關鍵機制、排版陷阱與設計系統 |
| [11](#11-權限身分與並發) | 權限、身分與並發 |
| [12](#12-設定總表) | 設定總表（環境變數） |
| [13](#13-如何執行從-fake-到-real) | 如何執行、從 fake 到 real |
| [14](#14-測試與驗證現況可信度地圖) | **測試與驗證現況（可信度地圖）** |
| [15](#15-明確尚未做的) | **明確尚未做的** |
| [16](#16-已知風險與未解問題) | 已知風險與未解問題 |
| [17](#17-對-agent-server-端的相依需求) | 對 agent server 端的相依需求 |
| [18](#18-給接手者的下一步建議) | 給接手者的下一步建議 |

**名詞表**（全文一致使用）

| 名詞 | 意思 |
|---|---|
| **agent** | 待評估的對象：一個 stateless 的 domain agent，公司內部既有系統，**不在本 repo 內** |
| **agent server** | host 該 agent 的 HTTP 服務，單一 `POST /execute` 端點。**不在本 repo 內** |
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
> **後來 agent server 端改為單一 `POST /execute`**（`{"message", "metadata"}` → `{"content"}`），
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
| **Stage 3** | SkillOpt 自動優化、整份 eval set 用候選 skill 重跑驗證改善、skill 寫回 agent server（含版本控制 / rollback）| ❌ 未開始 |
| **Stage 4** | **Playground**：單題即時試打 + per-request **workspace override**（agent 的 config 與 skill 檔）的迭代沙盒，加上把試出來的題目**升級成新 eval set** 的 shortlist | **✅ 已實作** |

**Stage 4 不在原本的三階段藍圖裡**，是後來新增的。它補的是 Stage 1 動線末端的缺口：
開發者看完診斷、心裡有了「如果 skill 這樣改應該就會對」的假設之後，
**原本沒有任何便宜的方式驗證那個假設**——唯一的路是改 eval set、跑一整個 run。
Stage 4 就是那條便宜的路。它刻意**不碰** Stage 3 的難題（不寫回、不做「驗證改善」的自動判定）。

它後來還長出第二個用途：在 playground 問的題目，通常是**現有 eval set 沒有的**——
那正是它被拿到這裡問的原因。shortlist（§7.6）讓那幾題不必被手抄進上傳檔才留得下來。

> **Stage 1 的價值主張**：它解決 §1.2 痛點的約 80%，而且不需要機率、熱點或 SkillOpt。
> Stage 1 的診斷準確度在真實資料上跑起來之後的觀察，**是決定要不要投入 Stage 2 的關鍵依據**。

---

## 3. 系統架構

### 3.1 拓樸

```
┌─────────────────────────────────────────────────────────────┐
│ Eval Platform（本 repo，獨立 app）                            │
│                                                              │
│  Frontend (React + Vite)                                     │
│    側邊欄三個 section（Evaluation / Playground / Optimize）      │
│    ├── Evaluation：三層下鑽（卡片 → run 歷史 → 三欄詳情）        │
│    ├── Playground：單題試打 + agent workspace 編輯 + shortlist   │
│    └── Optimize：SkillOpt 的位置，尚未實作（見 §10.1）           │
│                                                              │
│  Backend (FastAPI async)                                     │
│    ├── Orchestrator：讀 eval set → 逐題打 agent → judge →      │
│    │                 等 trace → 診斷 → 落庫 → SSE 推進度        │
│    ├── Playground：單題版本，狀態只在記憶體                      │
│    └── 六個 seam（fake / real 各一套）                          │
│                                                              │
│  App DB (PostgreSQL)：Langfuse 沒有的概念 + 指回 Langfuse 的索引  │
└───────┬─────────────────────────────┬────────────────────────┘
        │ 讀 trace（HTTP public API）    │ POST /execute（HTTP）
        ▼                             ▼
   ┌──────────┐                ┌──────────────────┐
   │ Langfuse │◀───trace 寫入────│  Agent Server    │
   └──────────┘                │  (stateless agent)│
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
- **DB**：PostgreSQL 16，schema 由 Alembic migration 建立。
- **部署形態**：`db` / `backend` / `frontend` **各自一個 container**，由 `docker-compose.yml` 編排。
  host 端唯一需求是 docker（含 compose）——不需要 host 的 Python venv 或 node_modules。

### 3.2 六個 seam（最重要的一節）

每個外部依賴各藏在一個 **Python Protocol** 後面，**fake 與 real 兩套實作都已存在**，
由六個環境變數逐一切換（`*_IMPL=fake|real`）。**預設六個都是 `fake`**，所以不接任何外部服務
也能跑完整 demo；要接真的可以一個一個開，不必一次全換。

| Seam | 介面 | 假實作 | 真實實作 |
|---|---|---|---|
| `AgentClient` | `call(question, correlation_id, user_id, tags, workspace=None) -> AgentResponse` | 睡 1–3s，回假 response | `POST {base}/execute`，body 見 §3.3 |
| `JudgeClient` | `judge(question, response, ground_truth) -> Verdict` | 睡 0.5–1s，二元判定 | OpenAI 相容端點，LLM 同時吐 verdict + score + comment |
| `TraceClient` | `fetch_trace(correlation_id) -> Trace \| NotReady` | 前 2 次 poll 回 NotReady，之後給假 trace | Langfuse，**兩條讀取策略依序嘗試**（見 §3.5） |
| `DiagnosisClient` | `diagnose(trace, gt_reasoning, judge_verdict \| None) -> dict` | 睡 2–4s，回 §8.2 的 JSON | §8.2 四段式 prompt + 輸出驗證 + span_index 越界剔除 |
| `SynthesisClient` | `synthesize(trace, question, agent_response) -> str` | 依假 trace 生出編號步驟 | §8.3 的 prompt，**與 judge/diagnosis 共用同一個 LLM client** |
| `WorkspaceClient` | `get_workspace()`、`get_version()` | 罐頭 config + 四個 skill 檔 | `GET {base}/get_workspace`、`GET {base}/get_config_version` |

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
放進 `/execute` 的 metadata，**agent server 用它當 Langfuse trace id**，事後平台用它反查 trace。

一次 agent 呼叫的完整 request body：

```json
{
  "message": "<題目文字>",
  "metadata": {
    "trace_data": {
      "trace_id":   "<correlation_id>",
      "session_id": "<correlation_id>",
      "user_id":    "<觸發者的 subject>",
      "tags":       ["eval_<eval set 名稱>"]
    },
    "workspace": {
      "config": { "agents": { "defaults": { "model": "…" } } },
      "skills": { "billing/SKILL.md": "…", "billing/references/refunds.md": "…" }
    }
  }
}
```

- `trace_id` 與 `session_id` **是同一個值**：每題都是自己的 correlation 單位，
  所以也是自己的 Langfuse session。
- `tags` 在 eval run 是 `["eval_<eval set 名稱>"]`，在 Playground 是 `["playground"]`。
- **`metadata.workspace` 只在 Playground 改過 agent 的 config 或 skill 檔時才出現**；
  eval run 的 request body 與 Stage 4 出現之前**完全相同**（連 key 都不會多）。
  兩半各自可省略：只改 config 就不送 `skills`，反之亦然。
- **兩半在 agent server 上的套用方式刻意不同**（完整契約見
  `docs/agent_server_stage4_endpoints.md`）：
  - `config` 是**稀疏 overlay**，deep merge 到 agent 自己的 `config.json` 上。
    非如此不可——平台拿到的快照已經把機密拿掉了，整份取代會讓 agent 沒有 API key。
  - `skills` 是**這次呼叫的完整檔案集**，整份取代 agent 的目錄。
    只有取代表達得出「把某個 reference 檔刪掉試試看」。
- 回應：`{"content": "<agent 的回答>"}`。client 對回應寬容——裸 JSON 字串或純文字都接受；
  空回答視為失敗（判一個空字串會產生毫無意義的 incorrect，反而蓋住真正的問題）。

> ⚠️ **這是 repo 外的相依**：agent server 必須讀 `metadata.trace_data.trace_id` 並用它當
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

**送出前會先問一次版本**（`GET {base}/get_config_version`）。與快照不同時跳對話框，
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
| `source_format` | text | `'csv' \| 'jsonl'`——**開發者實際上傳的檔案格式**。因為 CSV 在前端就被轉成 JSONL，後端 payload 恆為 JSONL，此欄是唯一保留原始格式的地方 |
| `metadata` | jsonb | 開發者自訂的 metadata key-value。**單一 JSONB 欄位**，未建 keys 表——key 量不大，「既有 key 自動帶出」以掃描 JSONB 支援。ORM 屬性叫 `meta`（`metadata` 在 declarative Base 上是保留字）|
| `version` | int | 樂觀鎖 |
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

**未建的表**：`skills`、`skill_versions`、`skillopt_runs`（Stage 3）；
`eval_set_metadata_keys`（改用單一 JSONB）；`playground_*`（刻意不落庫，§7）。

### 5.2 五個 Alembic migration

| Revision | 內容 |
|---|---|
| `0001_stage1_schema` | 上述 7 張表 |
| `0002_real_integration` | `question_results.agent_response` / `error_message` / `agent_latency_ms`、`runs.error_message`。假資料時代不需要，接真實服務後「看得到 eval 結果」少不了它們 |
| `0003_run_config` | `runs.name` / `runs.config` / `runs.secrets`——逐 run 設定（§4.7）|
| `0004_run_lifecycle` | `runs.cancel_requested`、`question_results.trace_error` / `diagnosis_error` |
| `0005_list_indexes` | 三個索引（見下）。**head 是這一個** |

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
| 使用者按下中止 | 見 §6.3 |

**其他執行控制**
- **timeout**：agent 呼叫包 `asyncio.wait_for`（`AGENT_TIMEOUT_S`），client 自身另有 httpx timeout。
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

五個 `question_*` 事件的 payload 相同：`question_pk / phase / verdict / status / error_message /
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
| 從三欄詳情把題目帶進 playground | **不做多輪對話**（agent 是 stateless，`/execute` 是單次呼叫）|
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

- **從哪來**：`WorkspaceClient` 一次讀完整份（`WORKSPACE_IMPL=real` 打
  `GET {base}/get_workspace`），或一份罐頭 workspace（`fake`；skill 目錄名對齊 seed 的
  skill tag，且 `billing` 帶一個 `references/` 檔，因為「skill 是一個目錄」正是舊模型表達不出
  的東西）。回傳四樣：`version` / `config`（已移除機密）/ `redacted_paths` / `skills`
  （扁平的 `{相對路徑: 檔案內容}`）。
- **讀不到要大聲**：讀失敗回 **503 + 原因**，絕不回一份空 workspace。
  > 「這個 agent 沒有 skill」與「你的 URL 錯了」長得一樣的話，開發者會默默地憑記憶重打一份 skill，
  > 然後測到錯的文字。空 workspace 本身是合法答案；**形狀不對**才是失敗。
- **機密**：agent server 自己的 API key 不會送過來，但**路徑會**（`redacted_paths`）。
  UI 因此把該欄位畫成「存在但隱藏、不可編輯」，而不是讓它憑空消失。
  > 消失的欄位會誘使人自己補一個同名 key，把真的金鑰蓋掉。
  > 前端另外會把落在這些路徑上的值從送出的 overlay 濾掉——萬一某個 agent server 是用
  > `"***"` 遮罩而不是刪除，這道防護會擋下「把真金鑰設成三個星號」。
- **override 怎麼傳**：`metadata.workspace = {config?, skills?}`（見 §3.3），
  config 稀疏 merge、skills 整份取代。
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

**它不是第六個外部依賴**：`SynthesisClient` 與 judge / diagnosis 共用同一個 LLM client 與同一套
JSON 修復流程，只多一個 `SYNTHESIS_MODEL`。

---

## 9. API 全表

互動式文件由執行中的 backend 提供：`/docs`、`/redoc`、`/openapi.json`。

**權限標記**：`R` = owner 或 viewer；`O` = 僅 owner；`—` = 只需登入身分；
`C` = 僅該 attempt 的建立者。

| 端點 | 權限 | 說明 |
|---|---|---|
| `GET /health` | — | |
| `GET /users` | — | 假使用者名單 + 目前身分 |
| `GET /me` | — | 目前 subject 與其在各 eval set 的角色。**UI 不用它 gate 權限**（§11.4）——每個 eval set 的 payload 自己就帶 `my_role` |
| `GET /run-config/defaults` | — | run config 對話框的預填值（env 來源）+ **六個 `*_IMPL` 現況** |
| `POST /eval-sets` | — | 建立（payload 恆為 JSONL + `source_format`）；建立者 = owner；可帶 `shares` |
| `GET /eval-sets` | — | 我有權限的卡片。分頁 + 篩選：`?limit&offset&q&metadata_key&metadata_value&sort`，回 `{items,total,has_more}` |
| `GET /eval-sets/metadata/keys` | — | 掃 JSONB 得既有 metadata key |
| `GET /eval-sets/{id}` | R | 單一卡片 |
| `PATCH /eval-sets/{id}` | O | 改 name / description / metadata（樂觀鎖 → 409）|
| `DELETE /eval-sets/{id}` | O | 刪整個 set（含所有 run / 結果 / 診斷）；底下有 running run → 409（先中止）|
| `PUT /eval-sets/{id}/roles` | O | **整批覆寫**分享名單（操作者本人永遠保留 owner）|
| `GET /eval-sets/{id}/questions` | R | 題目清單 |
| `PATCH /eval-sets/{id}/questions/{qpk}` | O | 改題（樂觀鎖 → 409；`question_id` 不變）|
| `POST /eval-sets/{id}/runs` | R | 觸發 run；body 帶 `name` / `config` / `secrets` / `reuse_secrets_from_run_id`，全部可省略 |
| `GET /eval-sets/{id}/runs` | R | run 列表（含 `incorrect_count` / `config` / `credentials_set` / `cancel_requested`）；分頁 `?limit&offset&q` |
| `GET /eval-sets/{id}/runs/{run_id}` | R | 單一 run |
| `POST /eval-sets/{id}/runs/{run_id}/cancel` | R\* | \*owner **或該 run 的觸發者**；非 running → 409 |
| `DELETE /eval-sets/{id}/runs/{run_id}` | O | running → 409（先中止）|
| `GET /eval-sets/{id}/runs/{run_id}/progress` | R | **SSE** 即時進度 |
| `GET /eval-sets/{id}/results` | R | 左欄題目清單；`?run_ids=..&mode=union\|intersection\|last_n&last_n=` |
| `GET /eval-sets/{id}/results/{rid}/trace` | R | 中+右欄：即時抓 trace（完整 body）+ 讀 DB 的診斷 |
| `POST /eval-sets/{id}/results/{rid}/re-diagnose` | O | 手動重診斷（避免 viewer 產生 LLM 成本）|
| `POST /eval-sets/from-shortlist` | — | 用 shortlist 的題目 + 複製既有 set 的題目建立新 set（§7.6）；讀不到的來源 set → 404（**寫入前**檢查）；沒有任何題目 → 422 |
| `GET /playground/workspace` | — | agent 的 config（已移除機密）+ 全部 skill 檔 + 版本；失敗 → **503 + 原因** |
| `GET /playground/workspace/version` | — | 只有版本字串，送出前的過期檢查用 |
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
  顯示某個 slot 有沒有值。**agent server 自己的金鑰也一樣**：`GET /playground/workspace` 只帶
  `redacted_paths`，不帶值。
- `GET /playground/attempts` 每筆回 `workspace_overridden` / `config_overrides`（點路徑陣列）/
  `edited_skill_files`（相對路徑陣列），讓清單一眼看出這次不是跑在 agent 自己的 workspace 上。
- `POST /eval-sets/from-shortlist` 回 `{id, question_count, duplicates_skipped}`。

---

## 10. 前端資訊架構

### 10.1 側邊欄的三個 section

```
側邊欄（可收合成圖示）
├─ Evaluation ← 目前所在
├─ Playground
└─ Optimize [Soon]   ← Stage 3 的位置，尚未實作

Evaluation（三層下鑽）                        Playground
├─ 首頁：eval set 卡片                        ├─ 編輯區（問題 + 四個面板，見下）
│   run 數、最近通過率、趨勢小折線、            ├─ phase stepper（Agent→Judge→Trace→Diagnosis）
│   regression 摘要數、成員數、齒輪            ├─ 三欄：attempt 清單 │ trace+診斷 │ span 細節
│                                          └─ Shortlist 對話框（§7.6）
├─ 中層：某 set 的 run 歷史
│   多選 run + union / intersection / last-N
└─ 底層：三欄詳情
    題目清單 │ trace + 診斷 │ span 細節
```

**Playground 的編輯區是一排對等的面板切換，一次只開一個**

`Agent config` · `Skill files` · `Expected answer & process` · `Endpoints & keys`

原本不是這樣：agent 自己的 `config.json` 藏在一顆叫 **Config** 的按鈕後面，而**這個平台**要打的
端點藏在一顆叫 **Settings** 的按鈕後面，兩排長得一模一樣的按鈕、兩個都叫某種 settings。
現在每顆按鈕都直說自己在編輯什麼，而且：

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
Stage 3 的 **SkillOpt 就是第三個 section**（`Optimize`），目前掛著 `Soon`、不可點，
只說明它將來做什麼；接上時把 registry 那筆的 `soon` 拿掉即可。

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
| **owner** | 全部 write（改題、改 metadata、改分享名單、刪 run / set、觸發 re-diagnose）+ 全部 read + 執行 eval | |
| **viewer** | 全部 read（含三欄錯誤診斷詳情）+ **執行 eval** + 中止自己觸發的 run | 改任何內容、刪 run / set、**觸發 re-diagnose**（避免 LLM 成本）|

- 一個 eval set 可指派多個 owner。
- 授權檢查做成**統一的 FastAPI 依賴**（`require_owner` / `require_reader`），不散在各 endpoint。
- **Playground 不在這個體系內**（§7.5）——它沒有 eval set。

### 11.2 登入是假的

**目前沒有真的 key-lock service。** 使用者身分來自 `X-User-Subject` header
（SSE 用 `?subject=` query，或設定檔預設值），可在 UI 右上角下拉切換，方便測試 owner/viewer 權限。
使用者名單來自 `GET /users`（設定檔 `KNOWN_USERS`，預設 alice / bob / carol / dave）。

> 原設計是「公司內部 key lock service 回傳的 token，授權以 token 的 identity(subject) 查核」。
> 換成真登入時，**只需要把 `current_subject` 這個依賴換掉**——其餘授權邏輯都建立在 subject 之上。

### 11.3 分享

- 上傳時可直接**輸入人名**指定分享對象（subject + role）。
- 每張卡片有 **config 齒輪**（僅 owner 見）：一個對話框可改 name / description / metadata **與分享名單**。
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
| `FAKE_USER_SUBJECT` | `alice` | 假登入的預設身分 |
| `KNOWN_USERS` | `["alice","bob","carol","dave"]` | `GET /users` 回傳的名單 |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | CORS 來源 |
| `ERROR_MESSAGE_MAX_CHARS` | `2000` | 落庫錯誤訊息的長度上限 |
| `SPAN_BODY_MAX_CHARS` | `800` | §4.4 單一 span body 截斷門檻（**只用於診斷 prompt**）|
| **`AGENT_IMPL`** / **`JUDGE_IMPL`** / **`TRACE_IMPL`** / **`DIAGNOSIS_IMPL`** / **`SYNTHESIS_IMPL`** / **`WORKSPACE_IMPL`** | 皆 `fake` | 每個 seam 各自 fake 或 real，**可逐一切換** |
| `AGENT_BASE_URL` | 空 | agent server base URL；client 打 `{base}/execute`、`{base}/get_workspace`、`{base}/get_config_version` |
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
| **0** | `WORKSPACE_IMPL=real` + `AGENT_BASE_URL` | Playground 的編輯器出現**真實的** config 與 skill 檔。**只讀、無副作用，風險最低** |
| 1 | `AGENT_IMPL=real` + `AGENT_BASE_URL` | 題目打得到真 agent；`agent_response` 有真實回答（判分仍是假的）|
| 2 | 加 `JUDGE_IMPL=real` + `LLM_BASE_URL` / `JUDGE_MODEL` | 通過率與 judge comment 開始有意義 |
| 3 | 加 `TRACE_IMPL=real` + `LANGFUSE_*` | 點錯題看得到真實 span（**前提是 agent server 已套用 correlation_id**）|
| 4 | 加 `DIAGNOSIS_IMPL=real` + `DIAGNOSIS_MODEL` | 診斷、caveat、可疑 span 都由真 LLM 產生 |
| 5 | 加 `SYNTHESIS_IMPL=real` + `SYNTHESIS_MODEL` | shortlist 的「Draft from trace」產出真實的步驟草稿 |
| 6 | agent server 支援 `metadata.workspace` | 注入的 skill 文字與 config 值出現在真實 trace 第一個 span 的 system message 裡 |

**前置檢查**：`make preflight` 會逐一 ping 設為 `real` 的 seam，回報每個 OK / FAIL 與原因。
> 設定打錯時，這比跑一次 eval 才發現快得多。

---

## 14. 測試與驗證現況（可信度地圖）

**這一節的目的是讓你知道能信到什麼程度。**

### 14.1 單元測試：230 個

`make test` 跑其中 **207 個**——**不需要 DB 也不需要網路**（外部呼叫一律以 `respx` mock，
LLM 路徑以 monkeypatch）。剩下 23 個（`test_pagination.py` 全部，加上 `test_shortlist.py`
建立 eval set 的那一半）需要一個真 Postgres，未設 `TEST_DATABASE_URL` 時自動 skip。

| 檔案 | 數量 | 涵蓋 |
|---|---|---|
| `test_agent_client.py` | 17 | request body 的 `message` + `metadata.trace_data`；`{"content": str}` 回應解析（含裸 JSON 字串與純文字 fallback）；空回答視為失敗；307 redirect 會被 follow（實測撞到過）；5xx raise vs 4xx 直接失敗；逐 run base URL / timeout 覆寫；**有 override 時 `metadata.workspace` 出現、沒有時整個 key 不存在**；只改一半時另一半整個省略（`config` 缺席 ≠ `config: null`）；`skills: {}` 會被送出（它的意思是「這次不要任何 skill」）|
| `test_langfuse_client.py` | 27 | 空頁 → NotReady；時間排序與重新編號（**含混用有／無小數秒的 ISO 時間**——字串比較會把 `…:00.500Z` 排在 `…:00Z` 前面；時間讀不懂的 span 仍然保留；同時間以 id 打破平手，使重讀不會重排）；observation 型別過濾；分頁；Basic auth；`usageDetails` 與舊版 `usage` 兩種 token 欄位；ERROR level 映射；401 / 連線失敗 → `TraceFetchError` 且訊息含 host 與狀態碼；**兩條讀取策略**（兩者映出的 span 完全相同、404 → NotReady、auto 命中第一條時不會多打第二條、第一條壞掉會 fallback、**全失敗時兩條的原因都在訊息裡**）|
| `test_judge_and_diagnosis.py` | 18 | verdict 正規化與非法值；門檻覆寫兩個方向；**§4.4 截斷保留所有 span**；越界 `span_index` 剔除；§8.2 四段 prompt 的順序；JSON 修復重試（成功與放棄各一）|
| `test_orchestrator.py` | 19 | agent 例外只讓該題失敗而 run 仍完成；agent 自報失敗保留原因；**judge 失敗不被當成 correct**；診斷失敗不影響 verdict 且原因落庫；trace store 出錯不讓題目失敗；非預期例外把 run 收成 failed 並送出 SSE 終止事件；重試上限；併發；第一次呼叫 agent 前所有 result 列已建好；中止前未開始的題目留 pending；**中止會放棄進行中的 agent 呼叫**；已判分的結果在中止後保留；五個事件依序送出且帶齊指紋欄位；**送去診斷的是 settle 過的 trace**（§6.1a——落庫的診斷沒有第二次機會）|
| `test_playground.py` | 50 | 四階段依序推進；**沒填期望答案 → judge 呼叫次數為 0**、**沒填期望流程 → diagnosis 呼叫次數為 0**；`judge_verdict=None` 時 prompt 第四塊說「未判分」且四塊順序不變；workspace override 傳到 agent；**編輯過的檔案是對著送出當下的快照算的**（沒有基準的話每個檔案都會被算成改過）；空 override 不送出；baseline 跟 agent server 要而不是信瀏覽器；agent 連不上時只損失摘要不損失 attempt；**override 的文字出現在假 trace 第一個 span 的 system message**、沒有 override 的 attempt 則乾淨；四種失敗政策；**中止放棄進行中的呼叫**（30s stub + 2s `wait_for` 斷言）；中止保留已拿到的答案；SSE 事件與指紋；store 上限淘汰最舊**但不淘汰還在跑的**；跨 subject 404；**金鑰不外流的值層級斷言**；五種 trace_state；檢視路徑不截斷；**最後一個 span 還在 ingest 時會等它**（§6.1a），診斷拿到的也是那份完整的，而 trace 本來就完整時只多一次確認讀取 |
| `test_run_config.py` | 19 | `build_seams` 空設定等同純環境變數行為；`*_IMPL` 仍是主開關；逐 run 值覆寫 env；空白欄位退回 env；judge 與 diagnosis 共用同一個 LLM client；`resolve()` 把留白寫死；金鑰沿用的端點配對規則；**金鑰不外流的值層級斷言**（序列化一個帶哨兵金鑰的 model，斷言哨兵不出現在 payload 任何位置——比檢查欄位名可靠）|
| `test_workspace_client.py` | 13 | 整份 workspace 讀取；**config 保持巢狀不被攤平**；空 workspace 合法 vs 形狀不對則失敗；沒有 version 仍可用（只是失去過期檢查）；skills 不是 `{路徑: 文字}` 時報錯並指名是哪一筆；4xx/5xx 帶狀態碼與 body；非 JSON body 不猜；transport 錯誤帶 host；版本端點沒有 version 時報錯（**回空字串會被讀成「沒變」而讓檢查失效**）|
| `test_shortlist.py` | 18（**12 個需 DB**）| synthesis：草稿來自 trace、**不寫回 attempt**、無 trace → 409、模型錯誤原文回傳、空草稿算失敗、別人的 attempt → 404。建立：只用 shortlist 建立；複製既有 set 的題目；**複製件拿到新的 question_id**；skill tag 一併複製；重複題目文字被跳過並計數；同文字時 shortlist 的版本勝出；**讀不到的 set 回 404 且什麼都沒建**；空的建立請求 → 422；建立者是 owner 且分享名單生效；**新 set 第一次被讀取就回報正確的 `my_role`**（owner / 被分享者 viewer / 兩條建立路徑都是——這正是前端 gate 權限用的欄位，§11.4）；沒有角色的人連讀都讀不到 |
| `test_trace_settle.py` | 14 | §6.1a 的 settle：最後才到的 span 會被等到；沒有成長就立刻結束（穩態只多一次請求）；成長時有上限；長度相同時採用較新的一份；**比較短的重讀、NotReady、確認讀取失敗一律不採用**；中止立刻停；`TRACE_SETTLE_MAX_READS=0` 回到舊行為；delay 真的有等；與 poll 的組合（NotReady → 出現 → settle）；**只有 settle 失敗時 `trace_error` 保持 None**（有 trace 就不該亮紅色 banner）|
| `test_run_lifecycle.py` | 11 | cancel 的權限矩陣（owner ✓ / 觸發者 ✓ / 其他 viewer ✗）；非 running → 409；跨 eval set → 404；delete 為 owner-only 且 running 時 409 |
| `test_results.py` | 8 | trace 檢視的狀態機。核心是**`pending` 的題目回 `not_started` 且對 trace store 發出零個請求**（用會記錄呼叫次數的 stub 斷言）|
| `test_deletion.py` | 5 | `delete_run` / `delete_eval_set` 的 DELETE **順序**（子表先於父表，特別是 `question_results` 必須早於 `questions`），以及一個「schema 新增子表卻忘了加進刪除順序」的守門測試 |
| `test_pagination.py` | 11（**需 DB**）| `limit`/`offset`/`total`/`has_more`；翻完所有頁**每張卡剛好出現一次**；只列出有權限的 set；搜尋與 metadata 篩選在 SQL 生效；趨勢受上限；regression 用最新兩個 run。**最重要的兩個是查詢數守門測試**：`GET /eval-sets` 與 `GET /runs` 在 `limit=1` 與 `limit=20` 時發出的查詢數必須**完全相同**——斷言時間會 flaky，斷言查詢數不會 |

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

**Langfuse 錯誤路徑**：用一個回傳真實 `Unknown table expression 'events'` 500 body 的 mock，
確認兩條策略都被嘗試、錯誤訊息含兩者、瀏覽器顯示白話說明且原始 SQL 收在可展開區塊。

### 14.3 ⚠️ 哪些**沒有**被證明（最重要的一段）

| 項目 | 狀態 |
|---|---|
| **Langfuse 讀取** | **已對接真環境**，真實 trace 讀得回來也渲染得出來。token 欄位兩種命名都處理過 |
| **agent server（`/execute`）** | ❌ **只用自建 mock 驗過**。證明不了貴方的 `/execute` 是否真的回 `{"content": str}`。client 刻意寫得寬容，但真接上去仍可能需要微調 |
| **LLM 端點（judge / diagnosis）** | ❌ **只用 mock 驗過**。證明不了貴方端點是否支援 `response_format: json_object`（被拒會自動退回，但仍未實測）|
| **agent workspace（`/get_workspace`、`/get_config_version`）** | ⚠️ **平台這一側只有 respx 單元測試**。agent server 那一側已依 `docs/agent_server_stage4_endpoints.md` 實作，但兩邊**尚未在本文件更新時完成對接驗證** |
| **`metadata.workspace`** | ⚠️ 同上。特別要在真環境確認的是 **config 是 deep merge 而不是整份取代**——取代會讓 agent 沒有 API key，而那是一個很晚才會發現的失敗 |
| **synthesis（`SYNTHESIS_IMPL=real`）** | ❌ **只用假層驗過**。真模型產出的顆粒度（會不會貼整段 SQL、會不會寫成十五步）需要真資料才知道，prompt 大概率要調 |
| **診斷品質本身** | ❌ **完全未知**。診斷準確度只能在真實資料上跑起來後觀察——而那正是決定要不要投入 Stage 2 的依據 |
| **`LLM_TIMEOUT_S` 的逐 run 版本** | ❌ 未做。`AGENT_TIMEOUT_S` 與 `LANGFUSE_TIMEOUT_S` 都能逐次調整，唯獨 LLM 的 timeout 仍是全域設定 |

---

## 15. 明確尚未做的

### 15.1 維持 Stage 2 / 3 邊界（刻意不做）

per-span 機率 / 熱點著色、人工重標 span、SkillOpt 自動優化、
整份 eval set 用候選 skill 重跑並驗證改善、skill 寫回 agent server、多租戶隔離
（多 agent server / 多 Langfuse project）、編輯的即時讀同步。

> 側邊欄上的 **`Optimize` 只是一個位置**：它不可點、標著 `Soon`，
> 存在的用意是讓資訊架構容得下 Stage 3，而不是暗示它做好了（§10.1）。
> 直接打 `#/optimize` 會拿到一頁說明「這裡將來會做什麼、現在該去哪裡」的佔位頁。

### 15.2 Stage 1 / 4 範圍內但確實還缺的

| 缺口 | 說明 |
|---|---|
| **Langfuse 只讀不寫** | verdict 應同時寫成 Langfuse Score（`source=API`），**尚未做**。目前 app DB 是唯一真相，Langfuse UI 上看不到本平台判的分數。eval set 也沒有寫進 Langfuse Dataset |
| **span tree 不重建** | Langfuse 回傳的 `parentObservationId` **完全未使用**，目前以**依 startTime 排序的平舖列表**呈現。樹狀結構留給 Stage 2 的熱點檢視 |
| **`LLM_TIMEOUT_S` 沒有逐 run 版本** | 補法很小：`RunConfig` 加欄位、defaults 加一行、往 client factory 傳進去、對話框加一格 |
| **run config 無法比對** | 唯讀檢視一次只能看一個 run；要並排 diff 兩個 run 的設定還得自己切換 |
| **真登入** | 目前是假登入（§11.2）。換掉一個依賴即可 |
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
| 7 | **重跑實驗需要 agent server 端的新能力**：per-request workspace override | ✅ **平台側已做**（Stage 4）。✅ **agent server 側也已依契約實作**，但兩邊尚未完成對接驗證（§14.3、§17）|
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

**完整契約寫在 [`docs/agent_server_stage4_endpoints.md`](agent_server_stage4_endpoints.md)**——
那份是寫給 agent server 實作者看的：每個端點的 input / output / 處理步驟 / curl 驗收清單，
不預設讀者知道這個平台的存在。以下只列需求與狀態。

| # | 需求 | 為什麼必要 | 狀態 |
|---|---|---|---|
| 1 | `POST /execute` 讀 `metadata.trace_data.trace_id`，**用它當 Langfuse trace id** | 沒有這一步，平台無從找回自己剛觸發的 trace，**整個錯誤定位功能失效** | ✅ 已實作 |
| 2 | `GET /get_workspace` → `{"version", "config", "redacted_paths", "skills"}`：`config.json` 移除機密後的內容 + 每個 skill 檔（`{相對路徑: 文字}`）+ 版本字串 | 讓 Playground 從**真實的** config 與 skill 檔開始編輯，而不是從空白 textarea | ✅ 已實作 |
| 3 | `GET /get_config_version` → `{"version"}`，與上面同一個字串 | 送出前的過期檢查（§4.10a）。單獨一個端點是因為它被問得頻繁得多 | ✅ 已實作 |
| 4 | `POST /execute` 讀 `metadata.workspace = {config?, skills?}`，**只影響這一次呼叫、不落磁碟、不影響其他 request** | Playground 的迭代沙盒（Stage 4）與 Stage 3 的重跑實驗都靠它 | ✅ 已實作 |
| 5 | skill 更新 API + 版本控制 / rollback | Stage 3 的「存回 agent server」 | 🔴 Stage 3，未規劃 |

**第 2–4 項都是加法**，不改動既有 `/execute` 契約：沒有 override 時 request body 與現在**完全相同**
（連 `workspace` 這個 key 都不會出現）。

**兩條規則撐著整個設計，也是最容易被「順手優化」掉的**（詳見那份契約的 §5.2 / §5.3）：

1. **傳入的 `config` 是 deep merge 到 agent 自己的 `config.json` 上，不是整份取代。**
   平台拿到的快照已經把機密刪掉了，整份取代會讓 agent 沒有 API key——而那是一個很晚才會發現的失敗。
2. **傳入的 `skills` 是整份取代。** 沒有取代就表達不出「把某個 reference 檔刪掉試試看」。

> 這兩者刻意不對稱。實作者若把它們「統一」，其中一個就會壞掉。

---

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
4. **完成 agent server 那一側的對接驗證**（§17 的 2–4 項已經實作，但兩邊還沒一起跑過）。
   優先確認的順序是：`get_workspace` 讀得回來 → 帶 `metadata.workspace` 送一題、
   **確認 agent 仍然叫得動 LLM**（這一項在驗 config 是 merge 而不是取代）→
   注入的文字出現在 trace 第一個 span 的 system message 裡。
5. **開 `SYNTHESIS_IMPL=real` 並調 prompt**（§8.3）。假層產出的顆粒度是設計出來的，
   真模型會不會貼整段 SQL、會不會寫成十五步，只有真資料知道。
6. **補 §15.2 的小缺口**時，優先考慮 **verdict 寫回 Langfuse Score**——
   那讓兩個系統的真相一致，成本也不高。

**如果你要修改程式碼，先讀這四段**：§4（設計決策的理由）、§6.2（失敗策略）、
§10.2（三個前端機制）、§14.3（哪些沒被證明）。
這四段涵蓋了絕大多數「看起來多餘、其實在防某個具體失敗」的程式碼。
