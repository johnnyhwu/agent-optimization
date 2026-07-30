# Agent Evaluation + Trace 錯誤定位 + Skill Evolving 系統 — Spec 討論輸入文件

> 本文件用途：作為與 Claude chat 後續討論的輸入，目標是把下述系統擬定成一份詳細的
> system spec。文件自包含，讀者無需任何前文即可理解。以下內容包含 (1) 背景情境、
> (2) 想做的系統、(3) 對 Langfuse 能力邊界的查證結論、(4) 已找出的設計風險/bug、
> (5) 已定案的架構決策與推薦架構藍圖、(6) 待與 Claude chat 深入討論釐清的開放問題。

---

> ## ⚑ 怎麼讀這份文件（2026-07，先讀這段）
>
> **這份文件有兩半，寫作時間相差很遠，讀錯順序會被誤導。**
>
> | 章節 | 是什麼 | 可信度 |
> |---|---|---|
> | **§1–§5** | 原始背景、對 Langfuse 能力邊界的查證、找出的風險、定案的架構決策 | **設計脈絡**。說明「為什麼是這樣設計」，不描述程式碼 |
> | **§6** | 分階段藍圖與 Stage 1 的逐項定案 | **設計意圖**。多數已實作，但細節有出入 |
> | **§7–§8** | 驗收清單與開放問題 | 部分已完成，狀態以 §9 / §10 為準 |
> | **§9** | **Stage 1 實作現況（As-Built）** | **唯一權威的「Stage 1 到底做了什麼」** |
> | **§10** | **Stage 4：Playground 實作現況（As-Built）** | **唯一權威的「Playground 到底做了什麼」**。這是原三階段藍圖之外新增的階段 |
>
> **§1–§8 與 §9 衝突時，一律以 §9 為準。** §1–§8 刻意保留原貌（包含後來被推翻的假設），
> 因為那是理解設計取捨的脈絡；但它們**不是**目前程式碼的描述。
>
> **第一次讀這個專案，建議路徑**：
> 1. **§1–§2**（背景與想解決的問題）——不讀這段，後面所有設計都會顯得沒有動機。
> 2. **§6.6–§6.7**（為什麼只做 Stage 1、Stage 1 的範圍）。
> 3. **直接跳到 §9**，先看該節開頭的「§9 的地圖」再挑要讀的小節。§9 是自包含的。
> 4. 想動手跑起來或接真實服務，看 repo 根目錄的 **`README.md`**——那份是操作手冊，
>    本文件是設計與實作紀錄，兩者互補不重複。
>
> **一句話現況**：真實的 React UI + 真實的 app-DB schema + 真實的 orchestration/權限/樂觀鎖/
> SSE 邏輯；四個外部依賴（HTTP agent、LLM judge、LLM 診斷、Langfuse 取 trace）**假、真兩套
> 實作都已寫好**，由四個環境變數逐一切換，**預設全部走假的**，所以不接任何外部服務也能完整跑完。
> **Langfuse 那一個 seam 已經對接過真環境**，真實 trace 讀得回來也渲染得出來（§9.19）；
> 另外三個（agent / judge / diagnosis）仍只用 mock 端到端驗證過（詳見 §9.2 與 §9.16）。
>
> ⚠️ 兩個常見的踩雷點：§1.1／§6.2 寫的 **A2A protocol 已經不是現況**（agent server 改成單一
> `POST /execute`，見 §9.12），而 §6.14 的 schema 之後又加了四個 migration（見 §9.4）。

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
  > **已定案並實作，見 §9.7 / §9.15**：prompt 與門檻都已落地，且介面多了 `question` 參數
  > （真 judge 需要題目本身當 context）。

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

> 本節描述**已經寫進 codebase 且可執行**的東西，是本文件目前最權威的「現況」來源。與 §1–§8
> 的討論/藍圖若有出入，**一律以本節為準**——前面章節是設計過程的紀錄，本節是實際做出來的東西。
>
> **一句話現況**：Stage 1 的 app 本身（DB schema、orchestration、權限、樂觀鎖、SSE、三層 UI、
> 診斷落庫與讀取）**全是真的**；四個外部依賴（HTTP agent、LLM judge、Langfuse trace、LLM 診斷）
> **假、真兩套實作都已寫好**，用四個環境變數逐一切換，**預設走假的**，因此不接任何外部服務也能
> 完整跑起來。**Langfuse 的真實讀取已對接過真環境並讀得回 trace**（見 §9.19）；agent / judge /
> diagnosis 三個 seam 的真實實作仍只用 mock 驗過（見 §9.16）。
>
> **§9 的地圖**（§9.1–§9.16 描述現況；§9.17–§9.19 是三次後續補強的「改了什麼、為什麼」，
> 其中被修正的現況已同步回前面各節，所以前後不會互相矛盾）：
>
> | 節 | 內容 | 什麼時候看 |
> |---|---|---|
> | §9.1 | 交付形態與技術棧 | 想知道用了什麼 |
> | **§9.2** | **真假邊界（最重要的一節）** | 想知道哪些是真的、怎麼切換 |
> | §9.3 | 專案結構（關鍵檔案） | 想找某個東西在哪 |
> | **§9.4** | **App DB schema 與五個 migration** | 想理解資料模型 |
> | §9.5 | API 端點清單 | 要串接或除錯 |
> | **§9.6** | **Orchestrator 流程與失敗策略** | 想理解一次 run 到底發生什麼 |
> | §9.7 | 診斷的 I/O 契約 | 想調診斷品質 |
> | §9.8 / §9.8b | 權限、分享、上傳介面 | |
> | §9.9 | 前端三層 UI（含即時更新機制） | 想改前端 |
> | §9.10 | 截斷（只剩診斷路徑）／快取／SSE 事件表 | |
> | §9.11 | seed 假資料的內容 | 想看 demo 資料為什麼長這樣 |
> | §9.12 | **與 §1–§8 設計文件的差異總表** | 讀過前半段後**務必看這張表** |
> | §9.13 | 如何執行 | |
> | §9.14 | **明確「尚未做」** | 接手前務必看 |
> | §9.15 | 完整設定表（環境變數） | 要部署或接真實服務 |
> | §9.16 | 測試清單與「哪些已驗證／哪些沒有」 | 想知道能信到什麼程度 |
> | §9.17 | 補強一：中止 run、刪除、執行中的完整 question list、錯誤可見性 | 想知道演進脈絡 |
> | §9.18 | 補強二：三欄即時更新、Langfuse 雙端點、未開始題目不誤報、run 選單上限、清單分頁與效能 | 同上 |
> | §9.19 | 補強三：span payload 的結構化渲染（不再截斷、改用收合）、中欄分成三個具名分區 | 同上 |

### 9.1 交付形態與技術棧
- **一個獨立 app**：`backend/`（Python）+ `frontend/`（React）+ `docker-compose.yml`。
- **全部容器化**：`db` / `backend` / `frontend` **各自是一個 container**，由 compose 編排。
  host 端唯一需求是 **docker（含 compose）**——不需要 host 的 Python venv 或 node_modules。
  - backend image 釘 **CPython 3.12**，依賴用 **uv**（`uv pip install --system`）安裝。
  - frontend image 用 **pnpm**（corepack 釘版本）安裝依賴，鎖檔為 `pnpm-lock.yaml`。
  - 兩個 app container 都 bind-mount 原始碼，所以 `uvicorn --reload` 與 Vite HMR 照常運作；
    只有 `requirements.txt` / `package.json` 變動才需要 rebuild。
- **Backend**：FastAPI（async）+ SQLAlchemy 2（async, `asyncpg`）+ Alembic（migration 用 sync
  `psycopg`）+ Pydantic v2。run 進度用 **SSE**（`sse-starlette`）即時推送。
  對外整合用 `httpx`（agent HTTP / Langfuse）與 `openai` SDK（OpenAI 相容端點）。
- **Frontend**：React + Vite，純手寫 CSS 設計系統（無 UI 框架依賴），含 light/dark 主題與動畫。
- **DB**：PostgreSQL 16，schema 由 Alembic migration 建立（不是 in-memory；schema 本身就是重點）。
- **測試**：`pytest` + `pytest-asyncio` + `respx`（httpx mock），共 **127 個測試**。其中 116 個
  **不需要 DB 也不需要網路**（`make test` 只跑這些）；另外 11 個分頁測試需要真 Postgres，未設
  `TEST_DATABASE_URL` 時自動 skip（見 §9.16）。
- **上傳格式**：支援 **JSONL 與 CSV 檔案**。開發者一律**上傳檔案**（不再手貼 JSONL），檔案在**前端解析**
  成一張**可編輯的預覽表格**（見 §9.9）；按 Create 前把（可能改過的）表格**在前端重新序列化為 JSONL**
  送給後端，因此後端契約仍維持 JSONL-only（§6.11），CSV 的 quoting/換行由前端 `upload_parse.js`
  處理。CSV 欄位名同 §6.11；`skill` 儲存格接受 JSON 陣列字面值或以 `,`/`;`/`|` 分隔的字串。
  開發者原本上傳的格式另以 `source_format` 欄位一併送出並存進 `eval_sets`（§9.4）。

### 9.2 真實 vs 假造的邊界（最重要）
四個外部依賴各自藏在一個 **Python Protocol seam** 後面，**假、真兩套實作都已存在**，由
`backend/app/integrations/__init__.py` 的 `build_seams(config, secrets)` 依設定**逐一 seam**
選用（`*_IMPL=fake|real`）。預設四個都是 `fake`，所以 `SEED=1 ./scripts/dev.sh` 不需要任何
外部服務就能跑完整 demo；要接真的可以一個一個開，不必一次全換。

**`*_IMPL` 決定 fake/real（全域），端點則逐 run 決定**：`build_seams` 每次都建新的 client
實例，設定來自該 run 的 `runs.config` / `runs.secrets`（空值退回環境變數）。這不只是彈性——
`trigger_run` 開背景 task 時沒有鎖，若改成變動全域 settings，兩個併行的 run 會互相污染端點。

| Seam（Protocol） | 介面 | 假實作（模擬延遲） | 真實實作 |
|---|---|---|---|
| `AgentClient` | `call(question, correlation_id, user_id, tags) -> AgentResponse` | 睡 1–3s；回假 response | `real/agent.py`：`POST /execute {"message","metadata"}`，metadata.trace_data 帶 trace_id(=correlation_id)/session_id(=correlation_id)/user_id/tags，回應為 `{"content": str}`（§6.2）|
| `JudgeClient` | `judge(question, response, ground_truth) -> Verdict` | 睡 0.5–1s；二元判定 | `real/judge.py`：OpenAI 相容端點，LLM 同時吐 verdict+score，可選門檻覆寫 |
| `TraceClient` | `fetch_trace(correlation_id) -> Trace 或 NotReady` | 前 2 次 poll 回 NotReady，之後給假 trace | `real/langfuse.py`：**兩條讀取策略依序嘗試**——`GET /api/public/traces/{id}` 與 `GET /api/public/v2/observations?traceId=`；0 筆或 404 = NotReady，全部失敗才 raise `TraceFetchError`（§6.12、§9.18(b)）|
| `DiagnosisClient` | `diagnose(trace, gt_reasoning, verdict) -> dict` | 睡 2–4s；回 §6.9 的 JSON | `real/diagnosis.py`：§6.9 四段式 prompt，輸出驗證 + span_index 越界剔除 |

> **介面變更**：`judge()` 多了 `question` 參數——真實 LLM judge 需要題目本身當 context 才判得準。

- **假層延遲參數集中在** `backend/app/fake_config.py`（agent/judge/diagnosis 的 min/max、
  trace not-ready poll 次數）。**trace poll backoff 與上限已移到 `app/config.py`**：它同時
  管真實 Langfuse ingestion 的等待，而真實 ingestion 比假層慢一個數量級。
- **可控觸發（demo/測試用）**：假層會辨識題目文字裡的標記——`⟦timeout⟧`→該題 agent「逾時」變
  `failed`；`⟦wrong⟧`→judge 判 incorrect；`⟦caveat⟧`（放 reasoning 內）→診斷帶 caveat。其餘題目
  以文字 hash 決定約 30% incorrect。**真實實作不認得這些標記**（真 agent 沒有理由認得）。
- **接真實需要 agent server 端配合（§6.2）**：agent server 必須讀 `/execute` request body 的
  `metadata.trace_data.trace_id` 並用它當 Langfuse trace id，否則平台無從找回自己剛觸發的
  trace。`trace_id` 與 `session_id` 用同一個值（每題都是自己的 correlation 單位）；`user_id`
  是觸發該 run 的使用者；`tags` 帶 `["eval_<eval_set 名稱>"]`。
- **落庫新增**（migration `0002_real_integration`）：`question_results.agent_response`（agent
  實際回答）、`error_message`（失敗原因）、`agent_latency_ms`，以及 `runs.error_message`。
  假資料時代不需要，真實情境下「看得到 eval 結果」少不了它們。
  （`0003_run_config` 再加上 `runs.name` / `runs.config` / `runs.secrets`——逐 run 設定，見 §9.15。）
- **失敗策略**：單題失敗（agent 不通、judge 解析不了、timeout）→ 該題 `failed` 並記下原因，
  run 繼續並正常收斂（partial completion）；診斷失敗**不影響**該題判定；任何非預期例外仍會把 run
  結掉並送出 SSE 終止事件——run 不會卡在 `running` 讓前端空等。
- **前置檢查**：`python -m app.check_integrations`（`make preflight`）會逐一 ping 設為 real 的
  seam 並回報 OK/FAIL。

### 9.3 專案結構（關鍵檔案）
```
backend/
  Dockerfile  .dockerignore                # backend container；依賴用 uv 安裝
  alembic/versions/0001_stage1_schema.py   # §6.14 的 7 張表
  alembic/versions/0002_real_integration.py # agent_response / error_message / agent_latency_ms
  alembic/versions/0003_run_config.py       # runs.name / runs.config / runs.secrets（逐 run 設定）
  alembic/versions/0004_run_lifecycle.py    # cancel_requested / trace_error / diagnosis_error（§9.17）
  alembic/versions/0005_list_indexes.py     # ★ 兩個清單端點要用的三個索引（§9.18(e)）
  app/
    config.py           # 設定：DB URL、假登入、span 截斷長度、★四個 *_IMPL 開關、
                        #   agent HTTP / LLM / Langfuse 連線、run_concurrency、trace poll backoff
                        #   （連線類的值同時是 run config dialog 的預設值）
    fake_config.py      # ★ 假層專用的延遲設定檔
    db.py  models.py    # async engine；7 張表的 ORM（EvalSet.metadata 因保留字→ORM 屬性叫 meta）
    schemas.py          # Pydantic：ShareEntry / EvalSetCreate(含 shares, source_format) / RolesUpdate / *Card
                        #   RunConfig(非機密) / RunSecrets(只進不出) / RunCreate / RunOut ...
    auth.py             # current_subject + require_owner / require_reader 依賴
    integrations/       # ★ 四個 seam：base.py(Protocol) + fake.py(假) + real/(真)
      real/agent.py  real/judge.py  real/langfuse.py  real/diagnosis.py
      real/llm.py       # 共用 OpenAI 相容 client + JSON 契約解析（含一次修復重試）
      real/prompts.py   # judge prompt + §6.9 四段式診斷 prompt
      __init__.py       # Seams + build_seams(config, secrets)：依 *_IMPL 選 fake / real，
                        #   端點則逐 run 決定（空值退回環境變數）
    orchestrator.py     # §6.15 run 流程（背景 asyncio task）+ 失敗策略 + 併發上限
    cancellation.py     # 中止 run：耐久旗標 + in-process asyncio.Event（§9.17(a)）
    check_integrations.py # 前置檢查：ping 設為 real 的 seam
    sse.py              # 每個 run 的 in-memory 進度 pub/sub
    services/           # upload(JSONL 解析+question_id 生成) / truncation(§6.7，只給診斷 prompt 用)
                        #   aggregation(三 mode+regression)
                        #   run_config(逐 run 設定的 env 預設值 + 觸發時寫死有效值)
                        #   deletion(FK 安全的刪除順序，seed 與兩個 DELETE 端點共用)
    routers/            # eval_sets / questions / runs / results / diagnosis
    seed.py             # 假資料（見 §9.11 種的內容）
  tests/                # 9 個檔案、127 個測試，逐一說明見 §9.16
  sample_eval_set.jsonl  sample_eval_set.csv   # 兩種格式的範例檔（內容等價）
frontend/src/
  App.jsx api.js        # 三層檢視狀態機；API client（帶 X-User-Subject）
  upload_parse.js       # 前端 JSONL/CSV 解析→可編輯表格列，送出前再序列化回 JSONL
  usePagedList.js       # ★ 兩個清單共用的分頁 hook（追加、去重、擋過期回應）+ 捲動哨兵
  components/           # EvalSetList/Sparkline/UploadDialog/ConfigDialog/ShareEditor/QuestionEditor
                        # RunHistory/RunConfigDialog/RunPicker(有上限的 run 選單)/RunConfigView(唯讀)
                        # RunProgress(SSE)/RunStatusBar(執行中的堆疊長條+中止鈕)
                        # RunDetail/QuestionList/SpanList(中欄分區)/SpanDetail
                        # SpanPayload(★ chat 形狀的 span input/output 收合渲染)
                        # ListFooter(Load more + 捲動哨兵)/ConfirmDialog
                        # Breadcrumb/Modal/Toast/ThemeToggle/icons
```

### 9.4 App DB Schema（如實作，對照 §6.14）
- **完全照 §6.14 建 7 張表**：`eval_sets / questions / question_skills / runs / question_results /
  span_analyses / eval_set_roles`。UUID 主鍵用 `gen_random_uuid()`（migration 先 `CREATE EXTENSION
  pgcrypto`）。`questions` 與 `eval_sets` 各有 `version` 供樂觀鎖。
- **五個 migration**：`0001_stage1_schema`（7 張表）、`0002_real_integration`（真實整合所需欄位）、
  `0003_run_config`（`runs.name` / `runs.config` / `runs.secrets`，逐 run 設定）、
  `0004_run_lifecycle`（`runs.cancel_requested` / `question_results.trace_error` /
  `question_results.diagnosis_error`，見 §9.17）、
  `0005_list_indexes`（三個索引，見下）。
- **★ `0005_list_indexes`**：在此之前，schema **除了主鍵與 unique 約束之外一個索引都沒有**——
  資料只來自 `seed.py` 時無所謂，一旦累積真實歷史就不是。新增的三個都是兩個清單端點實際會走的路徑：

  | 索引 | 為什麼需要 |
  |---|---|
  | `eval_set_roles(user_subject)` | 首頁的第一個查詢是「這個人看得到哪些 set」，但該表主鍵是 `(eval_set_id, user_subject)`，用 subject 單獨查用不到主鍵索引 |
  | `runs(eval_set_id, started_at DESC)` | run 列表與每張卡的聚合（run 數、趨勢、最新兩個 run）都靠它，一次有序索引掃描解決 |
  | `question_results(run_id, verdict)` | 算 incorrect 數的聚合。`(run_id, question_pk)` 的 unique 約束已能用 run 查，但依 verdict 計數仍要回表 |

  不用 `CONCURRENTLY`：Stage 1 的資料量還小，而且 `CONCURRENTLY` 無法在 Alembic 的交易內執行。
- **★ `0003_run_config`**：`config`（非機密：base URL、模型、timeout、concurrency）與
  `secrets`（金鑰）刻意分成兩個 JSONB 欄位——沒有任何 response model 讀 `secrets`，
  「金鑰不外流」因此是結構上的保證，而不是靠人記得維護白名單。舊 run 的 `'{}'`
  代表整組退回環境變數，行為與過去完全一致。
- **★ `0002_real_integration` 新增的四個欄位**（假資料時代不需要，接真實服務後不可或缺）：

  | 欄位 | 用途 |
  |---|---|
  | `question_results.agent_response` | **agent 實際回答的內容**。原本只存 judge 的 verdict，接真 agent 後等於看不到「被評的東西是什麼」 |
  | `question_results.error_message` | 該題 `status='failed'` 的**原因**（agent 不通 / judge 解析失敗 / timeout）。原本只有一個光禿禿的 `failed` |
  | `question_results.agent_latency_ms` | agent round-trip 實測耗時 |
  | `runs.error_message` | 整個 run 以 `status='failed'` 收場的原因 |

- **metadata 用單一 JSONB**（`eval_sets.metadata`），**未建** §6.10 提的 `eval_set_metadata_keys` 表。
- **`source_format`（`'csv' | 'jsonl'`）記的是「開發者實際上傳的檔案格式」**，由前端隨建立請求送上。
  因為 CSV 在前端就被轉成 JSONL（§9.1），後端 payload 恆為 JSONL，此欄是唯一保留原始格式的地方。
- **實作註**：`question_results.question_pk -> questions.id` 這條 FK **沒有 ON DELETE CASCADE**（刻意——
  鎖定的 set 本就不刪題）。因此刪 eval set **不能只靠 Postgres cascade**（cascade 不保證會先刪
  `question_results` 再刪 `questions`）；順序統一收在 `services/deletion.py`，seed 與兩個 DELETE
  端點共用（見 §9.17）。

### 9.5 API 端點清單（實際存在）
```
GET  /health
GET  /users                                  # 假使用者名單 + 目前身分
GET  /me                                     # 目前 subject 與其在各 set 的角色
GET  /run-config/defaults                    # run config dialog 的預填值（env 來源）+ 四個 *_IMPL 現況
POST /eval-sets                              # 建立(payload 恆為 JSONL + source_format)；建立者=owner；可帶 shares
GET  /eval-sets                             # 我有權限的 set 卡片（含 run 數/通過率/趨勢/regression/roles）
                                            #   分頁 + 篩選：?limit&offset&q&metadata_key&metadata_value&sort
                                            #   回傳 {items,total,has_more}（§9.18）
GET  /eval-sets/metadata/keys               # 掃 JSONB 得既有 metadata key
GET  /eval-sets/{id}                        # 單一 card
PATCH/eval-sets/{id}                        # 改 name/description/metadata（樂觀鎖→409）owner
PUT  /eval-sets/{id}/roles                  # 整批覆寫分享名單 owner-only（操作者保留 owner）
GET  /eval-sets/{id}/questions              # 題目清單
PATCH/eval-sets/{id}/questions/{qpk}        # 改題（樂觀鎖→409；question_id 不變）owner
DELETE /eval-sets/{id}                      # 刪除整個 set（含所有 run/結果/診斷）owner-only；
                                            #   底下有 running run → 409（先中止）
POST /eval-sets/{id}/runs                   # 觸發 run（owner 或 viewer）；body 帶 name/config/secrets
                                            #   /reuse_secrets_from_run_id，全部可省略
GET  /eval-sets/{id}/runs                   # run 列表（含 incorrect_count / name / config / credentials_set
                                            #   / cancel_requested）；分頁 ?limit&offset&q，
                                            #   回傳 {items,total,has_more}（§9.18）
GET  /eval-sets/{id}/runs/{run_id}          # 單一 run（詳情頁判斷中止鈕用；§9.18）
POST /eval-sets/{id}/runs/{run_id}/cancel   # 中止 run（owner 或該 run 的觸發者）；非 running → 409
DELETE /eval-sets/{id}/runs/{run_id}        # 刪除一個 run owner-only；running → 409（先中止）
GET  /eval-sets/{id}/runs/{run_id}/progress # SSE 即時進度
GET  /eval-sets/{id}/results                # 左欄題目清單；?run_ids=..&mode=union|intersection|last_n&last_n=
GET  /eval-sets/{id}/results/{rid}/trace    # 中+右欄：即時抓 trace(完整 body) + 讀 DB 的診斷
POST /eval-sets/{id}/results/{rid}/re-diagnose  # 手動重診斷 owner-only
```
（`/docs`、`/redoc`、`/openapi.json` 是 FastAPI 內建的，未列。）

- `GET /results` 每題回傳含 **`agent_response` / `error_message` / `agent_latency_ms`**
  （§9.4 新欄位）與 `verdict / judge_score / judge_comment / status / trace_ready / has_analysis /
  is_incorrect`、**`phase`**（`pending`|`answered`|`judged`|`failed`|`cancelled`，見 §9.17），
  以及 **`run_label`**——多選 run 時這一列是**跨 run 挑出來的代表**，可能來自比正在看的那個 run
  更舊的 run，不標出來很容易誤認（§9.18(c)）。
- `GET /results/{rid}/trace` 回傳 `spans[]`（含 `status_message`）、`analysis`、
  **`trace_error`** / **`diagnosis_error`**，以及 **`agent_response` / `ground_truth_response` /
  `error_message`**（讓中間欄並排顯示「agent 答了什麼 vs 期望答案」）。
  `trace_state` 有五個值，**分清楚它們是這支端點的主要價值**：

  | `trace_state` | 意思 | UI |
  |---|---|---|
  | `ready` | 抓到了 | 顯示 span 列表 |
  | `generating` | agent 已回答，但 Langfuse ingestion 還沒落地（§6.12） | 「產生中，重試中」+ Retry |
  | **`not_started`** | **agent 還沒被問到這題** | 「等待 agent」。**此時完全不呼叫 trace store**——以前會呼叫，於是壞掉的 Langfuse 會吐出一個跟上次一模一樣的新錯誤，看起來像舊錯誤被重播（§9.18(c)）|
  | `no_trace` | 該題 failed / cancelled，沒有 trace 可抓 | 說明沒有 trace |
  | `error` | trace store 讀不到（host 錯、401、逾時、server 端 SQL 錯誤） | 紅色 banner + 白話說明 + 原始錯誤收在可展開區塊（§9.18(b)）|

### 9.6 Orchestrator（如實作，對照 §6.15）
`POST /runs` 建立 `runs`(status=running，並存下該次的 `name` / `config` / `secrets`) 後，開一個
背景 asyncio task 跑 `orchestrator.run_eval`：先用 `build_seams(run.config, run.secrets)` 建出這個
run 專屬的四個 client，並從 `config["concurrency"]` 決定併發上限、`config["agent_timeout_s"]`
決定單題逾時（**都是逐 run，不再讀全域 settings**）→
run 開始**讀定 question 快照**（之後改題不影響這次 run）→ **一次把整份快照的 `question_results`
全部建好**（`status='pending'`，見 §9.17）→ 每題：agent →
judge → 寫 `question_results`（含 `agent_response` 與 `agent_latency_ms`）→ poll（backoff）等
trace ready → 標 `trace_ready` → 若 incorrect：抓+截斷 trace → 診斷 → 寫 `span_analyses`（含
caveat）→ 每題透過 SSE 推進度。完成時算好 `pass_rate/total_count/correct_count` 存回 `runs`。

**★ 失敗策略（接真實服務後的重點；假層永遠不會 raise，所以這整段在假資料時代是無效程式碼）**

| 情境 | 行為 |
|---|---|
| 單題失敗（agent 不通、judge 回不了合法 JSON、timeout） | 該題 `status='failed'` 並**寫下 `error_message`**，run 繼續跑其餘題目（partial completion） |
| judge 呼叫失敗 | **絕不預設為 correct**——那會灌水通過率。該題記為 failed、`verdict` 留 null |
| 診斷失敗 | **不影響該題判定**。verdict 才是結果，診斷是加值；owner 可事後手動 re-diagnose |
| trace store 暫時抓不到 | 不算該題失敗，只是 `trace_ready=false`，**並把原因寫進 `question_results.trace_error`**（§9.17）|
| 任何非預期例外 | 仍會把 run 收成 `status='failed'`、寫 `runs.error_message`、**並送出 SSE 終止事件**。run 不會卡在 `running` 讓前端無限等待 |
| 使用者按下中止 | 立刻放棄進行中的 agent/judge 呼叫，該題 `status='cancelled'`；未開始的題目留 `pending`；已判分的結果保留；run 收成 `status='cancelled'`、`pass_rate=None`（§9.17）|

**其他執行控制**
- **timeout**：agent 呼叫包 `asyncio.wait_for`（`AGENT_TIMEOUT_S`），client 自身另有 httpx timeout。
- **重試**：對暫時性錯誤（timeout / 連線錯誤）做**有上限的指數退避**重試
  （`AGENT_MAX_RETRIES` / `LLM_MAX_RETRIES`，預設各 2 次）。4xx 這類必然重現的錯誤不重試。
- **併發**：`RUN_CONCURRENCY`（預設 **1** ＝ 嚴格序列，與原本行為一致）以 `asyncio.Semaphore`
  控制同時打 agent 的題數。>1 時 `question_done` 事件順序不再固定，但前端以 `question_pk` 索引，不受影響。

### 9.7 診斷 I/O 契約（如實作，對照 §6.9）
診斷器（假、真皆同）輸出**強制 JSON**：`overall_diagnosis`（白話總結）、`suspects[]`（每個含
`span_index / confidence(high|medium|low) / reason / evidence`，第一名排最前，前端預設跳它）、
`caveat`（可選，懷疑非單一 span 或非 skill 可控）。整包存進 `span_analyses.raw_llm_output`(JSONB)，
`caveat` 另存獨立欄，`model_used` 記下產生它的模型名。前端讀 DB 直接 render，不重跑（§6.12 快取）。

**真實實作額外做的三件事**（`real/diagnosis.py`）：
1. **input 依 §6.9 固定四段組裝**：system 語氣硬約束（「提供線索、不是判決；不確定就說不確定；
   可指多個可疑點」）→ 期望流程（整段 `ground_truth_reasoning`）→ 截斷後的 trace（每個 span 帶
   `index / tool_name / status / input / output`）→ judge 的 verdict 與 comment。
2. **餵給 LLM 的 trace 先套 §6.7 截斷**（見 §9.10）。
3. **`span_index` 對照實際送出的 span 驗證，越界的 suspect 直接丟棄**。前端會自動跳到
   `suspects[0].span_index`，LLM 幻覺一個 index 就會讓開發者跳到不存在的 span。
   `confidence` 不在 high/medium/low 之列時正規化為 medium。

**LLM 輸出解析**（`real/llm.py`，judge 與診斷共用）：要求 `response_format: json_object`
（相容性比 `json_schema` 高，很多 self-hosted 端點只支援前者；端點若整個拒絕就自動退回不帶該參數），
回來的內容以 Pydantic model 驗證。解析失敗會把模型自己的輸出與錯誤訊息回丟、**給它一次修復機會**；
再失敗就 raise（絕不默默塞一個預設值）。也會處理模型自作主張加上的 ```json code fence。

### 9.8 權限、登入、分享（如實作，對照 §6.16）——**含新增功能**
- **角色**：owner（全寫 + 讀 + 跑 run + re-diagnose）、viewer（讀 + 跑 run；不可寫、不可 re-diagnose）。
  以 `eval_set_roles`（subject→role）為準；guard 用 `require_owner` / `require_reader` 依賴。
- **樂觀鎖 409**：`questions`、`eval_sets` 各帶 `version`；`UPDATE ... WHERE id AND version` 未命中回
  **409**。衝突粒度=單列。已驗證兩人改同題→後者 409。
- **假登入**：`X-User-Subject` header（SSE 用 `?subject=`）或設定檔預設；UI 右上角下拉切換身分。
- **★ run config 的可見範圍**：`list_runs` 走 `require_reader`，所以**在該 eval set 有角色的人
  （含 viewer）都看得到底下所有 run 的非機密設定**（`RunOut.config`：base URL、模型、timeout、
  concurrency，以及 `langfuse_public_key`——它是 Basic auth 的識別碼那半，沒有 secret key 不能用，
  且讓它 round-trip 才能讓「沿用舊 run 設定」免重填）。**金鑰對任何人都不回傳，owner 也一樣**；
  只透過 `credentials_set` 顯示某個 slot 有沒有值。沒有角色的人連 run 列表都拿不到（403）。
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
- **★ 新增（配合真實整合，讓「看得到 eval 結果」成立）**
  - 中間欄最上方多一個 **Answer 區塊**：並排顯示 **agent 實際回答**（帶 correct/incorrect 標籤）
    與**期望答案**，下方是 judge 的 comment。接真 agent 後這是開發者第一個要看的東西——
    光有 verdict 說不出哪裡錯。
  - 左欄 failed 題目直接**紅字顯示 `error_message`**（滑過看完整內容），不再只是一個 `failed` 標籤。
  - 中間欄 failed 題目上方有一條錯誤橫幅；span 列若有 `status_message`（Langfuse ERROR level 的
    說明）也會一併顯示。
- **★ 新增（逐 run 設定，見 §9.15）**
  - 按「Run eval」先開 **`RunConfigDialog`**：run 名稱（預設當下日期時間）＋ agent / Langfuse /
    LLM 三區的端點、模型、timeout 與 concurrency，右下角才是送出。預填來自
    `GET /run-config/defaults`；`*_IMPL=fake` 的區塊會**變灰並標示不會生效**——否則填了半天
    卻跑出假資料是最容易踩的坑。頂端「Use config from」可挑舊 run 沿用設定。
  - 每個 run 列右側一顆按鈕開 **`RunConfigView`**（唯讀）：該 run 當初的九項設定，加上金鑰
    「有/無」。整個元件**沒有任何 input**，所以不會被誤認成可編輯表單。
  - **run 列整列可點**進入詳情（原本要按 "Open" 按鈕）；checkbox 與 config 按鈕各自
    `stopPropagation` 保留自己的行為，並補上 `role="button"` 與 Enter/Space 鍵盤支援。
- **★ 新增（回應「太像玩具」的回饋）**：整套現代化 CSS 設計系統（陰影/圓角/字級/焦點框）、卡片
  hover 浮起、清單進場、**對話框 pop-in 動畫**、進度條 shimmer、**Toast** 提示（存檔/衝突/錯誤）；
  **light/dark 主題切換**（右上角，首次進站前套用避免閃爍，存 localStorage，預設跟隨作業系統）；
  **每張 card 的 config 齒輪**（見 §9.8）。
- **★ 新增（清單規模，見 §9.18(e)）**
  - **首頁卡片與 run 歷史都分頁**：捲到底自動追加下一頁（`IntersectionObserver`），並固定附一顆
    **Load more** 按鈕與「Showing N of M」計數。按鈕不是裝飾——鍵盤操作不會觸發 observer，
    頁面本身不捲動時也永遠不會觸發；計數則回答「還值不值得繼續捲」，只有轉圈圈是答不出來的。
  - 兩份清單共用 `usePagedList.js`：**追加時以 id 去重**、**丟棄過期回應**（改了篩選條件後，
    舊請求可能後到）、**擋掉重複的併發載入**（捲動哨兵會連續觸發）。`refresh()` 只重讀目前
    已顯示的範圍，所以刪一筆資料不會把捲到一半的清單縮回第一頁。
  - **首頁工具列**：名稱搜尋（debounce 250ms）＋ metadata key/value 篩選 ＋ 排序（最新 / 名稱）。
    全部送到後端做，**跨所有分頁生效**——只篩已載入的那一頁，結果會取決於使用者捲了多遠。
  - 進場動畫的 stagger **只在頁內計算**（`i % PAGE_SIZE`），否則每次追加都會讓整個清單重新閃一次。
- **★ 新增（三欄詳情的即時更新，見 §9.18(a)）**——這是本 UI 最容易被誤解的一段，特別說明：
  - **三欄全部跟著 run 的 SSE 走**，不只左欄。開啟中的那一題是**用 id 記住、每次 render 從
    `results` 重新查**的（而不是點擊當下複製一份），所以它永遠是最新狀態。
  - 中欄與右欄的內容全部來自 `GET .../trace` 這一包 payload。它會在**指紋**
    （`phase|verdict|trace_ready|has_analysis`）改變時重抓——由既有的 SSE 事件就地更新，
    **事件驅動，不是輪詢**。因此 agent 的回答、judge 的 verdict、診斷都會當場出現，不需要
    退出去再進來。
  - **重抓時不清空畫面**，只在標題列顯示一個小圓點；只有換題才清空。
  - **不搶走開發者手動選的 span**：只有換題、或診斷第一次出現時才自動跳到 `suspects[0]`。
    每次刷新都跳的話，正在讀某個 span 的人會被硬拉走。
  - 手動 Retry 與 re-diagnose 走一個獨立的 nonce（重新產生的診斷不會改變 `has_analysis`，
    光靠指紋看不出來）。
- **★ 新增（span payload 結構化渲染 + 中欄分區，接上真實 Langfuse 之後）**
  - **右欄不再是兩塊 JSON dump。** Langfuse 存的是 agent SDK 交給它的東西，沒有 schema 可驗；
    但一個 LLM generation 實務上就是 chat-completions 的請求／回應——進去是
    `{"tools": [...], "messages": [...]}`，出來是一則 assistant message。`SpanPayload.jsx`
    照這個形狀渲染：tools 一個可收合區塊（每個 tool 再收合，顯示 name / description /
    parameters），messages 每則一個可收合列，列頭是 **role 色籤**（system / user / assistant /
    tool）＋一行摘要（該則的開頭，或 `→ tool_name()`）＋字數。assistant 的 `tool_calls` 另外
    以工具名＋重新縮排後的 arguments 呈現（OpenAI 是把 arguments 塞成 JSON 字串）。
  - **兩條規則**：(1) **認得就渲染，認不得也要能看**——每個分支都 fallback 到 pretty-print
    JSON，不認得的 payload 不會炸掉也不會消失；(2) **收合，不切斷**。每個 Input / Output
    區塊右上角有 **Pretty | JSON** 切換，JSON 模式是完整未截斷的原始 payload，這是規則 (1)
    的保險。
  - **預設展開狀態**：tools 收起、所有 message 收起、**只展開最後一則**與 Output。最後一則
    是這個 span 真正在講的事，其餘是需要時才追溯的脈絡。
  - 後端配合：`Span` 多了 `input_json` / `output_json`（trace store 原本的物件），
    `SpanOut.input/output` 型別放寬成物件或字串；`observation_to_span` 連「被 agent 序列化成
    JSON 字串」的 payload 也會 parse 回來。假層 `build_fake_trace` 也改成同樣的 chat 形狀，
    所以純 Docker 的 demo 就能驗證這條渲染路徑。
  - **中間欄改成三個具名分區**：**Answer**（agent 回答＋verdict／期望答案／judge comment）、
    **Diagnosis**、**Trace · n spans**。四種互不相干的內容擠在同一條捲軸裡，沒有分界就是一片
    文字牆。trace 狀態橫幅（generating / error / not_started / no_trace）也一併移進 Trace 分區
    ——它們講的是下面那份 span 列表，不是答案或診斷。

### 9.10 截斷 / 快取 / 進度（如實作）
- **§6.7 截斷：只剩「餵給診斷 LLM 前」這一處**（`real/diagnosis.py`，門檻
  `SPAN_BODY_MAX_CHARS`，預設 800）。只截**單一 span 過長的 input/output body**（保留頭尾、
  中間省略），**絕不砍 span**——這是為了 context window，是硬限制。
  **檢視路徑（`GET .../trace`）已不再截斷**：截斷會把開發者點開 span 想看的證據本身砍掉，
  而且會讓 JSON 變成無法 parse 的碎片。長度改由 UI 用「收合」處理（見 §9.9 的 span payload
  渲染），不是用「切掉」。
- **§6.12 快取**：診斷在 run 當下（trace ready 後）生成並落庫；事後點開**直接讀 DB**；唯一重算入口是
  owner 手動 re-diagnose。
- **§6.12 非同步 ingestion**：抓 trace 前 poll + 指數退避。**參數在 `config.py`
  （`TRACE_POLL_BACKOFF_S` 預設 `[0.5,1,2,4,8]`、`TRACE_POLL_MAX_ATTEMPTS` 預設 8），不在
  `fake_config.py`**——它同時管真實 Langfuse ingestion 的等待，而真實 ingestion 比假層慢一個數量級。
  兩個 request 路徑（看 trace、re-diagnose）用同一個上限但**短 sleep**，因為它們跑在 request 裡，
  不能佔用 orchestrator 那種長退避；抓不到就回 `generating` / 409 讓使用者重試。
- **進度**：SSE（`sse.py` 的 in-memory pub/sub，單程序 POC）。事件型別：

  | 事件 | 何時送出 | 用途 |
  |---|---|---|
  | `snapshot` | 訂閱當下 | 晚加入的訂閱者補當前狀態（`total`/`done`/`correct`/`status`）|
  | `run_started` | 所有 result 列建好後 | 帶 `total` |
  | `question_started` | 開始打 agent 前 | 左欄轉灰（`pending`）|
  | `question_answered` | agent 回答後 | 左欄轉白（`answered`，「judging…」）|
  | **`question_judged`** | 判分寫入後 | 左欄轉綠/紅。**不能等到最後**——後面的 trace poll 與診斷在真實服務下要跑數十秒（§9.18(a)）|
  | **`question_traced`** | trace poll 結束後 | `trace_ready` / `trace_error` 定案 |
  | `question_done` | 該題全部完成 | 帶 `has_analysis`（診斷此時才寫完）|
  | `run_completed` | run 結束 | 含 `status`，可能是 `cancelled` / `failed` |
  | `ping` | 15 秒無事件 | 保持連線 |

  五個 `question_*` 事件的 payload 相同：`question_pk / phase / verdict / status /
  error_message / trace_ready / has_analysis / trace_error / diagnosis_error /
  done / total / correct`。前三個欄位是左欄「灰 → 白 → 綠/紅」的來源（§9.17(c)）；
  `phase`、`verdict`、`trace_ready`、`has_analysis` 四個合起來是中欄重抓 trace 的
  **指紋**（§9.18(a)）——這是三欄詳情能即時更新的機制。

### 9.11 seed 假資料（`python -m app.seed`）
一個 eval set「Billing Agent Regression Suite」，角色 alice=owner、bob=viewer、carol=viewer（示範分享）；
5 題、3 個 run，通過率 0.8→0.6→0.4（可見的退步趨勢），使三種 incorrect mode 明顯不同
（union={Q2,Q3,Q5} / intersection={Q2} / last-2={Q2,Q3}）；含一題帶 **caveat** 的診斷、
一題 trace 「生成中」狀態、以及一個超長 span body 觸發 §6.7 截斷。

> seed 依賴假層（它直接呼叫 `build_fake_trace` 產生 trace，並在題目文字裡埋 §9.2 的標記），
> 所以 seed 出來的資料是給 **fake 模式**的 demo 用的。真實模式下請自行上傳 eval set 再 run。

### 9.12 與本設計文件（§1–§8）的差異總表
| 主題 | 原文件說 | 實作現況 |
|---|---|---|
| 上傳格式 | CSV + JSONL 皆定義 | **JSONL + CSV 檔案上傳**（前端解析成可編輯表格，送出前再序列化為 JSONL；後端仍 JSONL-only）|
| metadata keys | 需 `eval_set_metadata_keys` 表 | **未建表**，單一 JSONB + 掃描取 key |
| metadata 篩選/排序 | 首頁可依 key 篩選/排序 | **已做**（§9.18(e)）：名稱搜尋 + metadata key/value 篩選 + 排序，全部在 SQL 完成，跨所有分頁生效 |
| 授權 | 「統一 middleware」 | 以 **FastAPI 依賴**（require_owner/reader）實作，效果等同 |
| 登入 | key-lock service token | **假登入**（header/query/設定檔 + UI 切換）|
| 上傳介面 | 未細談（§6.10 只談 card 欄位） | **檔案上傳 + 可編輯預覽表格**（§9.8b）：無手貼文字框，送出前可逐格修改與增刪列 |
| 分享 | 只提「可多 owner」 | **新增完整分享 UI/API**：上傳時指定分享對象、card config 改分享名單、`PUT /roles`；**對象一律直接輸入人名**（無預設名單下拉）|
| Langfuse | 讀 trace / 寫 dataset+score | **讀已實作**（`TRACE_IMPL=real`）：依 correlation_id 取回 observation 並重建 span 列表，**兩條端點策略依序嘗試**以繞開自架版的 `events` 表問題（§9.18(b)）；**寫 dataset / score 尚未做**（§6.3 的 score 回寫留待之後）|
| UI 外觀/主題 | 未提 | **新增**現代化設計系統、動畫、Toast、**light/dark 主題** |
| 逐題 regression | 標記為 Stage 1.5 | 首頁 card 的 regression 摘要與三 mode **皆已做** |
| Agent 通訊協定 | §1.1/§6.2 設想的是 Google A2A(Agent-to-Agent) protocol server | **agent server 端後來改為單一 FastAPI `POST /execute`**（`{"message","metadata"}` → `{"content"}`），本平台的 `AgentClient` 也隨之從手寫 A2A JSON-RPC client 換成 `real/agent.py` 的 HTTP client；correlation 機制不變——`metadata.trace_data.trace_id`(=`session_id`) 走 correlation_id，另加 `user_id`(觸發 run 的使用者) 與 `tags`(`["eval_<eval_set 名稱>"]`)|
| LLM judge | §6.7 標明「prompt 與二元化門檻留待之後」 | **已定案並實作**：LLM 同時吐 `verdict + score + comment`；另有可選的 `JUDGE_SCORE_THRESHOLD` 由分數推導 verdict，調門檻不用改 prompt |
| judge 介面 | `judge(response, ground_truth)` | **多了 `question` 參數**——真 LLM judge 需要題目本身當 context |
| 診斷 LLM | §6.9 定案 I/O 契約 | **已實作**（`DIAGNOSIS_IMPL=real`），並加上輸出驗證與 `span_index` 越界剔除 |
| 部署形態 | 未提（只提 docker-compose 起 Postgres）| **db / backend / frontend 各一個 container**；backend 依賴用 uv、frontend 用 pnpm |
| 錯誤處理 | 未提 | orchestrator 有完整失敗策略（§9.6），run 不會卡在 `running` |
| 連線設定的作用域 | 未提（隱含是部署層級的環境變數）| **改為逐 run**：觸發時用 dialog 設定並寫入 `runs.config`/`runs.secrets`；`*_IMPL` 仍是全域主開關。金鑰只進不出，沿用舊 run 由後端複製且與端點綁定（§9.15）|
| 測試 | 未提 | **127 個測試**：116 個不需 DB 或網路（respx mock），11 個分頁測試需真 Postgres 且未設 `TEST_DATABASE_URL` 時 skip |
| 清單規模 | 未提 | **兩個清單皆分頁**（`{items,total,has_more}` + 無限捲動），且卡片與 run 列表的查詢數**與頁面大小無關**（§9.18(e)）|
| 三欄詳情的即時性 | 未提（§6.15 只說 run 進度要即時推送）| **三欄都跟著 SSE 更新**：開啟中的題目以指紋觸發重抓 trace，agent 回答 / verdict / 診斷都當場出現（§9.18(a)）|

### 9.13 如何執行
- 一鍵：`SEED=1 ./scripts/dev.sh`（build image → 起 Postgres → migrate → seed → 起 backend:8000 +
  frontend:5173）。或用 `make` 分項（`make db/build/migrate/seed/backend/frontend/test/preflight`）。
- **db、backend、frontend 各自是一個 container**，host 只需要 docker（含 compose）——不需要
  host 的 Python venv 或 node_modules。backend image 釘 CPython 3.12，依賴以 **uv** 安裝；
  frontend 依賴以 **pnpm** 安裝。
- 接真實整合的設定與逐一啟用步驟見 `README.md` 的「Going from fake to real」。
- 詳見 repo 根目錄 `README.md`。

### 9.14 明確「尚未做」

**維持 Stage 2/3 邊界（刻意不做）**
per-span 機率/熱點、人工重標 span、SkillOpt、per-request skill override 重跑、寫回 agent server、
多租戶隔離、編輯的即時讀同步（目前靠重載/切換身分刷新）。

**Stage 1 範圍內但確實還缺的**
- ~~**run 無法取消**~~ → **已補**，見 §9.17。
- **Langfuse 只讀不寫**：§6.3 說 verdict 應同時寫成 Langfuse Score（`source=API`），**尚未做**。
  目前 app DB 是唯一真相，Langfuse UI 上看不到本平台判的分數。
- **span tree 不重建**：Langfuse 回傳的 `parentObservationId` **完全未使用**，Stage 1 以
  **依 startTime 排序的平舖列表**呈現；樹狀結構留給 Stage 2 的熱點檢視。
- ~~**metadata 篩選/排序**~~ → **已補**，見 §9.18(e)：首頁的名稱搜尋與 metadata key/value
  篩選、排序皆在 SQL 中完成，跨全部分頁生效。
- **`LLM_TIMEOUT_S` 沒有逐 run 版本**：`AGENT_TIMEOUT_S` 與 `LANGFUSE_TIMEOUT_S` 都能在 run
  config dialog 逐次調整，唯獨 LLM 的 timeout 仍是全域設定（`build_seams` 呼叫
  `get_client_for` 時沒有傳 `timeout_s`），dialog 上也沒有這個欄位。判斷 judge/diagnosis
  模型太慢時只能改 env 重啟。補法很小：`RunConfig` 加欄位、`run_config.defaults()` 加一行、
  往 `get_client_for` 傳進去、dialog 加一格。
- **run config 無法比對**：唯讀檢視一次只能看一個 run；要並排 diff 兩個 run 的設定還得自己切換。

（CSV 上傳已補上——見 §9.1；惟後端仍以 JSONL 為單一寫入契約，CSV 於前端解析。）

---

### 9.15 設定總表（環境變數）

全部由 `backend/app/config.py`（pydantic-settings）讀取，`docker-compose.yml` 會把它們透傳進
backend container。金鑰只走環境變數或 repo 根目錄的 `.env`，**不會進 image**。
完整說明見 `backend/.env.example`。

| 變數 | 預設 | 說明 |
|---|---|---|
| `DATABASE_URL` / `SYNC_DATABASE_URL` | 指向 compose 的 `db` | app 用 asyncpg、Alembic 用 psycopg |
| `FAKE_USER_SUBJECT` | `alice` | 假登入的預設身分（§6.16）|
| `KNOWN_USERS` | `["alice","bob","carol","dave"]` | `GET /users` 回傳的假使用者名單（右上角切換身分用）|
| `ERROR_MESSAGE_MAX_CHARS` | `2000` | 落庫的錯誤訊息長度上限（`error_message` / `trace_error` / `diagnosis_error`）|
| `FRONTEND_ORIGIN` | `http://localhost:5173` | CORS 來源 |
| `SPAN_BODY_MAX_CHARS` | `800` | §6.7 單一 span body 截斷門檻 |
| **`AGENT_IMPL` / `JUDGE_IMPL` / `TRACE_IMPL` / `DIAGNOSIS_IMPL`** | 皆 `fake` | 每個 seam 各自 `fake` 或 `real`，**可逐一切換** |
| `AGENT_BASE_URL` | 空 | agent server 的 base URL；client 會打 `{base}/execute` |
| `AGENT_TIMEOUT_S` / `AGENT_MAX_RETRIES` | `120` / `2` | 單次呼叫上限、暫時性錯誤重試次數 |
| `LLM_BASE_URL` / `LLM_API_KEY` | `http://litellm-ai4bi.cpoap-dev.dev.tsmc.com` / 空 | **OpenAI 相容**端點（可指向 self-hosted）|
| `JUDGE_MODEL` / `DIAGNOSIS_MODEL` | 皆 `Qwen3.6-27B` | 兩個用途可用不同模型 |
| `LLM_TIMEOUT_S` / `LLM_MAX_RETRIES` | `120` / `2` | |
| `JUDGE_SCORE_THRESHOLD` | 空（＝採信 LLM 的 verdict）| 設 0–1 數字則改由分數推導 verdict |
| `LANGFUSE_HOST` | `http://langfuse-ai4bi.cpoap-dev.dev.tsmc.com` | |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | 空 | HTTP Basic auth |
| `LANGFUSE_TIMEOUT_S` | `60` | |
| `LANGFUSE_OBSERVATION_TYPES` | `["GENERATION","SPAN"]` | 其餘型別（如 `EVENT`）不進 span 列表 |
| **`LANGFUSE_TRACE_READ_STRATEGY`** | `auto` | `auto`(兩個端點依序試) / `trace_api` / `observations_api`（§9.18）|
| `RUN_CONCURRENCY` | `1` | 1 ＝ 嚴格序列（原行為）；調高可併發打 agent |
| `TRACE_POLL_BACKOFF_S` / `TRACE_POLL_MAX_ATTEMPTS` | `[0.5,1,2,4,8]` / `8` | §6.12 ingestion 等待 |

**這些連線設定是「預設值」，不是唯一來源**：`*_IMPL` 仍是 fake / real 的主開關，但按下
「Run eval」會先跳出 config dialog，用上表的值預填，並允許逐次修改 run 名稱、agent base URL /
timeout、Langfuse host / 金鑰 / timeout、LLM base URL / 金鑰、judge 與 diagnosis 模型，以及
**concurrency（一次送幾題給 agent）**。每個 run 會把自己實際用的設定存進 `runs.config`
（非機密）與 `runs.secrets`（金鑰），因此兩個 run 可以打不同的 agent server 或用不同的
judge model，而且事後看 trace / re-diagnose 時會沿用該 run 當初的端點。dialog 中留白的欄位
就退回上表的環境變數值，所以 seeded fake demo 仍可空表單直接跑。

**留白欄位在觸發當下就會被寫死**（`services/run_config.py` 的 `resolve()`）：`runs.config`
存的是「有效值」而非「使用者改過的差異」，因此每個 run 的設定都是完整、可事後判讀的紀錄——
否則一個空欄位事後無從分辨是「當初用了 env 的值」還是「根本沒設」，而今天的 env 也無法
作證當初的內容。每個 run 列都有一顆按鈕可開啟**唯讀**的 config 檢視（`RunConfigView.jsx`）；
`0003_run_config` 之前建立的舊 run `config` 是 `{}`，該畫面會直接說明它早於此功能，而不是
編造數值。金鑰只顯示「有/無」（`RunOut.credentials_set` 僅回傳 slot 名稱 `llm` / `langfuse`）。

金鑰**只進不出**：`runs.secrets` 不會被任何 response model 序列化（`list_runs` 對 viewer 也開放，
見 §6.16）。要沿用舊 run 的金鑰時，前端只送 `reuse_secrets_from_run_id`，由後端 server-side 複製；
且金鑰與其端點綁定——若 `llm_base_url` / `langfuse_host` 被改掉，對應金鑰就不會被沿用，必須重新輸入。

**前置檢查**：`make preflight`（＝`python -m app.check_integrations`）會逐一 ping 設為 `real`
的 seam，回報每個 OK / FAIL 與原因。設定打錯時，這比跑一次 eval 才發現快得多。

**建議的逐一帶起順序**（每一步都可以獨立驗證，壞掉時範圍很小）：

| 步驟 | 設定 | 這一步該看到什麼 |
|---|---|---|
| 1 | `AGENT_IMPL=real` + `AGENT_BASE_URL` | 題目打得到真 agent；`agent_response` 有真實回答（判分仍是假的）|
| 2 | 加 `JUDGE_IMPL=real` + `LLM_BASE_URL` / `JUDGE_MODEL` | 通過率與 judge comment 開始有意義 |
| 3 | 加 `TRACE_IMPL=real` + `LANGFUSE_*` | 點錯題看得到真實 span（**前提是 agent server 已套用 correlation_id**）|
| 4 | 加 `DIAGNOSIS_IMPL=real` + `DIAGNOSIS_MODEL` | 診斷、caveat、可疑 span 都由真 LLM 產生 |

**接真實的前提（§6.2，repo 外的相依）**：agent server 必須讀 `/execute` request body 的
`metadata.trace_data.trace_id` 並用它當 Langfuse trace id，否則平台無從找回自己剛觸發的
trace。

---

### 9.16 測試與驗證現況

**單元測試**（`backend/tests/`，共 **127 個**，9 個檔案）

`make test` 跑其中 **116 個**——不需 DB 也不需網路，外部呼叫一律以 `respx` mock。剩下 11 個
（`test_pagination.py`）需要一個真 Postgres，未設 `TEST_DATABASE_URL` 時整個檔案自動 skip，
所以 `make test` 的「零外部依賴」承諾沒有被打破：

```bash
createdb agenteval_test
TEST_DATABASE_URL='postgresql+asyncpg://localhost/agenteval_test' pytest tests/test_pagination.py
```
- `test_agent_client.py`（13）：request body 的 `message` + `metadata.trace_data`（trace_id=session_id、
  user_id、tags）、`{"content": str}` 回應解析（含裸 JSON 字串與純文字兩種容錯 fallback）、
  非字串/缺 `content` 視為失敗、空回答視為失敗、307 redirect 會被 follow 而非誤判為空回應
  （實測中撞到過：server 端路由是 `/execute/` 帶尾斜線時常見的 trailing-slash 307）、
  5xx raise（交給重試）vs 4xx 直接失敗、逐 run 的 base URL / timeout 覆寫環境變數。
- `test_langfuse_client.py`（24）：空頁→`NotReady`、時間排序與重新編號、observation 型別過濾、
  分頁、`traceId` 與 Basic auth、`usageDetails` 與舊版 `usage` 兩種 token 欄位、ERROR level 映射；
  401 / 連線失敗 → `TraceFetchError` 且訊息含 host 與狀態碼、過長的錯誤 body 會被截斷；
  以及 §9.18(b) 的**兩條讀取策略**：trace API 與列表端點對映出的 span 完全相同、404 → `NotReady`
  而非失敗、`auto` 命中第一條時不會多打第二條、第一條壞掉時會 fallback、全失敗時**兩條的原因都在
  錯誤訊息裡**（fallback 不能把主要路徑的失敗藏起來）。
- `test_judge_and_diagnosis.py`（18）：verdict 正規化與非法值、門檻覆寫兩個方向、§6.7 截斷保留所有 span、
  越界 `span_index` 剔除、§6.9 四段 prompt 的順序、JSON 修復重試（成功與放棄各一）。
- `test_orchestrator.py`（18）：agent 例外只讓該題失敗而 run 仍完成、agent 自報失敗保留原因、
  judge 失敗**不**被當成 correct、診斷失敗不影響 verdict 且原因落庫、trace store 出錯不讓題目失敗
  且原因落庫、非預期例外把 run 收成 failed 並送出 SSE 終止事件、重試次數上限、併發；
  以及 §9.17 的四項：第一次呼叫 agent 前所有 result 列就已建好、中止前未開始的題目留 `pending`、
  中止會**放棄進行中的 agent 呼叫**（以 `asyncio.wait_for` 逾時當斷言）、已判分的結果在中止後保留；
  以及 §9.18(a) 的三項：一題走完會依序送出 `question_started/answered/judged/traced/done` 五個事件、
  每個事件都帶齊中欄重抓 trace 所需的指紋欄位、診斷失敗的原因會出現在 `question_done` 上。
- `test_deletion.py`（5）：`delete_run` / `delete_eval_set` 的 DELETE 語句**順序**（子表先於父表，
  特別是 `question_results` 必須早於 `questions`），以及一個「schema 新增子表卻忘了加進刪除順序」
  的守門測試。
- `test_run_lifecycle.py`（11）：cancel 的權限矩陣（owner ✓ / 觸發者 ✓ / 其他 viewer ✗）、
  非 running 回 409、跨 eval set 回 404；delete run / delete eval set 為 owner-only 且
  running 時回 409。
- `test_run_config.py`（19）：`build_seams` 空設定等同純環境變數行為、`*_IMPL` 仍是主開關
  （設定填了真端點也不會把 fake seam 變 real）、逐 run 值覆寫 env、空白欄位退回 env、
  judge 與 diagnosis 共用同一個 LLM client；`resolve()` 把留白欄位寫死成 env 值且
  `defaults()` 涵蓋每個欄位；金鑰沿用的端點配對規則（端點沒變才複製、變了就丟、
  手動輸入優先、跨 eval set 一律 404）；以及**金鑰不外流的值層級斷言**——
  序列化一個帶哨兵金鑰的 `RunOut`，斷言兩個哨兵值都不出現在 payload 任何位置
  （比檢查欄位名稱可靠，因為 `credentials_set` 讓 router 合法地讀到 `runs.secrets`）。
- `test_results.py`（8）：trace 檢視的狀態機。核心是 §9.18(c) 的迴歸測試——**`pending` 的題目回
  `not_started` 且對 trace store 發出零個請求**（用一個會記錄呼叫次數的 stub 斷言）；即使該列上
  留著上一次的 `trace_error` 也不會顯示。另有 `answered` 之後才會去抓、trace 就緒回 spans、
  `failed` 回 `no_trace` 不發請求、trace store 失敗回 `error` 而非 `generating`。
- `test_pagination.py`（11，**需要 DB**）：`limit`/`offset`/`total`/`has_more`、翻完所有頁
  **每張卡剛好出現一次**（不重複也不遺漏）、只列出呼叫者有權限的 set、名稱搜尋與 metadata
  key/value 篩選在 SQL 生效（`total` 反映全部符合者而非該頁）、依名稱排序、趨勢線受
  `TREND_RUNS` 上限、regression 用最新兩個 run 算。**最重要的兩個是查詢數守門測試**：
  `GET /eval-sets` 與 `GET /runs` 在 `limit=1` 與 `limit=20` 時發出的查詢數必須**完全相同**。
  斷言相同而非「相近」，是因為聚合都是整頁一次算完的，查詢數是程式的性質、與資料無關；
  任何人不小心加回一個 per-set 查詢都會立刻被抓到。斷言時間會 flaky，斷言查詢數不會。

**已驗證的端到端行為**
- **fake 模式**：與真實整合加入前**行為完全相同**（卡片 3 runs / 趨勢 0.8→0.6→0.4、
  三種 incorrect mode 各異、SSE 五題含 `⟦timeout⟧` 部分完成、診斷與 caveat、
  403/409、上傳與 version bump、三層 UI）。
- **real 模式**：以**自建 mock 的 agent HTTP / OpenAI 相容 / Langfuse 服務**跑過完整流程——
  上傳 → run → 真回答與判分落庫、correlation_id 從 agent metadata 一路對回 Langfuse `traceId`、
  NotReady 退避路徑、observation→span 映射、幻覺 `span_index` 剔除、
  以及「故意打壞 agent `/execute` endpoint → 每題帶原因失敗、run 仍正常收斂」。
  ⚠️ 這批 real 模式驗證是 agent server 端還是 A2A JSON-RPC 協定時做的；agent server 改成
  `POST /execute` 這個純 HTTP 契約後，尚未針對新契約重跑一次完整的 real-模式端到端驗證
  （單元測試 `test_agent_client.py` 已針對新契約重寫並通過，但 mock 端到端流程還沒有）。
  ⚠️ **逐 run 設定（§9.15）同樣只有單元測試層級的驗證**：`build_seams` / `resolve()` /
  金鑰配對 / 金鑰不外流都有測試涵蓋，前端也 build 過，但「開 dialog → 跑 run → 開唯讀
  檢視 → 用『Use config from』再跑一次 → 事後看 trace」這條完整 UI 動線尚未實跑。
  `0003_run_config` 的 migration 只用 alembic offline（`--sql`）確認過產出的 DDL 正確。

> ⚠️ **agent / judge / diagnosis 三個 seam 尚未對接真正的服務**。上述 real 模式驗證用的是
> mock，能證明 correlation 環路、失敗策略與資料流都正確；**證明不了**貴方 agent server 的
> `/execute` 是否真的回 `{"content": str}`、貴方 LLM 端點是否支援
> `response_format: json_object`。兩處在 client 內都刻意寫得寬容（`/execute` 回應接受裸 JSON
> 字串或純文字當 fallback、`response_format` 被拒會自動退回），但真的接上去仍可能需要微調。
>
> **Langfuse 不在此列**：真環境已對接，trace 讀得回來，token 欄位命名（`usageDetails` 與舊版
> `usage`）兩種都已處理。見 §9.19。

---

### 9.17 Run lifecycle 與錯誤可見性（後續補強）

> 本節是 §9.1–§9.16 之後的一次補強，對應 §9.14 列出的「run 無法取消」，以及一個更根本的問題：
> **接真實服務後，外部依賴壞掉時 UI 什麼都看不出來**。四個 seam 的 real 實作（agent / judge /
> Langfuse / diagnosis）**維持原樣、`*_IMPL` 也仍預設 `fake`**——這次補的是它們失敗時的可見性。

#### (a) 中止 run
- **持久旗標 + in-process 事件**：`runs.cancel_requested`（`0004_run_lifecycle`）是耐久的真相，
  給 UI 讀、也撐得過重啟；`app/cancellation.py` 的 `asyncio.Event` 則是「立刻」的機制。
  只查 DB 旗標的話，正在 `await` 真 agent 的那一題最久要等 `AGENT_TIMEOUT_S`（預設 120s）才會停，
  而停止鈕存在的理由正是那種時候。
- **`_await_or_cancel`**：agent 與 judge 呼叫都與 cancel event 賽跑（`FIRST_COMPLETED`），
  event 先到就 `task.cancel()`。實測按下中止到 run 變 `cancelled` 約 **44ms**（該題的 fake agent
  當時還在 1–3s 的睡眠中）。
- **狀態語意**：進行中的那題 → `status='cancelled'` + 原因；尚未開始的題目 → 留 `pending`
  （run 因此誠實地讀作「停在 N/M」）；**已判分的題目結果一律保留**（成本已經付出去了），
  只跳過 trace poll 與診斷。
- **`pass_rate` 留 `None`**：半個 run 的通過率會拖低首頁 card 的趨勢線，而原因與 agent 無關；
  `failed` run 本來就是這樣處理。
- **權限**：owner 可中止任何 run；**viewer 可中止自己觸發的 run**（§6.16 允許 viewer 觸發 run，
  能開就必須能關）。這條規則兩個既有 guard 都表達不了，故 `auth.role_for` 由私有改為公開。

#### (b) 刪除 eval set / run
- `DELETE /eval-sets/{id}` 與 `DELETE /eval-sets/{id}/runs/{run_id}`，皆 **owner-only**（§6.16），
  UI 上都有確認對話框，並寫明會連帶刪掉幾個 run。
- **執行中的 run 不能刪**（409）：orchestrator 還在寫那些列。要刪先中止——這正是停止鈕的用途。
- **刪除順序收在 `services/deletion.py`**：`question_results -> questions` 這條 FK 沒有 CASCADE，
  而 Postgres 不保證 cascade 會先刪 `question_results` 再刪 `questions`，所以子表一律顯式地由深往淺刪。
  `seed.py` 原本內嵌同一份順序，現已改為呼叫它。

#### (c) Run 執行中的完整 question list
- orchestrator 在跑第一題**之前**就把整份快照的 `question_results` 全部建好（`status='pending'`）。
  原本是逐題建立，於是慢 agent 執行時左欄會「一題一題冒出來」，看起來像這個 eval set 只有一題。
  附帶好處：SSE `snapshot` 的 `total` 從第一秒起就是對的。
- **`phase`** 由 `services/aggregation.result_phase(status, agent_response, verdict)` 推導（不落庫）：
  `pending`（灰、pulse）→ `answered`（白底、空心點，「judging…」）→ `judged`（綠/紅）；另有
  `failed` / `cancelled`。REST 與 SSE 共用同一個函式，兩邊的顏色不可能對不上。
- 新增 SSE 事件 `question_started` / `question_answered`（§9.10），前端就地更新該列、不整份重抓。
- **前端**：按下 Run eval 後**直接進入該 run 的詳情頁**；三欄之上新增 `RunStatusBar.jsx`
  （堆疊長條 + 「x/y judged (n%) · x/y answered (n%) · z not started (n%)」+ 中止鈕）。
  run history 頁的進度條改為由 run 列表驅動（狀態為 `running` 的都畫），中途離開再回來仍看得到。

#### (d) Question list 的篩選 UI
原本標題列是一個 `float: right` 的原生 checkbox「only wrong」。改為既有的 `.segmented` 分段控制項
`All (n) | Wrong (n)`：附上計數才說得出「被藏起來的是什麼」，而不只是「怎麼取消隱藏」。

#### (e) Trace / diagnosis 的錯誤可見性（本次的核心）
病灶有兩處，兩處都把「設定壞掉」壓成了「還在 ingest」：
1. `routers/results.py` 在 `trace_ready=false` 時**根本不去抓** trace，於是永遠回 `generating`。
2. `_resolve_trace_spans` 的 `except Exception: return None` 把 host 打錯 / 401 / 逾時全吞掉。

補法：
- `integrations/base.py` 新增 **`TraceFetchError`**，與 `NotReady` 明確區分。`real/langfuse.py`
  把 `httpx` 錯誤包成它，訊息帶 **host + HTTP 狀態碼 + response body 前 200 字**——401 這種情況，
  body 才講得出到底哪裡錯。
- `TraceView` 新增狀態 **`error`** 與欄位 **`trace_error`** / **`diagnosis_error`**。
  `trace_ready=false` 時**照樣嘗試抓一次**（那個旗標只記錄 run 當下的結果，不重試等於讓設定錯誤
  永遠顯示 generating）；`build_seams` 也包了 try——`TRACE_IMPL=real` 但沒設金鑰原本會是 **500**。
- orchestrator 把 trace poll 的最後一次錯誤寫進 `question_results.trace_error`，
  診斷失敗寫進 `question_results.diagnosis_error`（原本只有 `log.warning`，UI 上完全看不出
  「模型掛了」與「根本沒送去診斷」的差別）。
- `re-diagnose` 的 409 會帶上 trace 錯誤；診斷模型失敗回 **502 + 模型自己的錯誤訊息**，不再吞成 500。
- 前端 `SpanList.jsx`：`error` 狀態顯示紅色 banner + 訊息 + **Retry**；`generating` 若有
  run 當下的失敗原因也一併附註；診斷失敗有自己的 banner 與 Re-diagnose 按鈕。

> **fake demo 的連帶調整**：既然檢視路徑現在會重試而非相信 `trace_ready`，seed 那題用來示範
> 「generating」的題目就不再停得住了。`FakeTraceClient` 因此認得 correlation_id 中的
> `notready` 標記（同 §9.2 的標記風格），seed 對該題改用帶標記的 correlation_id。

#### (f) 本次的驗證方式（與 §9.16 不同，這次有真的跑起來）
- 後端單元測試全數通過（該次新增 23 個，當時總計 95 個；目前總數見 §9.16）。
- **真 Postgres**（本機 16）跑完 `alembic upgrade head`（含 `0004`）、`python -m app.seed`
  **重複執行**（等於在真 DB 上驗證刪除順序）、以及完整的 API 動線：run 觸發後 5 題立刻全部
  `pending`、phase 依序推進、中止 44ms 生效、權限矩陣（owner / 觸發者 / 其他 viewer）、
  刪除的 403/409/204、SSE 事件序列。
- **真瀏覽器**（Playwright + Chromium）走過首頁 → run 歷史 → 詳情三層：卡片垃圾桶、確認對話框、
  自動跳轉詳情頁、5 題全灰起跑、狀態列百分比、中止、All/Wrong 分段篩選、viewer 看不到破壞性按鈕，
  以及把 `LANGFUSE_HOST` 指到不存在的 host 後，中欄確實顯示紅色錯誤 banner 與真實原因。
  過程中無任何 console / page error。
- ⚠️ 當時仍**未對接真正的外部服務**：Langfuse 錯誤路徑是用不存在的 host 與空金鑰驗的，
  成功路徑只有 mock。**Langfuse 這一項之後已解除**（§9.19）。

---

### 9.18 首次接上真實 Langfuse 後的五項修正

> 本節是 §9.17 之後的一次修正，起因是**第一次把 `TRACE_IMPL` 指到真實 Langfuse 去跑**時暴露的四個
> 缺陷，外加一個 POC 一直沒處理的規模問題。四個 seam 的 real 實作除了 Langfuse 讀取（(b)）之外
> **維持原樣**，`*_IMPL` 也仍預設 `fake`。

#### (a) 三欄詳情不會即時更新（最影響體感的一個）

**症狀**：run 執行中點進一題，左欄的小字從 `waiting` → `judging…` → `correct` 一路變化，但
**中欄的 Agent Answer 一直是空的，judge 結果也不出現**；退回上一頁再進來，全部資訊就都在了。

**病灶三處，全在 `frontend/src/components/RunDetail.jsx`**：
1. `activeResult` 存的是**點擊當下從 `results` 複製出來的物件**。SSE 進來時 `patch` 會重建
   `results` 裡的物件，但 `activeResult` 還指著舊的那一個——由它衍生的 verdict、
   `canReDiagnose` 全部跟著凍結。
2. 中欄與右欄的內容**全部來自 `GET .../trace`，而那個請求只在 `pick()` 裡發一次**。題目從
   `pending → answered → judged → diagnosed` 的過程中沒有任何重新抓取。
3. `agent_response`、`judge_comment`、`verdict`、`analysis` 都在同一包 payload 裡，所以是一起凍結的。

**補法**：
- `activeResult` 改存 **id**，畫面上的那一列由 `results.find(...)` 即時推導。
- 新增一個「**trace 指紋**」`id|phase|verdict|trace_ready|has_analysis`，指紋一變就重抓 trace。
  它由既有的 SSE 事件就地更新，所以是**事件驅動、不是輪詢**。
- **重抓時不清空畫面**（舊 `pick()` 的 `setTrace(null)` 會讓畫面閃回空狀態）；只有換題才清空，
  同一題的背景刷新以標題列一個小圓點表示。
- **保留開發者手動選的 span**：只有換題、或診斷第一次出現時才自動跳到 `suspects[0]`。每次刷新
  都跳的話，正在讀某個 span 的人會被硬拉走。
- **後端**：`orchestrator._publish_progress` 補上 `has_analysis` / `trace_error` /
  `diagnosis_error`；並新增 **`question_judged`** 與 **`question_traced`** 兩個事件。原本判分完到
  最後一個 `question_done` 之間隔著 trace poll 與診斷，接真實服務時那是數十秒——那段時間題目會
  一直停在 `judging…`。

#### (b) Langfuse 的 `Unknown table expression 'events'`

**這是 Langfuse 部署端的問題，不是本平台的**。我們呼叫的是
`GET /api/public/v2/observations?traceId=`；錯誤訊息裡的 SQL 是 **Langfuse server 自己對它的
ClickHouse 產生的**。自架版本約 3.152.0 起會查一張屬於 v4 wide-observations schema 的
`events` / `events_core` 表，而該表的 production migration 尚未釋出
（langfuse#11924、langfuse#12223、discussion#12777）。

> **官方 Python SDK 幫不上忙**：`langfuse.api.*` 是同一組 REST 端點的產生式 client，會撞到
> 完全相同的 server 端查詢。因此**不引入該依賴**，維持既有的 httpx client。

**根治在 Langfuse 那邊**：查 `SELECT * FROM default.schema_migrations WHERE dirty = 1`、
重跑 ClickHouse migration（注意 `LANGFUSE_AUTO_CLICKHOUSE_MIGRATION_DISABLED`），
或把 image 釘回 3.152 以下。

**本 repo 這邊做的（`real/langfuse.py`）**：
1. **兩條讀取策略，依序嘗試**，先拿到 observation 的獲勝：
   - `GET /api/public/traces/{id}` → `TraceWithFullDetails`，其 `observations` 是完整的
     observation 物件，欄位與列表端點相同，**`observation_to_span` 原封不動共用**。
   - 既有的分頁式 `GET /api/public/v2/observations?traceId=`。
   兩者由 Langfuse 內部**不同的查詢**服務，所以其中一條壞掉時另一條有機會可用。
2. **每條策略各自的 NotReady 語意**：單一 trace 端點的 `404` = 尚未 ingest（→ `NotReady`），
   不是失敗。只有**每一條都失敗**才 raise `TraceFetchError`，且**把每一條的錯誤都帶上**——
   fallback 絕不能把主要路徑的失敗原因藏起來。
3. **`LANGFUSE_TRACE_READ_STRATEGY`**（`auto` | `trace_api` | `observations_api`）：部署確認
   正常後可以釘死其中一條，省掉多餘的第一次請求。
4. **錯誤訊息要能讀**：前端 `SpanList.jsx` 認得這個 ClickHouse 簽章（以及 401、連不上），紅色
   banner 顯示一句白話說明「這是 Langfuse 自架的已知問題，不是 eval 平台的錯」與該怎麼修，
   **原始 SQL 收進可展開的 Technical detail**。完整原文照舊存進 `trace_error`。

> ⚠️ 誠實的但書：fallback 是一個**避險**。如果貴方的 Langfuse 兩個端點都以同樣方式壞掉，
> 只有修部署才能解決——但至少畫面會直說是這麼回事。

#### (c) 新 run 顯示上一個 run 的 Langfuse 錯誤

**病灶**：`routers/results.py::get_trace` 只用 `result.status in ("failed","cancelled")` 分支。
還在 `pending`（agent 根本還沒被問）的題目會落進 else 分支、**真的去打 Langfuse**——一個不可能
存在 trace 的 correlation_id。Langfuse 壞掉時那一打就立刻失敗，產生一個**跟上次一模一樣的全新
錯誤**，看起來就像舊錯誤被重複拿來用。

**補法**：改用 `result_phase(...)` 判斷，`pending` → 新的 `trace_state="not_started"`
（「這題還沒送給 agent」），**在 agent 回答之前絕不呼叫 trace store**。順帶省掉一筆真實成本：
以前每點一次未開始的題目，都會在同一個 request 內連打最多 `TRACE_POLL_MAX_ATTEMPTS`(8) 次 HTTP。
另外 re-diagnose 成功時會清掉 `trace_error`；多選 run 時中欄會標出這一列**屬於哪一個 run**
（`QuestionResultOut.run_label`）——跨 run 的代表列很容易被誤認成正在看的那個 run。

#### (d) 「Use config from」下拉改為有上限的 listbox

原本一個 run 一個 `<option>`，沒有上限；而且原生 `<select>` **無法指定顯示幾列**
（`size` 對下拉不適用），所以「只顯示 10 個、其餘用捲的」只能自己做 listbox。

新的 `RunPicker.jsx`：`max-height` 剛好十列、其餘捲動；每列顯示 run 名稱、時間、通過率與金鑰
標記（一整排原始時間戳很難認）；超過十個時出現搜尋框；支援 ↑/↓/Enter/Esc 與
`role="listbox"`/`role="option"`。**Esc 只關 popup、不關整個 run config 對話框**（否則填到一半
的設定全沒了）。它**自己去打分頁端點**，不再靠 `RunHistory` 傳整份清單當 prop——那份清單現在也
分頁了，當 prop 只會拿到使用者剛好捲到的部分。

#### (e) 分頁與清單效能

畫面渲染只是小的那一半，**真正會讓 app 卡住的是後端查詢**：

| 端點 | 原本 | 現在 |
|---|---|---|
| `GET /eval-sets` | 每個 set 三個查詢，其中一個把**該 set 所有 run 的所有 `question_results`** 撈出來，只為了算兩個 run 的 regression | 整頁一次算完的聚合查詢，**查詢數固定** |
| `GET /eval-sets/{id}/runs` | 每個 run 一個 `COUNT`（N+1） | 一次 `GROUP BY run_id` |

- **regression 只需要最新兩個 run**（`regression_summary` 本來就只讀 `[0:2]`），把 verdict 載入
  限制在那兩個 run 上，去掉了絕大部分的資料量。
- **趨勢線只取最近 `TREND_RUNS`(20) 個 run**（window function）。趨勢是「最近走向」的一瞥，不是
  檔案庫；沒有上限的話，一個長壽的 eval set 會為了畫 120px 的 SVG 而載入它的全部歷史。
- **分頁**：兩個端點都吃 `limit`/`offset`，回傳 `{items, total, has_more}`。前端是
  **無限捲動 + Load more 按鈕**（`usePagedList.js` / `ListFooter.jsx`）——按鈕不是裝飾：
  IntersectionObserver 對鍵盤操作不會觸發，頁面不捲動時也永遠不會觸發。
- **篩選/排序在 SQL 做**（§6.10 的缺口）：名稱搜尋 + metadata key/value + 排序。只篩已載入的
  那一頁，會讓搜尋結果取決於使用者捲了多遠。
- **migration `0005_list_indexes`**：schema 原本除了 PK 與 unique 之外**一個索引都沒有**。新增
  `eval_set_roles(user_subject)`（首頁第一個查詢，PK 是 `(eval_set_id, user_subject)` 用不上）、
  `runs(eval_set_id, started_at DESC)`、`question_results(run_id, verdict)`。

**實測（真 Postgres 16，60 個 eval set、其中一個 80 個 run、共 31,520 筆 `question_results`）**：

| | 之前 | 之後 |
|---|---|---|
| `GET /eval-sets` | 180 個查詢 / 209.5 ms | **6 個查詢 / 47.4 ms** |
| `GET /runs` | 80 個查詢 / 44.8 ms | **3 個查詢 / 4.0 ms** |

查詢數與頁面大小無關（limit=1 與 limit=24 相同），這正是
`tests/test_pagination.py::test_card_query_count_does_not_grow_with_page_size` 守住的性質——
斷言時間會 flaky，斷言查詢數不會。

#### (f) 本次的驗證方式

- **後端單元測試 122 個通過**（新增 27 個）。其中 11 個是**需要資料庫**的分頁測試，未設
  `TEST_DATABASE_URL` 時會 skip，所以 `make test` **維持不需要 DB 也不需要網路**（111 passed,
  11 skipped）。
- **真 Postgres 16**：`alembic upgrade head`（含 `0005`）、`python -m app.seed`、上表的效能實測。
- **真瀏覽器**（Playwright + Chromium，17 項檢查全通過、無任何 console / page error）：
  - 首頁 24 張卡分頁、Load more 追加 48 張**無重複**、搜尋跨全部分頁生效。
  - run 歷史 20 列分頁、追加 40 列無重複、**多選在追加後仍保留**。
  - Run picker popup 高度受限可捲、Esc 只關 popup。
  - **觸發 run 後停在同一題不做任何切換**，中欄自己長出 Agent Answer → verdict → trace spans；
    手動選的 span 在多次背景刷新後仍是選中的那一個。
  - 未開始的題目顯示「Waiting for the agent」，**不是** trace 錯誤。
- **Langfuse 錯誤路徑**：用一個回傳真實 `Unknown table expression 'events'` 500 body 的 mock，
  確認**兩條策略都被嘗試**、錯誤訊息含兩者、瀏覽器中顯示白話說明且原始 SQL 收在可展開區塊；
  同時確認**同一個 run 中尚未回答的題目完全沒有對 Langfuse 發出任何請求**（(c) 的直接驗證）。
- 當時**尚未對接真正的 Langfuse 服務**，成功路徑只有 mock。**這一點之後已經解除**：真實
  Langfuse 上的 trace 現在讀得回來，見 §9.19。

---

### 9.19 讀得回真實 trace 之後：span payload 的結構化渲染

> §9.18 收尾時 Langfuse 的成功路徑還只有 mock。實際接上去之後，讀取本身是通的，但**右欄顯示
> 的東西不對**——真實 span 的 body 不是假層那種一行字串，而是一次 LLM call 的完整請求／回應。
> 本節記錄的是這件事帶出的兩處修正。四個 seam 的 real 實作**維持原樣**，`*_IMPL` 也仍預設 `fake`。

#### (a) 右欄把拿到的結構丟掉了

**症狀**：點一個 span，看到兩塊被切到 800 字的 JSON。開發者點開 span 是想知道「這次 LLM call
到底看到什麼、又產出什麼」，得到的卻是一段讀不完也讀不懂的碎片。

**病灶兩處**：
1. `observation_to_span` 把 `obs["input"]` 一律用 `as_text()` 壓成 JSON 字串，結構在進到 API
   之前就沒了。
2. `GET .../trace` 又對它套一次 `truncate_body`。截斷本身沒錯——錯在套用的位置：§6.7 是為了
   **診斷 LLM 的 context window**，套在檢視路徑上砍掉的是開發者要看的證據，順帶讓 JSON 變成
   無法 parse 的碎片。

**補法**：
- `Span` 多兩個欄位 `input_json` / `output_json`（trace store 原本的物件；連被 agent 序列化成
  JSON 字串的 payload 也 parse 回來）。`input` / `output` 仍是文字，因為診斷 prompt 是從它們組的。
- `SpanOut.input/output` 型別放寬成「物件或字串」，**檢視路徑不再截斷**。
- 前端新增 `SpanPayload.jsx`，照 chat-completions 的形狀渲染（tools / 每則 message / tool_calls），
  細節與預設收合狀態見 §9.9。長度改用**收合**處理，不用切的。
- 假層 `build_fake_trace` 也改成同樣的 chat 形狀，純 Docker 的 demo 就能驗證這條路徑。

**取捨**：整條 trace 的完整 body 會一次回給前端。以真實 trace（約 8 個 generation，每個帶 tool
定義與愈來愈長的 messages）估計是每題數百 KB——以這個 POC「點一次抓一次、跑在 localhost」的
檢視路徑來說可以接受，trace 再大才需要改成逐 span 延遲載入。

#### (b) 中間欄四種內容擠在一條捲軸裡

Agent 回答、期望答案、judge 評語、診斷、span 列表，五段內容沒有任何分界。改成三個具名分區
（**Answer / Diagnosis / Trace · n spans**），trace 狀態橫幅一併移進 Trace 分區——它們講的是
下面那份 span 列表，不是答案也不是診斷。內容一項沒增沒減，只是把界線畫出來。

#### (c) 本次的驗證方式

- **後端單元測試 127 個通過**（新增 5 個：結構化 body 保留、JSON 字串 payload 會被 parse、
  純文字沒有結構形式、超長 body 檢視時不截斷、結構化 body 以物件形式送出）。
  `test_judge_and_diagnosis.py` 的 §6.7 截斷測試**原封不動且仍通過**——這就是「診斷路徑沒被動到」
  的守門測試。
- **真 Postgres 16 + 真瀏覽器**（Playwright + Chromium，無任何 console / page error）：走完
  卡片 → run 歷史 → 三欄詳情，確認 tools 與前面的 message 預設收起、最後一則與 Output 預設展開、
  role 色籤、Pretty|JSON 切換兩個區塊都可用、那個刻意超長的 tool 結果在自己的框內捲動而
  **畫面上再也沒有 "truncated" 字樣**；`generating` 橫幅出現在 Trace 分區內、正確題目的 Diagnosis
  分區顯示「Correct answer — no diagnosis generated.」；light/dark 兩個主題都確認過。

---

## 10. Stage 4：Playground 實作現況（As-Built）

> 本節與 §9 同性質：描述**已經寫進 codebase 且可執行**的東西，是 Playground 的權威現況來源。
>
> **Stage 4 不在 §6.6 的三階段藍圖裡。** 它是後來新增的階段，補的是 Stage 1 動線末端的一個缺口：
> 開發者在三欄詳情看完診斷、心裡有了「如果 skill 這樣改應該就會對」的假設之後，**沒有任何便宜的
> 方式驗證那個假設**——唯一的路是改 eval set、跑一整個 run。Playground 就是那條便宜的路：
> **一題、一組設定、一份可改的 skill，按一次就跑**。
>
> **一句話現況**：真實的 UI + 真實的 orchestration；**完全不落庫**（沒有 migration）；
> 新增第五個 seam（skill 目錄），fake/real 兩套都寫好，預設 fake。
> 判分與診斷**都是選填的**：給了期望答案才判分，給了期望流程才診斷。

### 10.1 範圍與刻意不做

| 做了 | 沒做（刻意） |
|---|---|
| 單題即時試打：問題 → agent → trace → span 檢視 | **不落庫**：沒有 `playground_*` 表、沒有 migration |
| per-request **skill override**（改 skill 重跑，不寫回） | **不寫回 agent server**（需版本控制 / rollback，§4.9 → Stage 3）|
| 選填的 judge（期望答案）與 diagnosis（期望流程） | **不做「一按跑 N 次取多數」**（§6.5 的建議）——一次一次手動跑 |
| 本 session 的 attempt 清單 + 切換 + clone 回編輯區 | **不做並排 diff / skill diff** |
| 從三欄詳情把題目帶進 playground | **正式 eval run 不支援 skill override**（只有 playground 有）|
| 中止進行中的 attempt | **不做多輪對話**（agent 是 stateless，`/execute` 是單次呼叫）|

### 10.2 第五個 seam：`SkillClient`

沿用既有四個 seam 一模一樣的圖樣（Protocol + fake + real + `*_IMPL` 開關），所以不接 agent server
也能完整驗證這條路徑。

| Seam | 介面 | 假實作 | 真實實作 |
|---|---|---|---|
| `SkillClient` | `list_skills() -> [SkillSummary]`、`get_skill(name) -> Skill` | `fake.py::FakeSkillClient`：三個罐頭 skill（`billing` / `reporting` 對齊 seed 的 skill tag）| `real/skills.py::HttpSkillClient`：`GET {base}/skills`、`GET {base}/skills/{name}` |

- **`SKILL_IMPL=fake|real`**（預設 `fake`）。**共用 `AGENT_BASE_URL` / `AGENT_TIMEOUT_S`**——
  skill 就住在回答問題的那台 server 上，多一個 base URL 只是多一個會設錯的地方。
- **`build_seams(..., include_skill=False)`**：skill client 是**選擇性建構**的。
  理由是隔離故障面——`SKILL_IMPL=real` 但沒設 base URL 會 raise，而 run 路徑完全不讀 skill 目錄；
  若無條件建構，一個設錯的 skill seam 會讓**觸發 run 與看 trace 全部 500**。只有 playground 的
  skill 端點會傳 `include_skill=True`。
- **讀不到就要大聲**：目錄讀失敗回 **503 + 原因**，絕不回空陣列。
  「這個 agent 沒有 skill」與「你的 URL 錯了」長得一樣的話，開發者會默默地憑記憶重打一份 skill，
  然後測到錯的文字。空目錄本身是合法答案（agent 還沒有 skill）；**有內容但沒有一個有名字**才是失敗。
- **解析寬容**（比照 `real/agent.py`）：目錄接受 `{"skills":[…]}` / `{"items":…}` / 裸 list /
  純字串名；skill 內容接受 `content` / `text` / `skill` / `body`，或整個 body 就是純文字。

### 10.3 資料落點：完全 ephemeral

**`app/playground.py`** 有一個 module-level 的 `OrderedDict` store，key 是 attempt id。
**沒有任何一張表、沒有任何 migration。**

這是決定，不是省略：**attempt 是一次拋棄式實驗，run 是一筆歷史紀錄**。不落庫換到三件事——
不用 migration、不用權限列、eval 歷史裡不會混進「這個 run 是真的嗎」的模稜兩可——代價只有一個，
而 UI 直說了那一個：**backend 重啟就沒了**（含 `uvicorn --reload` 的自動重啟）。

- **單 process 假設**：與既有的 in-memory SSE hub（§9.10）同一個限制，多 worker 部署要先有共享 bus。
- **`PLAYGROUND_MAX_ATTEMPTS_PER_USER`（預設 20）**：上限不是裝飾。一個 attempt 握著一整條 trace，
  真實 agent 是數百 KB 的 span body（§9.19 的取捨），無上限的記憶體 store 會一次一個 attempt 地
  吃掉 process 的記憶體。**淘汰最舊的，但絕不淘汰還在跑的**——那會讓背景 task 變成孤兒。
- **`get(attempt_id, subject)`**：別人的 attempt 一律 **404 而非 403**。scratch work 是私有的，
  所以「某個 id 上是否存在一個 attempt」也不是別人該知道的事。

### 10.4 判分與診斷都是選填的

| 給了什麼 | 會發生什麼 |
|---|---|
| 只有問題 | agent → trace。**judge 與 diagnosis 完全不被呼叫**（不是呼叫了丟掉——那是帳單）|
| ＋期望答案 | 加上 judge，出現 verdict / score / comment |
| ＋期望流程 | 加上 diagnosis（§6.9 的線索式輸出，含 caveat）|
| 只有期望流程、沒有期望答案 | **有診斷、沒有 verdict** |

最後一列迫使一個契約變更：**`DiagnosisClient.diagnose(..., judge_verdict: Verdict | None)`**。
`build_diagnosis_messages` 的第四塊**照樣存在**，只是改寫成「沒有判分：未提供期望答案，
所以什麼都沒被評分。**不要假設最終答案是錯的**」。§6.9 的四塊順序一格都沒動。
把整塊拿掉才是錯的做法——模型會自行推論「答案錯了」然後去找一個可能不存在的故障。

診斷的觸發條件也與 run 不同：**run 只診斷 judge 判錯的題**，playground **只看有沒有期望流程**。
一個描述了期望流程的開發者想知道 trace 在哪裡偏離，而他可能根本沒提供期望答案。

### 10.5 端點與權限

```
GET    /playground/skills                       # 目錄；失敗 → 503 + 原因
GET    /playground/skills/{name}                # 單一 skill；不存在 → 404
POST   /playground/attempts                     # 建立 + 起背景 task，201（回 detail）
GET    /playground/attempts                     # 我的 attempt 清單（新到舊，不分頁）
GET    /playground/attempts/{id}                # 詳情，含 TraceView 形狀的 trace
POST   /playground/attempts/{id}/cancel         # 非 running → 409
DELETE /playground/attempts/{id}                # running → 409（先中止）
POST   /playground/attempts/{id}/re-diagnose    # 無 trace / 無期望流程 → 409；模型失敗 → 502
GET    /playground/attempts/{id}/progress       # SSE，?subject=
```

- **權限**：`require_owner` / `require_reader` 都宣告 `eval_set_id: uuid.UUID = Path(...)`，
  所以在沒有 eval set 的路徑上**用不上**。規則改為 `current_subject` + 「attempt 屬於建立者」。
- **清單不分頁**：store 本來就有 per-subject 上限（§10.3），數量上限是結構保證的。
- **回傳 trace 時直接沿用既有的 `TraceView`**（`schemas.py`）——這是整個整合最省的一步：
  前端 `SpanList` / `SpanDetail` / `SpanPayload` **零修改**就能渲染，連 §9.19 的結構化 span
  渲染、五種 `trace_state` 橫幅、診斷/caveat 橫幅全部免費繼承。
- `TraceView` **新增 `ground_truth_reasoning`**（`results.py` 也一併回傳）：三欄詳情要把題目
  「帶進 playground」時，期望流程得跟著走。
- **金鑰只進不出**，與 run 同一條規則：`PlaygroundAttempt.secrets` 沒有任何 response model
  裝得下它。「借用舊 run 的金鑰」那條規則（`runs.py::_resolve_secrets`）**不適用**——
  playground 不落庫、也沒有 run 可借，改由前端在該 browser session 的 state 裡留著，
  所以一個 session 只需輸入一次。
- **設定在觸發當下寫死**（`run_config.resolve`），與 §9.15 同理：attempt 記的是「有效值」，
  所以事後看得出它到底用了什麼。

### 10.6 兩處抽取（先做的重構，不是新功能）

Playground 沒有複製既有邏輯，而是先把它抽出來。兩處都以既有測試當守門：

| 新檔 | 內容 | 為什麼 |
|---|---|---|
| **`app/pipeline.py`** | 單題四步（`call_agent` / `call_judge` / `wait_for_trace` / `run_diagnosis`）＋ retry / timeout / cancel 三個政策（`with_retries` / `await_or_cancel` / `RunCancelled` / `clip`）| 這些原本全部**內嵌在 `orchestrator._process_question` 裡、與 DB 寫入交織**，沒有可重用的單題函式。**DB 寫入與 `_publish_progress` 留在 orchestrator 原地**——那些是「一個 run」的性質（一列一題、done/total 計數），playground 一個都沒有 |
| **`app/services/trace_view.py`** | `resolve_trace_spans`（檢視路徑的短 poll）＋ `span_to_out`（`input_json` 優先、**檢視路徑不截斷**）| 原本已經是**兩份幾乎相同的複製**（`results.py` 與 `diagnosis.py`，§9.14 自己點出過），playground 會變第三份 |

- `AgentClient.call` 的 `skill_override` 是**尾端、keyword、有預設**；而且 `pipeline.call_agent`
  **只在有 override 時才把它傳出去**，所以一次 eval run 的呼叫（與 request body）與 §10 出現之前
  **完全相同**，連沒長出這個參數的 AgentClient 實作都照樣能用。

### 10.7 skill override：怎麼傳，以及**平台無法保證什麼**

有 override 時，`real/agent.py` 在 `/execute` 的 body 加上（沒有時**整個 key 都不存在**）：

```json
{"message": "...", "metadata": {"trace_data": {...}, "skill_override": {"name": "billing", "content": "..."}}}
```

**agent server 端需要做的三件事**（repo 外的相依，全部是加法，不動既有契約）：

1. `POST /execute` 讀 `metadata.skill_override`，有值時**這一次呼叫**改用該 skill 文字，
   不落磁碟、不影響其他 request（§4.7 / §6.5 的 per-request override）。
2. `GET /skills` → `{"skills":[{"name","description"}]}`
3. `GET /skills/{name}` → `{"name","content"}`

> ⚠️ **誠實的但書：平台無法自動驗證 agent 真的採用了 override。** 這與 §4.8 的非決定性是同一個
> 問題。實務上唯一的證據是：注入的 skill 文字會出現在該次 trace **第一個 span 的 system message**
> 裡，而 `SpanPayload.jsx` 就是照 chat-completions 形狀渲染的（§9.19），所以**看得到**。
> 這句話寫在 UI 的 hint 裡，不假裝有自動驗證。
> 假層也照同一條路徑做：`FakeAgentClient` 記下 override，`build_fake_trace` 把它接在 system
> prompt 後面，所以純 Docker 的 demo 就能驗證「override 有沒有送到」這件事看起來是什麼樣子。

### 10.8 前端

- **頂層分頁**（`Eval Sets | Playground`）用既有的 `.segmented`（不需要新 CSS）。原本三層的 `view`
  state 一動不動——playground 是那整個狀態機的**兄弟，不是第四層**，因為它不屬於任何 eval set。
  麵包屑只在 eval 分頁顯示（否則 playground 會掉出一個誤導的孤兒「Eval Sets」麵包屑）。
- **新元件**：`Playground` / `PlaygroundComposer` / `SkillEditor` / `AttemptList` / `PhaseSteps`，
  以及從 `RunConfigDialog` **抽出**的 `RunConfigFields`（defaults 抓取 + fake seam 變灰 +
  金鑰只進不出，兩邊共用而不是 fork 260 行）。
- **照抄 §9.18(a) 的兩個機制**：開啟中的 attempt **存 id、每次 render 重新查**；
  **指紋**（`phase|verdict|status|trace_ready|has_analysis` + nonce）驅動 trace 重抓，
  事件驅動、不輪詢、重抓不清空畫面、只在換 attempt 或診斷首次出現時才跳 suspect。
- **`PhaseSteps` 而非 `RunProgress`/`RunStatusBar`**：後兩者是**聚合形狀**的（0/1 的長條、
  total=1 的堆疊長條都沒有意義）。單題要的是「四個呼叫裡現在卡在哪一個」。
  不適用的階段**畫刪除線而不是隱藏**——那是一個選擇而非還在等待，而且加上期望答案時整排不該變形。
- **`SpanList` 加 `playground` prop**：它本身與 run/result 幾乎零耦合，但有兩處假設了「被評分過的
  eval 題目」——無條件 render「Expected answer」（缺值顯示 `—`），以及診斷區的 fallback 文案寫
  「a question is diagnosed once it has been judged incorrect」。在 playground 兩句都是假話。
- **`.three` 的高度**：原本硬編碼 `calc(100vh - 210px)`。新增分頁條改變了**所有既有頁面**的
  chrome 高度，所以改成 `var(--chrome-h, 320px)`；**playground 則完全不用視窗推導的高度**
  （`height: 62vh`）——它的 composer 展開兩個面板時高度會變三倍，任何固定減法都會在某個狀態下
  裁切或留白。

### 10.9 設定新增

| 變數 | 預設 | 說明 |
|---|---|---|
| **`SKILL_IMPL`** | `fake` | 第五個 seam。**只讀不寫、風險最低，可以最先開**（§10.10）|
| **`PLAYGROUND_MAX_ATTEMPTS_PER_USER`** | `20` | 記憶體 store 的 per-subject 上限（§10.3）|
| `fake_config.SKILL_FETCH_LATENCY_S` | `0.15` | 假目錄的延遲。刻意很短——它在開發者打字時被讀取，該像讀本機檔案 |

`make preflight`（`app/check_integrations.py`）多一個 `skill` 檢查；`GET /run-config/defaults`
的 `impls` 多一個 `skill`，所以 UI 能標示「這些 skill 是罐頭的」。

### 10.10 建議的帶起順序（接在 §9.15 的表之前）

| 步驟 | 設定 | 這一步該看到什麼 |
|---|---|---|
| **0** | `SKILL_IMPL=real` + `AGENT_BASE_URL` | Playground 的下拉出現**真實的** skill 名稱與內容。只讀、無副作用，是風險最低的一步 |
| 1–4 | 同 §9.15 | |
| 5 | agent server 支援 `metadata.skill_override` | 注入的 skill 文字出現在真實 Langfuse trace 第一個 span 的 system message 裡 |

### 10.11 測試與驗證現況

**單元測試**：新增 **54 個**（總計 **170 個通過 + 11 skipped**）。playground 不碰 DB，
所以新測試**不需要 `TEST_DATABASE_URL`**，`make test` 維持不需 DB 也不需網路。

- `tests/test_skill_client.py`（13）：目錄的四種 body 形狀、空目錄合法 vs 無名字則失敗、
  4xx/5xx 帶狀態碼與 body、transport 錯誤帶 host、skill 文字的三種鍵、缺 base URL 的訊息、
  逐 attempt base URL 覆寫 env。
- `tests/test_playground.py`（39）：四階段依序推進；**沒填期望答案 → judge 呼叫次數為 0**、
  **沒填期望流程 → diagnosis 呼叫次數為 0**（斷言呼叫次數而非「verdict 是 None」——後者在
  「呼叫了但丟掉」的情況下也會通過，而那是一筆帳單）；`judge_verdict=None` 時 prompt 第四塊
  說「未判分」且四塊順序不變；skill override 傳到 agent；agent 失敗 / judge 失敗 / diagnosis 失敗
  / trace store 失敗四種政策；**中止會放棄進行中的 agent 呼叫**（以 30s stub + `wait_for` 2s 斷言）；
  中止時保留已拿到的答案；SSE 五個事件與指紋欄位；store 上限淘汰最舊**但不淘汰還在跑的**；
  跨 subject 404；金鑰不外流的**值層級**斷言；五種 `trace_state`；檢視路徑不截斷。
- `tests/test_agent_client.py` **+2**：有 override 時 `metadata.skill_override` 出現、
  **沒有時整個 key 不存在且 metadata 只有 `trace_data`**（既有契約的迴歸守門）。
- **`test_results.py` / `test_judge_and_diagnosis.py` 一行未改且全過**——這是兩處抽取
  （§10.6）沒有改變行為的守門證據。`test_orchestrator.py` 只改了兩處符號引用
  （`orchestrator._with_retries` → `pipeline.with_retries`，函式搬家），18 個斷言一個沒動。

**真瀏覽器端到端**（真 Postgres 16 + Playwright/Chromium，**33 項檢查全通過、
無任何 console / page error**）：
- 既有動線迴歸：卡片 → run 歷史 → 三欄詳情，**三欄沒有被裁切、頁面不再垂直捲動**
  （`--chrome-h` 改動的風險面）。
- 三欄詳情的「Try this in the playground」把問題／期望答案／期望流程／該 run 的設定一起帶過去，
  且期望欄位面板**帶著值抵達時自動是展開的**。
- skill 目錄載入 → 選 `billing` → 內容帶出 → 改文字 → 出現 `edited` 標記。
- 送出後**留在原地不做任何切換**：phase stepper 依序推進，中欄自己長出答案 → verdict → span 列表
  → 診斷（這一項直接驗證了指紋重抓機制）。
- **改過的 skill 文字出現在 span payload 裡**（§10.7 的那個唯一證據），
  且畫面上沒有任何 "truncated" 字樣。
- 只有問題的 attempt：judge 與 diagnosis 兩個階段畫刪除線，中欄說「未提供期望答案，此 attempt
  未被判分」與「加上期望流程才會產生診斷」，而不是顯示一個空的期望答案。
- 執行中按停止 → 立刻停；clone 回填編輯區；light/dark 兩個主題都確認過。

> ⚠️ **與 §9.16 相同的但書**：以上全部在 **fake 模式**下驗證。`SKILL_IMPL=real` 的
> `HttpSkillClient` 只有 respx 單元測試，**沒有對接過真正的 agent server**；
> `metadata.skill_override` 也還沒有任何 agent server 讀它（§10.7 的三件事都還沒做）。
