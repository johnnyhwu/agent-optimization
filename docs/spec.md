# Agent Evaluation + Trace 錯誤定位 + Skill Evolving 系統 — Spec 討論輸入文件

> 本文件用途：作為與 Claude chat 後續討論的輸入，目標是把下述系統擬定成一份詳細的
> system spec。文件自包含，讀者無需任何前文即可理解。以下內容包含 (1) 背景情境、
> (2) 想做的系統、(3) 對 Langfuse 能力邊界的查證結論、(4) 已找出的設計風險/bug、
> (5) 已定案的架構決策與推薦架構藍圖、(6) 待與 Claude chat 深入討論釐清的開放問題。

---

> ## ⚑ 目前狀態（2026-07，重要）
> **Stage 1 的 POC 已經實作完成並可端到端執行**。§1–§5 為原始背景與設計推理、§6 為分階段
> 藍圖與 Stage 1 定案、§7–§8 為驗收與開放問題，皆保留作為設計脈絡。**若你只想知道「現在到底
> 做了什麼」，請直接看新增的 §9「Stage 1 POC 實作現況（As-Built）」**——那一節自包含地描述已落地
> 的技術棧、DB schema、API、權限/分享、前端、以及與本設計文件的所有差異。
>
> 一句話總結 POC 性質：**真實的 React UI + 真實的 app-DB schema + 真實的 orchestration/權限/樂觀鎖
> 邏輯；但所有外部依賴（A2A agent、LLM judge、LLM 診斷、Langfuse 取 trace）皆以「假資料層」樁接
> （stub），並模擬真實延遲**。目的先證明 UI + 資料流 + schema，尚未串接任何真實外部系統。
> §6 各節中若與 §9 衝突，以 §9（實作現況）為準。

---

## 1. 背景情境

### 1.1 現有的 Agent
- 有一個 **stateless domain agent**：每次收到使用者問題都重新初始化，沒有跨 request 記憶。
- Agent 透過 **Google A2A(Agent-to-Agent)protocol server** host，任何符合 A2A protocol 的
  client 都能送 query 進來。
- Agent harness 是 **nanobot**(https://github.com/HKUDS/nanobot)。基於其 Python SDK，
  在 A2A server endpoint 上每次收到問題時重新初始化 agent，使其成為 stateless。
- Agent 的 reasoning 邏輯是：**tool-calling → tool-calling → … → response generation**。
- Agent 有多個開發者預寫好的 **skill**，每個 skill 對應一個 domain-specific 問題類型。
  使用者問到某領域時，agent 會先讀取對應 skill，再開始一連串 tool calling。

### 1.2 現有的 Observability
- Agent 在 A2A server 上執行時，**每個 trace 都存進 Langfuse**。
- 在 Langfuse 上，一個 trace 內有多個 span，每個 span 是一個 ChatOpenAI span
  (tool calling 或 response generation)。

### 1.3 現有的 Evaluation 做法與痛點
- 使用者自建了一個 external eval server：Langfuse 觸發 → server 把每個問題打進 agent →
  拿到 response → 用 LLM-as-judge → 得到 score → 回傳，於是 Langfuse 上能看到每題
  correct / incorrect。
- **痛點**：對於被判錯的題目，開發者必須花很多時間人工去翻該題背後的 trace，逐個 span
  檢查是哪個 span 出錯。這個過程很麻煩。開發者希望系統能**自動針對錯題分析 trace、
  定位錯誤 span、並給出錯誤原因**。

---

## 2. 想做的系統（功能願景）

### 2.1 上傳 Evaluation Set
- 開發者上傳一個 eval set，每題包含：
  - `question`
  - `ground_truth_response`
  - **`ground_truth_reasoning_process_description`**：一段**自然語言、粗粒度**的理想推理過程描述，
    例如「agent 讀取 xxx skill，然後透過 SQL tool 取得 data，最後根據 SQL result 產生 response，
    在 response 中描述 xxx」。
  - **該題所屬的 `skill`**（開發者標註）。
- 提供 reasoning description 的目的：讓系統能**自動**針對錯題分析 trace、定位錯誤 span，
  而開發者**不必**把完整的 reasoning ground truth 逐步 label 出來（那太花時間）。

### 2.2 觸發 Evaluation + 即時進度
- 開發者按下 eval → 介面出現**進度條**，即時呈現每題的 score / correct or incorrect。

### 2.3 錯題的 Trace 錯誤定位（核心價值）
- 開發者可立刻點擊 incorrect 的題目 → 介面呈現該題 agent inference 的 **trace**
  （從 Langfuse 抓）。
- Trace 上**每個 span 標出「出錯機率」**：由 LLM 基於 trace + ground-truth reasoning 判斷，
  機率越高用越顯眼的顏色標示（熱點）。
- 滑鼠移到 / 點擊某 span → 右側呈現該 node 的 **input context、output context、token
  information**（就是 Langfuse 上點一個 span 會看到的東西）以及最重要的
  **「為什麼他可能是錯的」**。
- 目的：讓開發者快速定位錯誤原因。

### 2.4 人工修正標註
- 開發者點開 span 看到錯誤原因後，若有錯，可**修改錯誤原因**。
- 甚至可以把這個 span **重新標為「正確」**，把另一個 span **標為「錯誤」並提供原因**。
- 目的：這些人工修正會成為後續 Skill Evolving 的高品質訊號。

### 2.5 Skill Evolving 分頁（自動優化 skill）
- 參考方法：**SkillOpt**（微軟提出的單一 agent skill 自動優化演算法。
  參考：https://datasciocean.com/paper-intro/skillopt/）。
- Skill Evolving 頁面顯示 agent server 上 agent 擁有的**所有 skill**。
- 開發者可點選一個或多個 eval set result → 系統自動**合併，只留下 unique question**。
- 每個 question 背後都有對應 trace；每個 question 上傳時都標了所屬 skill →
  頁面呈現多個 **skill section**，每個 section 下列出需要用到該 skill 的 question。
- 開發者點某個 skill section → 開始執行 **SkillOpt 演算法** → 得到一份新的 optimized skill →
  可自行把新 skill 存回 agent server。

### 2.6 Skill 實驗（re-run experiment）
- 系統要能讓開發者實驗改過的 skill：讓 agent **重新執行相同的 question**，看看 trace 有無不同。
- 若結果正確，可透過系統把 skill **存回 agent server**。

---

## 3. Langfuse 能力邊界（已查證 Langfuse open-source codebase 的結論）

> 分兩層：**資料/儲存層**（Langfuse 可支撐）與**分析/UI/優化層**（Langfuse 沒有，須自建）。

### 3.1 Langfuse 可直接當後端支撐（✅ 已確認可行）
- **抓完整 trace + span tree**：`GET /api/public/traces/{traceId}`、
  `GET /api/public/v2/observations?traceId=...`。每個 observation 回傳
  input / output / model / token 用量(usageDetails) / cost(costDetails) / latency /
  startTime / endTime / **parentObservationId** / level / statusMessage / metadata。
  → **可在外部 UI 完整重建 span tree，並顯示等同 Langfuse span detail 的所有資訊**
  （滿足 2.3 的資料來源）。
- **上傳 eval set**：Langfuse Dataset Item 有 `input`、`expectedOutput`，加上一個
  **完全自由的 `metadata` JSON**（無 schema 限制）。→ ground-truth response 放
  expectedOutput，**reasoning process description 與 skill tag 放 metadata**。
  可用 `POST /api/public/v2/datasets`、`POST /api/public/dataset-items` 程式化上傳。
- **把每題連到它的 trace**：Dataset Run Item 用 `traceId`(+`observationId`) 連結
  dataset item 與一次執行的 trace（`POST /api/public/dataset-run-items`）。
- **對單一 span 標記正確/錯誤 + 原因**：Scores API `POST /api/public/scores` 支援
  `observationId` + `comment` + `metadata` + `source`(API / ANNOTATION)。
  → LLM 自動判斷用 `source=API`、人工修正用 `source=ANNOTATION`，靠 score id upsert 做編輯。
  讀回用 `GET /api/public/v3/scores?observationId=...`。
- **內建 LLM-as-judge**（可選用）：judge model 可指向任何 LLM connection，含**自訂 `baseURL`
  的 self-hosted 端點**。使用者可續用自建 judge，或改用 Langfuse 內建。

### 3.2 Langfuse 沒有、必須自建（❌）
- 「分析錯題 → 定位錯誤 span → 算出每個 span 出錯機率 → 熱點著色」的**分析引擎**。
- 想要的那套**客製 UI**：即時進度條、機率熱點著色的 trace 檢視、可編輯標註、重新標記 span。
  Langfuse 的 trace UI 無法疊加自訂的 per-span 機率、也無法改成這種互動。
- **Skill Evolving / SkillOpt** 整套（合併去重、依 skill 分組、跑優化、存回 agent server）。
- 改 skill 重跑實驗、skill 版本管理、寫回 A2A server。
- ⚠️ Langfuse **沒有**「trace 完成 → 主動 webhook 通知外部 eval server」的原生觸發機制。
  現有「Langfuse 觸發 → 打自建 server」那條路其實是使用者自己接的 glue。
  → 新系統的 orchestration（讀 dataset → 打 agent → 判分 → 寫回 score/run）**應由新平台主導**，
  不要依賴 Langfuse 觸發。

### 3.3 結論
**做一個獨立系統，把 Langfuse 當 trace/score 的資料骨幹，透過 public API 讀 trace、寫 score。
不要試圖把整套塞進 Langfuse（也不建議 fork Langfuse UI）。**

---

## 4. 設計上的風險 / 待釐清的 bug（按嚴重程度）

1. **【最關鍵】question ↔ trace 的關聯**：eval 系統打 A2A agent 後，如何得知該題對應 Langfuse
   上哪一條 trace？必須有 correlation 機制（見 §5 已定案）。這是 2.3 錯誤定位的前提。
2. **Langfuse ingestion 是非同步**：agent 回應後 trace 不一定馬上可查（批次 flush + 佇列進
   ClickHouse）。「點 incorrect 馬上看 trace」須處理最終一致性 / 重試；agent 端也要確保 flush。
3. **粗粒度自然語言 reasoning ↔ 具體 span tree 的對齊是模糊問題**（整個定位功能的核心風險）：
   - 多條有效路徑：agent 走了不同但同樣正確的路徑 → 可能被誤判某 span 錯。
   - 粒度不匹配：ground truth 說「用 SQL tool 取資料」，trace 有多次 tool call / 重試 → 對應模糊。
   - 錯誤不一定能歸到單一 span（compounding / emergent error）→「找出那個錯 span」框架會失效。
   - **「每個 span 出錯機率」的定義需先定案**：各 span 獨立機率，還是跨 span 加總為 1
     （假設剛好一個元凶）？兩者的演算法與 UI 完全不同。
4. **correct/incorrect 的判準**：LLM-as-judge 可能給連續分數或「部分正確」，二元化門檻要定義。
5. **skill-selection 錯誤這一類沒被涵蓋**：題目上傳時人工標了「該用 skill X」，但 agent 可能
   **讀錯 skill**（常見 bug）。若錯在選錯 skill，錯誤歸因與 SkillOpt 的優化對象都會指錯。
6. **SkillOpt 施力點假設過強**：假設「錯 → 優化 skill 就能修」，但錯誤可能在 SQL tool、
   base model 或 skill 以外，優化 skill 無效。需先判斷錯誤是否落在該 skill 可控範圍。
   （SkillOpt 需要 correct + incorrect 對比樣本，「合併保留 unique question」方向正確。）
7. **重跑實驗隱含 agent-server 端新需求**：要在不存回 server 的情況下測改過的 skill，A2A agent
   必須支援 **per-request skill override**（臨時注入 skill）。現在 skill 從磁碟讀，設計未提及。
8. **非決定性讓「trace 是否不同」不可靠**：LLM 有溫度，重跑幾乎必然有差異。應以 score / outcome
   比較（甚至重跑 N 次取多數）判斷改善，而非比對 raw trace diff。
9. **「存回 agent server」是新平台對 A2A server 的寫入耦合**：需 agent server 提供 skill 更新
   API + 版本控制 / rollback。完全在 Langfuse 之外。
10. **成本 / 規模**：把整條 trace（可能含大 context 的 input/output）餵給 LLM 做錯誤定位，
    token 成本可能很高，且每次編輯會重跑。需截斷 / 摘要 / 快取策略。

---

## 5. 已定案的架構決策

- **落地姿態**：**獨立 app + 自有 DB**，把 Langfuse 當 trace/score/dataset 的資料骨幹，
  透過 public API 讀 trace、寫 score。（不 fork Langfuse。）
- **question ↔ trace 關聯**：**correlation id 注入**——eval 平台對每題產生 correlation_id 放進
  A2A metadata，agent 端把它套用到 Langfuse trace（deterministic trace id 或 trace metadata），
  事後用它反查 trace。

---

## 6. 推薦架構藍圖

### 6.1 系統拓樸
```
[Eval Platform (新建, 獨立 app)]
   ├── 自有 DB (Postgres): skills, skill_versions, eval_sets, eval_runs,
   │                        question_results, span_analyses, skillopt_runs
   ├── Orchestrator:            讀 eval set → 逐題打 A2A agent → LLM-as-judge 判分
   ├── Error-Localization Engine: 抓 trace → LLM 依 ground-truth reasoning 算 per-span 機率
   ├── SkillOpt Engine:         合併去重題目 → 依 skill 分組 → 跑優化 → 產生新 skill
   └── Frontend:                進度條 / 熱點 trace 檢視 / 可編輯標註 / Skill Evolving 分頁
        │  (讀 trace、寫 score 都走 Langfuse public API)
        ▼
   [Langfuse]  ← trace/span store + dataset store + score store
        ▲
   [A2A Agent Server (nanobot)]  ── 每次 request 送 trace 進 Langfuse，並套用 correlation_id
```

### 6.2 Correlation 機制（前提，已定案）
- Eval Platform 對每題產生 `correlation_id`，放進 A2A request 的 metadata。
- Agent 端（A2A server wrapper）初始化 Langfuse trace 時，用 correlation_id 當
  **deterministic trace id**（或寫進 trace metadata `eval_correlation_id`）。
- Eval Platform 事後用 `GET /api/public/v2/observations?traceId={correlation_id}`
  （或 metadata filter）抓回整條 trace。
- **需 agent server 端小幅改動**：接受 metadata correlation_id 並套用到 trace。
- **處理 ingestion 非同步**：抓 trace 前先 poll / 重試（exponential backoff），或要求 agent 端
  回應前 flush 並回傳 trace 就緒訊號。

### 6.3 資料落點對應
| 概念 | 存哪 |
|---|---|
| eval set（question / ground-truth response / reasoning desc / skill tag） | Langfuse Dataset Item：`input` / `expectedOutput` / `metadata`(reasoning + skill)；app DB 存索引 |
| 每次 eval run 的 trace 連結 | Langfuse Dataset Run Item(traceId)，或 app DB 直接存 correlation_id ↔ trace |
| 題目 correct/incorrect 分數 | Langfuse Score（trace 層，`source=API`） |
| LLM 自動判斷的 per-span 出錯機率 + 原因 + **caveat** | **app DB `span_analyses`**（機率與 caveat 都是 app 專屬概念）；可選擇同時把最終判定寫成 Langfuse observation-level score。**caveat 必須落庫**（見 §6.8），供 Stage 3 SkillOpt 判斷是否納入樣本，避免屆時重跑診斷 |
| 人工修正（重標 span、改原因） | Langfuse Score（observation 層，`source=ANNOTATION`，comment=原因）+ app DB 留一份供 SkillOpt |
| skills / skill 版本 / SkillOpt run | **app DB**（Langfuse 無此概念） |

### 6.4 Error-Localization Engine 設計要點（對應 §4 風險）
- **per-span 機率定義**：建議「各 span 獨立的 error-likelihood(0~1)」而非加總為 1，以容納
  compounding error（多個 span 都可能有問題）。
- **對齊策略**：把 ground-truth reasoning description 拆成 steps，先做 step ↔ span 的軟對齊，
  再逐 span 問 LLM「相對於期望的這一步，這個 span 是否偏離、偏離原因」。
- **涵蓋 skill-selection 錯誤**：額外比對「agent 實際讀的 skill」vs「題目標註的 skill」，
  不一致時把『讀 skill 的那個 span』標為高機率錯誤來源。
- **成本控制**：大 input/output 先摘要或截斷再餵給定位 LLM；分析結果快取，編輯時只重算受影響 span。

### 6.5 SkillOpt / 實驗 設計要點
- SkillOpt 吃 (question, trace, correct/incorrect, error reason, current skill) 的
  **correct + incorrect 對比樣本**，只針對「錯誤確實落在該 skill 可控範圍」的題目。
- **caveat 題目預設排除**：Stage 1 診斷若對某題輸出 caveat（懷疑錯不在單一 span，或不在 skill
  可控範圍），該題**預設不自動納入** SkillOpt 樣本，標為「需人工確認」。理由即 §4.6——SkillOpt
  假設「改 skill 能修」，而 caveat 恰恰在說「這題改 skill 也修不好」，硬納入只會污染優化樣本。
  開發者可手動覆寫、確認納入。
- **重跑實驗**：agent server 需支援 **per-request skill override**（臨時注入候選 skill，不落磁碟）；
  以 **score / outcome**（可重跑 N 次取多數）判斷改善，而非比對 raw trace diff。
- **存回 agent server**：需 agent server 提供 skill 更新 API + 版本控制 / rollback。

---

## 6.6 分階段交付策略（討論後定案）

> 核心判斷：整個系統的價值集中在 2.3（錯誤定位）與 2.5（SkillOpt），而兩者都建立在同一個
> 脆弱假設上——「錯誤可歸因到單一 span，且該錯誤可靠改 skill 修好」。§4.3/§4.5/§4.6 已指出此
> 假設的裂縫（compounding/emergent error、錯不在 skill 而在 tool/base model/資料）。因此**不要
> 一次做完 §2.1–2.6**，改採三階段交付，把不確定性高的功能後推並明確標示為輔助建議而非結論。

- **Stage 1（一定 work、開發者立刻有感）**：只做「錯題自動抓 trace → LLM 白話診斷可疑 span →
  跳轉該 span 顯示 input/output/token」。解決 §1.3 痛點的約 80% 價值，不需機率/熱點/SkillOpt。
- **Stage 2（實驗性）**：per-span 機率熱點、reasoning↔span 的 step 拆解軟對齊、可編輯標註/重標
  span。明確標示為「輔助線索」，其準確度先在真實資料上觀察再決定投入深度。
- **Stage 3（最可能爛尾、依賴最多外部能力）**：SkillOpt 自動優化 + per-request skill override
  重跑 + 寫回 agent server。依賴 agent server 端尚未具備的能力（§4.7、§4.9），且 §4.8 非決定性
  讓「驗證改善」本身不可靠。列為最後、風險最高的階段。

## 6.7 Stage 1 詳細定案

**Stage 1 範圍（刻意最小化）**
- 唯一功能：開發者點某道 incorrect 題目 → 系統用 correlation 抓回該題 trace → LLM 依
  ground-truth reasoning 對整條（截斷後）trace 做**粗粒度**診斷 → UI 跳到可疑 span，右側顯示
  該 span 的 input/output/token（等同 Langfuse span detail）。
- **明確不做**：per-span 機率、熱點著色、人工重標、SkillOpt、重跑候選 skill、寫回 agent server。
- **orchestrator 由新平台自己做**（討論後修正）：Stage 1 的 eval run 由新平台 orchestrator 主動
  打 A2A agent → 拿 response → 執行 judge → 寫 `question_results`，**不再依賴舊 glue（§3.2）**。
  詳見 §6.14。
- **judge 視為黑盒 sub-component**：Stage 1 把 LLM-as-judge 當成一個獨立子元件（輸入
  response + ground truth，輸出 verdict），其 prompt、連續分→二元化門檻等細節**留待之後詳細
  實作**，先不阻塞 Stage 1 其他部分。

**Stage 1 各項決策**
- **Correlation（前置阻斷項，已確認可行）**：agent server 傳過去的 payload 本就含一個 metadata
  欄位，直接在其中新增一個 `trace_id`（correlation_id）key；事後用它反查 trace。使用者確認此項
  可實作，無 blocker。
- **Langfuse ingestion 非同步處理**：開發者點題當下 trace 不一定已進 ClickHouse（eval 剛跑完
  立刻點尤其會踩到）。→ 抓 trace 前先 **poll + exponential backoff**；UI 上必須把「trace 生成中
  （重試中）」與「真的沒有 trace」明確區分，避免開發者誤以為系統壞了。
- **診斷品質＝提供線索，非斬釘截鐵**：Stage 1 刻意**不給機率**，且輸出用**不確定語氣**——
  「最可疑的是 span 4，因為其 SQL 結果少了 X 欄位；但也可能上游 span 2 的檢索就漏了」，而非
  硬指單一 span。目的是讓開發者把它當「有用的線索、理解可能犯錯方向」，而非被精確假象誤導。
  精確度待系統實際跑起來、看到成效後再微調。
- **粗粒度對齊（最低限度用法）**：Stage 1 **不做** §6.4 的 step 拆解軟對齊；直接把整段
  `ground_truth_reasoning_process_description` 連同整條（截斷後）trace 丟給 LLM，問「相對於此
  期望流程，trace 哪裡偏離」。粗但夠用，並能在真實資料上看清對齊到底多不準——此觀察是決定
  是否投入 Stage 2 機率熱點的關鍵依據。
- **截斷策略（重要：砍 body 不砍 span）**：**保留所有 span**（絕不按 span 數截斷）。理由是
  根因常在前面、但讓錯誤「現形」的症狀證據常在後面（例：span 2 檢索漏欄位，單看正常，直到
  span 7 產生空結果才現形）；砍掉後段 span 會砍掉診斷證據。→ 只對**單一 span 內部超長的
  input/output body** 做截斷（保留頭尾、中間省略）或摘要，span 骨架（序號、tool 名、status、
  input/output 頭尾）全保留。因本情境 span 數通常不多，多數情況只需處理極端長的單一 span body。
- **成本/快取**：診斷在 eval 當下生成一次並落庫，之後點開直接讀 DB，不重生成（詳見 §6.12）。

## 6.8 Caveat 作為跨階段訊號（討論後定案）
LLM 診斷輸出的 `caveat` 欄位（懷疑錯不在單一 span、或不在 skill 可控範圍）不只是 UI 標記，而是
**貫穿三階段的訊號**：
- **Stage 1**：UI 顯眼標出（放在 `overall_diagnosis` 旁、trace 檢視頂部，不埋進單一 span 細節），
  告訴開發者「這題可能無法定位到單一 span / 錯不在 skill」，等於直接說「別浪費時間在這條 trace
  上找 skill 的錯」。
- **Stage 3（SkillOpt）**：有 caveat 的題目**預設不自動納入** SkillOpt 樣本，標為「需人工確認」
  （理由見 §6.5）。
- **落庫要求**：caveat 必須在 Stage 1 診斷生成當下就存進 app DB（`span_analyses` 或 run-level
  診斷紀錄），不能只即時吐出用完即丟；否則 Stage 3 得重跑一次診斷才知道哪些題有 caveat。這是
  「Stage 1 少做功能，但資料結構別做窄」的具體落點。

## 6.9 Stage 1 LLM Input / Output 契約（定案）

**Input（組給診斷 LLM，順序固定四塊）**
1. **任務框架 + 語氣約束（system）**：明確要求「你在提供線索、不是下判決；不確定就說不確定；
   可指出多個可疑點」。此約束是把「不確定語氣」從仰賴 LLM 自覺變成**硬約束**的關鍵——不寫進
   prompt，模型多半會斬釘截鐵指單一 span。
2. **期望流程**：整段 `ground_truth_reasoning_process_description`。
3. **實際 trace（截斷後）**：span 陣列，每個 span 帶 `index / tool_name / status / input /
   output`（input/output 已按 §6.7「砍 body 不砍 span」規則截斷）。**`index` 必給**，output 要
   靠它指回 span。
4. **judge 判錯結果**：把 judge 判「此題錯」以及（若有）它的 comment 一併給。讓 LLM 知道「最終
   答案錯在哪」能大幅收斂搜尋方向；幾乎免費且有效。

**Output（強制 JSON，不接受自由散文）**
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
設計理由：
- **`suspects` 是允許多個的陣列**：在資料結構層直接容納「不確定 / 多個可疑點」，不逼 LLM 選一
  個；第一名排最前，前端預設跳它。
- **`confidence` 用 high/medium/low 三檔文字，不用數字百分比**：Stage 1 刻意界線。完全不給強弱，
  前端只能平鋪、開發者不知先看哪個；三檔文字給了排序線索又不製造「73%」的假精確。Stage 2 要做
  機率熱點時再升級為連續值。
- **`caveat` 是逃生口**：對應 §4.3/§4.6，讓 LLM 有地方講「錯不在單一 span / 不在 skill」，而非
  被 schema 逼著硬指一個 span。

## 6.10 上傳 / 新增 Eval Set 介面（定案）

- **每張 eval set card 的欄位**：名稱、日期、對應上傳檔案，外加**開發者可自訂的 metadata key**。
- **自訂 metadata key 的兩個作用**：
  1. 下次新增 card 時，**既有 key 自動帶出**讓開發者填寫（故 key 是**跨 set 共享的 schema 層級
     概念**，非每張 card 各自為政）。→ app DB 需一張 `eval_set_metadata_keys` 記所有出現過的 key；
     card 本身存 key-value。
  2. 首頁可依這些 key **篩選要顯示的 card** 或**決定排序鍵**（預設日期排序）。

> **（實作現況，見 §9）**：POC **未建 `eval_set_metadata_keys` 表**——metadata 存在 `eval_sets.metadata`
> 這個單一 JSONB 欄位裡，「既有 key 自動帶出」改由 `GET /eval-sets/metadata/keys` **掃描 JSONB** 提供。
> 「依 metadata 篩選 / 排序」尚未實作（留待後續）。**新增功能**：上傳時 owner 可**直接輸入人名**指定要把
> 此 eval set **分享**給誰（見 §6.16 與 §9 的分享說明）。

## 6.11 上傳資料結構契約（CSV / JSONL，定案）

支援 **CSV** 與 **JSONL** 兩種上傳格式。CSV 若含長 reasoning /
逗號 / 換行，須用標準 CSV quoting。欄位：

> **（實作現況，見 §9）**：**CSV 與 JSONL 皆已實作**。JSONL 為每行一個 JSON 物件（欄位名同下表，
> `skill` 為 `list[str]`）；CSV 為首列表頭、欄名同下表，`skill` 儲存格可為 JSON 陣列字面值或以
> `,`/`;`/`|` 分隔的字串。兩者都在**前端**解析為可編輯表格（§9.8b），送出前一律序列化為 JSONL，
> 故**後端只有一條 JSONL 寫入路徑**；開發者實際上傳的格式記在 `eval_sets.source_format`。
> 下面關於 `question_id`、題目鎖定、改題快照的規則**皆已如實作**。

| 欄位 | 必填 | 說明 |
|---|---|---|
| `question` | ✅ | 題目 |
| `ground_truth_response` | ✅ | 理想答案 → Langfuse `expectedOutput` |
| `ground_truth_reasoning_process_description` | ✅ | 粗粒度自然語言理想流程，Stage 1 診斷對齊依據 |
| `skill` | ✅ | **list of str**：該題所屬 skill。目前實作只處理第一個，但型別先設為陣列，未來支援一題多 skill 不必改 schema |
| `question_id` | 選填 | 跨 run 穩定的題目 id；見下 |

- **`question_id` 決策（定案）**：選填。**未提供時由系統在上傳當下生成一個 immutable id**（生成後
  即與 question 文字脫鉤、永不重算——**不採 content hash**，因為改文字會使 hash 變動而破壞跨 run
  對齊）。開發者日後從本系統把 eval set **下載回去**會看到系統指派的 question_id。
- **Eval set 內題目鎖定規則（定案，讓對齊極單純）**：一個 eval set 建立後，**不可新增或刪除題目**，
  且題目的任何內容（question / ground_truth_response / reasoning）**只能透過系統介面修改**——藉此保證
  同一 set 下**所有 run 永遠基於同一份固定的 question_id 集合**，cross-run 對齊不會有對不上的情況。
  介面修改時 question_id 保持不變。開發者若要增刪題目，請**另建一個新的 eval set**。
- **改題後舊 run = 歷史快照（定案）**：透過介面改題只影響**之後**的新 run；既有 run 的
  `question_results` 與 `span_analyses` 保持原樣（反映它執行當下、改題前的 ground truth 真相），
  不標 stale、不重算。符合「run 是一次歷史執行」的語意。
- **為何 `question_id` 是 Stage 1 就要定的地基**：§6.13 的三種 incorrect mode 與 card 上的 regression
  明細全都依賴「同一題跨 run 可對齊」。上述鎖定規則正是保證此對齊成立的機制。

## 6.12 診斷生成與快取流程（定案，修正版）

- **生成時機前移到 eval 當下**：對錯題的診斷在**該 run 執行、evaluation 判分完成的當下**就生成，
  存進 app DB。開發者事後點開該題**直接讀 DB**，即時、不重跑 LLM（省時省錢）。
- **必須掛在 trace ready 之後**：因 §4.2 Langfuse ingestion 非同步，eval 當下 agent 剛回應、trace
  可能還沒進 ClickHouse。故流程為 **判分 → poll/backoff 等 trace ready → 生成診斷 → 存 DB**；
  「生成診斷」不可與判分同時發，否則會偶發性存進空診斷。
- **Stage 1 的重算觸發只有一個**：開發者手動點「重新診斷」。trace 是 immutable 的一次執行，
  自動重算（trace 變了）Stage 1 幾乎不會發生，先不做。（人工重標 span 是 Stage 2。）故 Stage 1
  快取邏輯極簡：**生成一次、存著、只有手動才重算**。

## 6.13 Stage 1 前端三層架構（定案）

開發者長期使用、會累積很多 eval set、每個 set 又跑過很多 run，故 UI 是**三層下鑽**，非單一頁面：

**最上層 — Eval Set 清單（首頁）**
- 每個 set 一張 card。card 上顯示**近期 run 資訊與趨勢**：已跑過幾個 run、最近一次通過率、通過率
  趨勢（小折線）。Stage 1 即可做（聚合查詢）。
- regression 摘要（幾題退步 / 進步）：card 上先只顯示**摘要數字**（例「⚠ 2 題退步」），逐題明細
  留到下一層，避免 card 資訊爆量。
- 依 §6.10 的自訂 metadata key 篩選 / 排序（預設日期）。

**中層 — 某 Eval Set 的 Run 歷史**
- 點進一個 set → 列出**歷次 run**（時間、整體通過率、錯幾題）。
- **跨 run 對比 / regression 明細**（哪題從對變錯、從錯變對）放在這一層；依賴 §6.11 的穩定
  question_id。Stage 1 可先做基本 run 列表 + 各自通過率，逐題 diff 標為 Stage 1.5，但**資訊架構
  先留好位置**。
- **多選 run**：開發者可只點一個 run（進入下層看該次），也可**一次選多個 run**，差別只在最底層
  左欄「哪些題算 incorrect」的判定 mode（見下）。

**底層 — 某次（或多次）Run 的三欄詳情**
- 三欄：**左＝題目清單**（correct/incorrect 分色、可篩「只看錯的」）｜**中＝trace 全貌**（垂直
  span 列表 + 頂部 `overall_diagnosis` 與 caveat，suspect span 加標記與 confidence 小標；點題自動
  選中第一名 suspect）｜**右＝span 細節**（上半 = 等同 Langfuse span detail 的 input/output/token；
  下半 = 該 span 的診斷 reason + evidence，非 suspect 則顯示「此步驟未被標為可疑」）。
- **多選 run 時的三種 incorrect 判定 mode**（只影響**左欄哪些題被列為 incorrect**，不影響三欄
  其餘部分）：
  - **Mode A 寬鬆 / union**：任一 run 錯即算錯 → 全面盤點「曾經出過問題」的題。
  - **Mode B 嚴格 / intersection**：所有選中 run 都錯才算錯 → 找「頑固、穩定會錯」的題，**這批
    最該投 SkillOpt**。
  - **Mode C 近 N 個 run**：過去 N 個 run 錯才算 → 找「最近才開始錯（regression）」的題。
  - 三個 mode 全依賴 §6.11 的穩定 question_id。
- **麵包屑 + 快速返回（Stage 1 必做，非 nice-to-have）**：三欄詳情內要能一鍵回到 run、回到 set。
  開發者一天查十題，每次從頭點會崩潰。

## 6.14 App DB Schema（Stage 1 定案）

原則（呼應 §6.3）：**Langfuse 是 trace/span/score 的真相來源，app DB 不複製 trace 內容**。app DB
只存 Langfuse 沒有的概念 + 指回 Langfuse 的索引（correlation_id）。span 的 input/output/token 全文
於檢視時即時向 Langfuse API 抓，不落 app DB。

**1. `eval_sets`**
```
id            uuid pk
name          text
description   text null
source_format text            -- 'csv' | 'jsonl'
metadata      jsonb           -- 自訂 metadata key-value（Stage 1 單表 JSONB，key 量不大）
version       int             -- 樂觀鎖（改名稱/描述/metadata 用）
created_at    timestamptz
updated_at    timestamptz
```
> metadata 決定用**單一 JSONB 欄位**（非 keys+values 兩張表）——Stage 1 key 量不大。首頁「既有 key
> 自動帶出 / 依 key 篩選排序」以掃描 JSONB 的方式支援；value 型別 Stage 1 先全當字串排序，數字/
> 日期排序留待 Stage 1.5。

**2. `questions`**（stable question_id 的家）
```
id                uuid pk         -- 內部 pk（改文字不變，內部關聯不斷）
eval_set_id       uuid fk -> eval_sets
question_id       text            -- §6.11 上傳時生成、immutable、使用者可見/可下載
question          text
ground_truth_response  text
ground_truth_reasoning text       -- reasoning_process_description
version           int             -- 樂觀鎖（改題用，衝突粒度 = 單題）
created_at        timestamptz
unique (eval_set_id, question_id)
```

**3. `question_skills`**（skill = list of str）
```
question_pk  uuid fk -> questions.id
skill_name   text
ordinal      int             -- 保留順序；Stage 1 只用 ordinal=0
pk (question_pk, ordinal)
```

**4. `runs`**（一次 eval 執行）
```
id             uuid pk
eval_set_id    uuid fk -> eval_sets
triggered_by   text            -- token subject（誰觸發，owner 或 viewer 皆可）
status         text            -- 'running' | 'completed' | 'failed'
started_at     timestamptz
completed_at   timestamptz null
pass_rate      numeric null    -- 完成時算好存著，首頁 card 趨勢直接讀，不每次聚合
total_count    int null
correct_count  int null
```

**5. `question_results`**（regression 對齊核心）
```
id             uuid pk
run_id         uuid fk -> runs
question_pk    uuid fk -> questions.id
correlation_id text            -- = 注入 agent metadata 的 trace_id，指回 Langfuse（不存 trace 本身）
verdict        text null       -- 'correct' | 'incorrect'（未判分前 null）
judge_score    numeric null
judge_comment  text null
status         text            -- 'pending' | 'done' | 'failed'（容許 run 部分完成）
trace_ready    bool            -- §6.12：trace 是否已確認可查
created_at     timestamptz
unique (run_id, question_pk)
```
> `(question_pk, verdict)` 跨多個 run join → 算出 regression（哪題從 correct 變 incorrect）。
> `status` 讓某題 agent timeout / judge 失敗 / trace 一直 not ready 時，run 不整個卡死。

**6. `span_analyses`**（診斷結果，帶 caveat）
```
id                  uuid pk
question_result_id  uuid fk -> question_results
overall_diagnosis   text
caveat              text null       -- §6.8 跨階段訊號；獨立成欄（非埋 JSONB）便於 Stage 3 篩選
raw_llm_output      jsonb           -- 完整診斷 JSON，含 suspects[]；Stage 1 整包存、不拆子表
generated_at        timestamptz
model_used          text            -- 記生成用的 model，日後微調精確度要用
unique (question_result_id)
```
> `suspects[]` Stage 1 整包存進 JSONB（UI 讀整包 render，無跨題查 span 需求）；Stage 2 要做機率
> 熱點 / 跨題聚合可疑 span 時再拆 `span_suspects` 子表。現在拆是過度設計。

**7. `eval_set_roles`**（權限，見 §6.16）
```
eval_set_id  uuid fk -> eval_sets
user_subject text            -- 來自登入 token 的 identity
role         text            -- 'owner' | 'viewer'
pk (eval_set_id, user_subject)
```

> **Stage 1 不建**：`skills`（skill 目前只是 question 上的字串 tag）、`skill_versions`、
> `skillopt_runs`——留待 Stage 3。

## 6.15 Orchestrator 流程（Stage 1 定案）

新平台自己的 orchestrator 執行一次 eval run，每題流程：
```
讀 question（run 開始時讀定一份 question 快照，不每題現讀）
  → 生成 correlation_id → 打 A2A agent（metadata 帶 correlation_id）
  → 拿 response → 送 judge（黑盒 sub-component）→ 得 verdict / score
  → 寫 question_results（verdict, correlation_id, status=done or failed, trace_ready=false）
  → poll / backoff 等 Langfuse trace ready → 標 trace_ready=true
  → 若 incorrect：抓 trace（§6.7 截斷）→ 生成診斷 → 寫 span_analyses（含 caveat）
  → 更新進度（即時推送到前端進度條）
```
- **run 開始時讀定 question 快照**：多 owner 下，A 正在改第 3 題、同時 B 觸發 run，該 run 用 B
  觸發當下的題目版本；A 之後改完不影響此已觸發的 run（自然順著「舊 run = 歷史快照」規則）。
- **診斷生成掛在 trace-ready 之後**（§6.12）：避免 async ingestion 導致存進空診斷。
- **部分完成**：任一題失敗標 `status=failed`，run 續跑其餘題，最後可為「部分完成」。
- **即時進度推送**：run 進度需即時推送前端（SSE / WebSocket 擇一，屬實作細節）。注意這是**單向、
  短生命週期**的 run 進度推送，與 §6.16 的「編輯同步」是兩回事，別混用。

## 6.16 權限與並發（Stage 1 定案）

**登入與角色**
- 開發者進系統登入，帶公司內部 key lock service 回傳的 token；授權以 token 的 identity(subject)
  對 `eval_set_roles` 查核。
- 只有兩種角色：**owner** 與 **viewer**。
  - **owner**：全部 write 權（改題、改 metadata、刪 run、觸發 re-diagnose）+ 全部 read + 執行 eval。
  - **viewer**：全部 **read**（含看 run 結果、三欄錯誤診斷詳情）+ **執行 eval**；**不能**改任何
    eval set 內容 / metadata、不能刪 run、**不能觸發 re-diagnose**（避免 LLM 成本）。
- 授權檢查做成 API 層**統一 middleware**，不散在各 endpoint：寫操作驗 owner，讀操作驗 owner 或
  viewer。
- 一個 eval set **可指派多個 owner**。

> **（實作現況，見 §9）**：
> - 統一授權以 **FastAPI 依賴** `require_owner` / `require_reader` 實作（等同「統一 guard」，但技術上
>   是 dependency 而非 middleware）。寫操作與 re-diagnose 驗 owner；讀 + 觸發 run 驗 owner 或 viewer。
> - **登入為假的（fake login）**：沒有真的 key-lock service。目前使用者身分來自 `X-User-Subject`
>   header（或 SSE 用的 `?subject=` query，或設定檔預設值），可在 UI 右上角下拉切換，方便測試
>   owner/viewer 權限。使用者名單來自 `GET /users`（設定檔 `known_users`，POC 為 alice/bob/carol/dave）。
> - **新增：分享管理**。owner 可在**上傳當下**指定分享對象（subject + role），也可事後用**每張 card 的
>   config（齒輪）按鈕**編輯名稱 / 描述 / metadata / **分享名單**。後端以 `PUT /eval-sets/{id}/roles`
>   整批覆寫分享名單（owner-only；且**操作者本人一定保留 owner**，不會把自己鎖在外面）。

**多 owner 並發寫 → 樂觀鎖（optimistic locking）**
- Stage 1 **不做**即時協作編輯（OT/CRDT）——與 Stage 1 不成比例。用樂觀鎖即可。
- 每個可獨立編輯的實體帶 `version`（`questions`、`eval_sets`）。前端開編輯時記住當下 version，
  提交時後端 `UPDATE ... WHERE id=? AND version=?` 成功則 `version+1`；未命中（他人已改過）→ 回
  **409 衝突**，前端提示「已被他人修改，請重新載入後再改」。
- **衝突粒度 = 單題**：A 改第 3 題、B 改第 5 題互不衝突；只有兩人都改第 3 題才衝突。

**讀同步 → Stage 1 用重載抓最新，不主動推送**
- eval set 的題目內容非高頻變動；Stage 1 不做編輯的即時推送。他人下次**進入該 set / 重新載入**
  時自然從 DB 拿到最新。可加被動提示「資料可能已更新，點此重載」（甚至先不做提示）。
- 前端輪詢「version 變了嗎」提示重載屬 Stage 2 可選；WebSocket 即時同步每字元不做。

## 7. 端到端驗證流程

### 7.1 Stage 1 驗收清單

> **（實作現況，見 §9）**：以下 1–10 項在 POC 中**皆已實作**，且**當時**以 Playwright + curl 端到端驗證通過。
> 唯二例外是與 Langfuse 真實串接有關的部分：POC **不寫入 Langfuse Dataset**（第 1 項末句不適用），
> trace 也是**假造**的（correlation_id 有存進 `question_results` 並用來取回假 trace，但不是真 Langfuse）。
>
> ⚠️ **驗證狀態註記**：第 1 項的上傳流程後來改為「檔案上傳 + 可編輯預覽表格」（§9.8b）。該次改動**尚未
> 重跑 Playwright 端到端驗證**——目前只驗證了「前端 parser 對 sample CSV/JSONL 的解析與序列化 round-trip
> 正確」「`EvalSetCreate.source_format` 的型別驗證」與「前端 build 通過」。**重跑第 1 項的端到端驗收仍待補**。

1. **上傳**：上傳一個小 eval set 檔案（**CSV 或 JSONL**，含 reasoning desc + skill）→ 預覽表格正確呈現且
   可編輯 → 送出後 questions 建好、未提供的 `question_id` 由系統生成且 immutable → 對應 Langfuse Dataset 出現。
2. **鎖定規則**：嘗試新增/刪除該 set 的題目 → 被擋（只能另建新 set）；透過介面改一題文字 →
   `question_id` 不變、`version+1`。
3. **執行 run**：觸發 eval → orchestrator 打 A2A agent（metadata 帶 correlation_id）→ judge 判分
   → `question_results` 寫入 → 進度條即時更新 → run 完成後 `pass_rate` 等聚合值存好。
4. **部分完成**：故意讓一題 agent timeout → 該題 `status=failed`、run 續跑其餘題並可完成。
5. **correlation + async**：對一題故意讓 agent 出錯 → correlation_id 能抓回正確 trace；trace 尚未
   ready 時 UI 顯示「生成中/重試」而非「無 trace」。
6. **診斷**：incorrect 題於 trace ready 後生成粗粒度診斷 → 存 `span_analyses`；三欄 UI 跳到第一名
   suspect，語氣為線索式、confidence 三檔；若判為 compounding/非 skill 範圍則 `caveat` 顯示於頂部。
7. **快取**：重開同題直接讀 DB、不重跑 LLM；owner 手動 re-diagnose 才重算；viewer 無 re-diagnose。
8. **三層 UI + 麵包屑**：首頁 card 顯示 run 數/通過率/趨勢/regression 摘要數；進 set 看 run 歷史；
   多選 run 時左欄三 mode（union / intersection / 近 N）正確篩選 incorrect；詳情內一鍵回 run/set。
9. **改題快照**：改一題後，既有 run 的結果與診斷不變（歷史快照）。
10. **權限與並發**：viewer 只能 read + 執行、寫操作被擋；兩個 owner 同時改同一題 → 後者收 409
    要求重載；改不同題互不衝突。

### 7.2 Stage 2 / 3 驗收（後續階段，暫存）
- Stage 2：人工改原因 / 重標 span → 寫回 Langfuse observation score(source=ANNOTATION) 且 app DB
  同步；per-span 機率熱點呈現。
- Stage 3：選多個 result → 合併去重 → 依 skill 分組 → 跑 SkillOpt → 產生新 skill；per-request
  override 重跑 → outcome 改善 → 存回 agent server。（有 caveat 的題預設排除，需人工確認。）

---

## 8. 開放問題（依三階段標註狀態）

> 標記：✅ Stage 1 已定案｜🟡 Stage 2 待議｜🔴 Stage 3 待議

1. 🟡 **per-span 出錯機率**採獨立機率還是加總為 1？UI 熱點如何呈現多個高機率 span？
   （Stage 1 不做機率；留待 Stage 2。）
2. **correct/incorrect 判準**：門檻、是否支援「部分正確」的分級？
   - ✅ Stage 1：judge 由新平台 orchestrator 自己執行，但視為**黑盒 sub-component**（§6.7）。
   - 🟡 judge 的 prompt、連續分→二元化門檻、部分正確分級，留待 judge 詳細實作時再定。
3. **reasoning description ↔ span 的對齊演算法**：
   - ✅ Stage 1 已定案採**粗粒度**（整段 reasoning + 整條截斷 trace 一次丟給 LLM）。
   - 🟡 Stage 2 才做 step 拆解、軟對齊、多有效路徑處理。
4. 🟡 **skill-selection 錯誤**與 **skill 範圍外錯誤**（tool / base model）如何在 UI 與 SkillOpt
   中區分？（Stage 2/3；Stage 1 以 `caveat` 先粗略承接「錯不在 skill」的訊號。）
5. 🔴 **SkillOpt 的具體輸入/輸出契約**：需要多少 correct/incorrect 樣本？產出的 skill 格式？
   （Stage 3。）
6. **agent server 端需要新增哪些 API**：
   - ✅ correlation_id 注入——已確認 payload metadata 直接加 `trace_id` key 即可，無 blocker。
   - 🔴 per-request skill override、skill 更新（含版本控制 / rollback）——Stage 3。
7. 🟡 **Langfuse 資料 vs app DB 的分工邊界**是否如 §6.3，或某些改放同一邊以簡化？
   （Stage 1 已定案分工見 §6.14；Stage 2+ 若有壓力再議。）
8. **成本控制**：
   - ✅ Stage 1 截斷策略已定案（§6.7：保留所有 span，只截單一 span 內超長 body）。
   - ✅ Stage 1 快取/生成流程已定案（§6.12：eval 當下 trace ready 後生成、落庫、只手動重算）。
   - 🟡 有編輯重算需求後（Stage 2 重標 span）的快取失效條件再議。
9. **並行與規模**：
   - ✅ Stage 1：orchestrator 由新平台自己做（§6.15）；run 進度即時推送；run 容許部分完成；
     多 owner 並發編輯用樂觀鎖（§6.16）。
   - 🟡 eval run 的 job queue、大量題目吞吐、編輯的即時讀同步（輪詢）留待 Stage 2。
10. **權限**：
    - ✅ Stage 1：eval-set 層 owner / viewer 兩角色、token 授權、統一 middleware（§6.16）。
    - 🟡 多租戶（多 agent server / 多 Langfuse project 隔離）留待後續。

> 補：以下原不在此清單、討論後於 Stage 1 定案——LLM input/output 契約(§6.9)、上傳介面與自訂
> metadata key(§6.10)、CSV/JSONL 結構與 question_id 穩定鍵(§6.11)、診斷生成/快取流程(§6.12)、
> 三層前端架構含多選 run 三 mode 與麵包屑(§6.13)、caveat 跨階段訊號(§6.8)。

---

## 9. Stage 1 POC 實作現況（As-Built）

> 本節描述**已經寫進 codebase 且可執行**的東西，是本文件目前最權威的「現況」來源。與 §6 藍圖若有
> 出入，以本節為準。

### 9.1 交付形態與技術棧
- **一個獨立 app**：`backend/`（Python）+ `frontend/`（React）+ `docker-compose.yml`（Postgres）。
- **Backend**：FastAPI（async）+ SQLAlchemy 2（async, `asyncpg`）+ Alembic（migration 用 sync
  `psycopg`）+ Pydantic v2。run 進度用 **SSE**（`sse-starlette`）即時推送。
- **Frontend**：React + Vite，純手寫 CSS 設計系統（無 UI 框架依賴），含 light/dark 主題與動畫。
- **DB**：PostgreSQL 16，schema 由 Alembic migration 建立（不是 in-memory；schema 本身就是重點）。
- **上傳格式**：支援 **JSONL 與 CSV 檔案**。開發者一律**上傳檔案**（不再手貼 JSONL），檔案在**前端解析**
  成一張**可編輯的預覽表格**（見 §9.9）；按 Create 前把（可能改過的）表格**在前端重新序列化為 JSONL**
  送給後端，因此後端契約仍維持 JSONL-only（§6.11），CSV 的 quoting/換行由前端 `upload_parse.js`
  處理。CSV 欄位名同 §6.11；`skill` 儲存格接受 JSON 陣列字面值或以 `,`/`;`/`|` 分隔的字串。
  開發者原本上傳的格式另以 `source_format` 欄位一併送出並存進 `eval_sets`（§9.4）。

### 9.2 真實 vs 假造的邊界（最重要）
所有外部依賴都以「假資料層」樁接，藏在**四個 Python Protocol seam 後面**，每個假實作都標了
`# REPLACE WITH REAL IMPL`，換成真實只需改一個檔（`backend/app/integrations/`）：

| Seam（Protocol） | 介面 | 假實作行為（模擬延遲） |
|---|---|---|
| `AgentClient` | `call(question, correlation_id) -> AgentResponse` | 睡 1–3s；回假 response |
| `JudgeClient` | `judge(response, ground_truth) -> Verdict(verdict,score,comment)` | 睡 0.5–1s；二元判定 |
| `TraceClient` | `fetch_trace(correlation_id) -> Trace 或 NotReady` | 前 2 次 poll 回 NotReady，之後給假 trace（練 §6.12 非同步 ingestion）|
| `DiagnosisClient` | `diagnose(trace, gt_reasoning, verdict) -> dict` | 睡 2–4s；回 §6.9 的 JSON |

- **所有延遲/計時參數集中在單一檔** `backend/app/fake_config.py`（agent/judge/diagnosis 的 min/max、
  trace not-ready poll 次數、poll backoff `[0.5,1,2,4]`、poll 上限 8 次）。
- **假造範圍**：A2A agent、LLM judge、LLM 診斷、Langfuse 取 trace **全部是假的**。**app DB、
  orchestration、樂觀鎖、權限、SSE、三層 UI、診斷落庫與讀取都是真的**。
- **可控觸發（demo/測試用）**：假層會辨識題目文字裡的標記——`⟦timeout⟧`→該題 agent「逾時」變
  `failed`；`⟦wrong⟧`→judge 判 incorrect；`⟦caveat⟧`（放 reasoning 內）→診斷帶 caveat。其餘題目
  以文字 hash 決定約 30% incorrect。

### 9.3 專案結構（關鍵檔案）
```
backend/
  alembic/versions/0001_stage1_schema.py   # §6.14 的 7 張表（唯一 migration）
  app/
    config.py           # 設定：DB URL、fake_user_subject、known_users、span 截斷長度
    fake_config.py      # ★ 唯一的延遲/計時設定檔
    db.py  models.py    # async engine；7 張表的 ORM（EvalSet.metadata 因保留字→ORM 屬性叫 meta）
    schemas.py          # Pydantic：ShareEntry / EvalSetCreate(含 shares, source_format) / RolesUpdate / *Card ...
    auth.py             # current_subject + require_owner / require_reader 依賴
    integrations/       # ★ 四個 seam：base.py(Protocol) + fake.py(假實作)
    orchestrator.py     # §6.15 run 流程（背景 asyncio task）
    sse.py              # 每個 run 的 in-memory 進度 pub/sub
    services/           # upload(JSONL 解析+question_id 生成) / truncation(§6.7) / aggregation(三 mode+regression)
    routers/            # eval_sets / questions / runs / results / diagnosis
    seed.py             # 假資料（見 §9.11 種的內容）
  sample_eval_set.jsonl  sample_eval_set.csv   # 兩種格式的範例檔（內容等價）
frontend/src/
  App.jsx api.js        # 三層檢視狀態機；API client（帶 X-User-Subject）
  upload_parse.js       # 前端 JSONL/CSV 解析→可編輯表格列，送出前再序列化回 JSONL
  components/           # EvalSetList/Sparkline/UploadDialog/ConfigDialog/ShareEditor
                        # RunHistory/RunProgress(SSE)/RunDetail/QuestionList/SpanList/SpanDetail
                        # Breadcrumb/Modal/Toast/ThemeToggle/icons
```

### 9.4 App DB Schema（如實作，對照 §6.14）
- **完全照 §6.14 建 7 張表**：`eval_sets / questions / question_skills / runs / question_results /
  span_analyses / eval_set_roles`。UUID 主鍵用 `gen_random_uuid()`（migration 先 `CREATE EXTENSION
  pgcrypto`）。`questions` 與 `eval_sets` 各有 `version` 供樂觀鎖。
- **metadata 用單一 JSONB**（`eval_sets.metadata`），**未建** §6.10 提的 `eval_set_metadata_keys` 表。
- **`source_format`（`'csv' | 'jsonl'`）記的是「開發者實際上傳的檔案格式」**，由前端隨建立請求送上。
  因為 CSV 在前端就被轉成 JSONL（§9.1），後端 payload 恆為 JSONL，此欄是唯一保留原始格式的地方。
- **實作註**：`question_results.question_pk -> questions.id` 這條 FK **沒有 ON DELETE CASCADE**（刻意——
  鎖定的 set 本就不刪題）；seed 清理舊資料時是**依 FK 順序手動刪子表**，非靠 cascade。

### 9.5 API 端點清單（實際存在）
```
GET  /health
GET  /users                                  # 假使用者名單 + 目前身分
GET  /me                                     # 目前 subject 與其在各 set 的角色
POST /eval-sets                              # 建立(payload 恆為 JSONL + source_format)；建立者=owner；可帶 shares
GET  /eval-sets                             # 我有權限的 set 卡片（含 run 數/通過率/趨勢/regression/roles）
GET  /eval-sets/metadata/keys               # 掃 JSONB 得既有 metadata key
GET  /eval-sets/{id}                        # 單一 card
PATCH/eval-sets/{id}                        # 改 name/description/metadata（樂觀鎖→409）owner
PUT  /eval-sets/{id}/roles                  # 整批覆寫分享名單 owner-only（操作者保留 owner）
GET  /eval-sets/{id}/questions              # 題目清單
PATCH/eval-sets/{id}/questions/{qpk}        # 改題（樂觀鎖→409；question_id 不變）owner
POST /eval-sets/{id}/runs                   # 觸發 run（owner 或 viewer）
GET  /eval-sets/{id}/runs                   # run 列表（含 incorrect_count）
GET  /eval-sets/{id}/runs/{run_id}/progress # SSE 即時進度
GET  /eval-sets/{id}/results                # 左欄題目清單；?run_ids=..&mode=union|intersection|last_n&last_n=
GET  /eval-sets/{id}/results/{rid}/trace    # 中+右欄：即時抓(假)trace(截斷) + 讀 DB 的診斷
POST /eval-sets/{id}/results/{rid}/re-diagnose  # 手動重診斷 owner-only
```

### 9.6 Orchestrator（如實作，對照 §6.15）
`POST /runs` 建立 `runs`(status=running) 後，開一個背景 asyncio task 跑 `orchestrator.run_eval`：
run 開始**讀定 question 快照** → 每題：生 correlation_id → 假 agent → 假 judge → 寫
`question_results` → poll（backoff）等 trace ready → 標 `trace_ready` → 若 incorrect：抓+截斷 trace →
假診斷 → 寫 `span_analyses`（含 caveat）→ 每題透過 SSE 推進度。**任一題失敗標 `status=failed`，run
續跑**（部分完成）。完成時算好 `pass_rate/total_count/correct_count` 存回 `runs`。

### 9.7 診斷 I/O 契約（如實作，對照 §6.9）
假診斷器輸出**強制 JSON**：`overall_diagnosis`（白話總結）、`suspects[]`（每個含
`span_index / confidence(high|medium|low) / reason / evidence`，第一名排最前，前端預設跳它）、
`caveat`（可選，懷疑非單一 span 或非 skill 可控）。整包存進 `span_analyses.raw_llm_output`(JSONB)，
`caveat` 另存獨立欄。前端讀 DB 直接 render，不重跑（§6.12 快取）。

### 9.8 權限、登入、分享（如實作，對照 §6.16）——**含新增功能**
- **角色**：owner（全寫 + 讀 + 跑 run + re-diagnose）、viewer（讀 + 跑 run；不可寫、不可 re-diagnose）。
  以 `eval_set_roles`（subject→role）為準；guard 用 `require_owner` / `require_reader` 依賴。
- **樂觀鎖 409**：`questions`、`eval_sets` 各帶 `version`；`UPDATE ... WHERE id AND version` 未命中回
  **409**。衝突粒度=單列。已驗證兩人改同題→後者 409。
- **假登入**：`X-User-Subject` header（SSE 用 `?subject=`）或設定檔預設；UI 右上角下拉切換身分。
- **★ 新增：分享（§6.10/§6.16 的延伸）**
  - 上傳時 `EvalSetCreate.shares`（`[{subject, role}]`）直接建對應 `eval_set_roles`。
  - **分享對象一律以「直接輸入人名」新增**（例：Alice 輸入 `bob`）；已移除原本「從預先定義名單下拉挑選」
    那個入口，`ShareEditor` 只保留自由輸入框 + add。加入後仍可切換該對象的 viewer/owner 角色。
  - 每張 card 有 **config 齒輪**（僅 owner 見）：一個對話框可改 name/description/metadata **與分享名單**。
  - `PUT /eval-sets/{id}/roles` **整批覆寫**分享名單；**操作者本人永遠保留 owner**（不可自我鎖出、
    保證至少一個 owner）。viewer 呼叫→403。
  - card 回傳 `roles`（分享名單）供 config 對話框顯示；首頁 card 顯示「N members」。

### 9.8b 上傳介面（如實作）——**檔案上傳 + 可編輯預覽表格**
- **不再有大 JSONL 文字框**。開發者按「Choose file…」選一個 **JSONL 或 CSV 檔**（或按「load sample」
  載入內建範例）→ 檔案在前端解析為一張**可編輯表格**（欄位：question / ground_truth_response /
  reasoning_process_description / skill(s) / question_id）。
- **表格可就地編輯**：每格可改；可 **add row / 刪 row**（鎖定規則僅在 set 建立**之後**生效，故上傳當下
  可自由增刪列）。`question_id` 留白代表由後端生成 immutable id。
- 按 **Create** 時，前端把表格重新序列化為 JSONL 打 `POST /eval-sets`，並附上 `source_format`
  （開發者實際上傳的格式，§9.4）；解析/驗證錯誤（缺必填欄、skill 空）在送出前於前端先提示，
  後端仍會再驗一次（422）。
- 因為要容納預覽表格，**上傳對話框放大**（width 960）。
- **範例檔**：`backend/sample_eval_set.jsonl` 與 `backend/sample_eval_set.csv`（內容等價，供兩種格式測試）；
  對話框另有「load sample」可不用檔案直接載入兩列示範資料。

### 9.9 前端三層 UI（如實作，對照 §6.13）——**含新增功能**
- **三層下鑽**都做了：首頁 card（run 數 / 最近通過率 / 趨勢小折線 / regression 摘要數）→ 某 set 的
  run 歷史（多選 run + union/intersection/last-N 三種 incorrect mode）→ 三欄詳情（左題目清單可篩
  「只看錯的」；中 span 列表 + 頂部 `overall_diagnosis` + caveat 橫幅 + suspect 標 confidence，自動選
  第一名 suspect；右 span 的 input/output/token + 該 span 的 reason+evidence 或「未被標為可疑」）。
- **麵包屑 + 一鍵回上層**：已做。
- **★ 新增（回應「太像玩具」的回饋）**：整套現代化 CSS 設計系統（陰影/圓角/字級/焦點框）、卡片
  hover 浮起、清單進場、**對話框 pop-in 動畫**、進度條 shimmer、**Toast** 提示（存檔/衝突/錯誤）；
  **light/dark 主題切換**（右上角，首次進站前套用避免閃爍，存 localStorage，預設跟隨作業系統）；
  **每張 card 的 config 齒輪**（見 §9.8）。

### 9.10 截斷 / 快取 / 進度（如實作）
- **§6.7 截斷**：只截**單一 span 過長的 input/output body**（保留頭尾、中間省略），**絕不砍 span**；
  門檻 `config.span_body_max_chars`（預設 800）。截斷在「檢視時抓 trace」當下套用。
- **§6.12 快取**：診斷在 run 當下（trace ready 後）生成並落庫；事後點開**直接讀 DB**；唯一重算入口是
  owner 手動 re-diagnose。
- **進度**：SSE（`sse.py` 的 in-memory pub/sub，單程序 POC）。

### 9.11 seed 假資料（`python -m app.seed`）
一個 eval set「Billing Agent Regression Suite」，角色 alice=owner、bob=viewer、carol=viewer（示範分享）；
5 題、3 個 run，通過率 0.8→0.6→0.4（可見的退步趨勢），使三種 incorrect mode 明顯不同（union / 
intersection / last-2 各給不同題集）；含一題帶 **caveat** 的診斷、一題 trace 「生成中」狀態、以及一個
超長 span body 觸發 §6.7 截斷。

### 9.12 與本設計文件（§1–§8）的差異總表
| 主題 | 原文件說 | 實作現況 |
|---|---|---|
| 上傳格式 | CSV + JSONL 皆定義 | **JSONL + CSV 檔案上傳**（前端解析成可編輯表格，送出前再序列化為 JSONL；後端仍 JSONL-only）|
| metadata keys | 需 `eval_set_metadata_keys` 表 | **未建表**，單一 JSONB + 掃描取 key |
| metadata 篩選/排序 | 首頁可依 key 篩選/排序 | **尚未做** |
| 授權 | 「統一 middleware」 | 以 **FastAPI 依賴**（require_owner/reader）實作，效果等同 |
| 登入 | key-lock service token | **假登入**（header/query/設定檔 + UI 切換）|
| 上傳介面 | 未細談（§6.10 只談 card 欄位） | **檔案上傳 + 可編輯預覽表格**（§9.8b）：無手貼文字框，送出前可逐格修改與增刪列 |
| 分享 | 只提「可多 owner」 | **新增完整分享 UI/API**：上傳時指定分享對象、card config 改分享名單、`PUT /roles`；**對象一律直接輸入人名**（無預設名單下拉）|
| Langfuse | 讀 trace / 寫 dataset+score | **完全樁接**：不寫 Langfuse，trace 為假；correlation_id 有存但指向假 trace |
| UI 外觀/主題 | 未提 | **新增**現代化設計系統、動畫、Toast、**light/dark 主題** |
| 逐題 regression | 標記為 Stage 1.5 | 首頁 card 的 regression 摘要與三 mode **皆已做** |

### 9.13 如何執行
- 一鍵：`SEED=1 ./scripts/dev.sh`（起 Postgres → 裝依賴 → migrate → seed → 起 backend:8000 + 
  frontend:5173）。或用 `make` 分項（`make db/setup/migrate/seed/backend/frontend`）。
- 需 **Python 3.10–3.13**（3.14 尚無部分套件 wheel）；`dev.sh` 會自動挑合適的直譯器。
- 詳見 repo 根目錄 `README.md`。

### 9.14 明確「尚未做」（維持 Stage 2/3 邊界）
per-span 機率/熱點、人工重標 span、SkillOpt、per-request skill override 重跑、寫回 agent server、
真實 Langfuse/A2A/LLM 串接、多租戶隔離、編輯的即時讀同步（目前靠重載/切換身分刷新）。
（CSV 上傳已補上——見 §9.1；惟後端仍以 JSONL 為單一寫入契約，CSV 於前端解析。）
