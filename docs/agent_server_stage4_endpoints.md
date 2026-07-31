# Agent HTTP Server — Stage 4（Playground）需要新增的 endpoint

> **這份文件的讀者**：實作 **agent server** 的人（或 Claude Code）。
> agent server 在 eval 平台這個 repo **之外**，是一個獨立的 HTTP 服務。
> 這份文件是**完整且自給自足**的實作規格：讀完它就能動工，不需要再去讀 eval 平台的原始碼。
>
> **要交付的東西共三件**（全部是**加法**，不改動任何既有行為）：
>
> | # | 變更 | 端點 | 目的 |
> |---|---|---|---|
> | **A** | 讀取並套用 `metadata.skill_override` | `POST /execute`（既有端點，新增一個選填欄位）| Playground 的「改 skill 重跑」 |
> | **B** | 新增 skill 目錄 | `GET /skills` | 讓開發者從**真實 skill 文字**開始編輯 |
> | **C** | 新增單一 skill 全文 | `GET /skills/{name}` | 同上 |
>
> **不在這次範圍**：任何寫入 / 更新 skill 的 API（那是 Stage 3，需要版本控制與 rollback）。

---

## 目錄

1. [背景：這些端點被誰呼叫、為什麼需要](#1-背景這些端點被誰呼叫為什麼需要)
2. [既有契約：`POST /execute`（不可破壞）](#2-既有契約post-execute不可破壞)
3. [變更 A：`POST /execute` 支援 `metadata.skill_override`](#3-變更-apost-execute-支援-metadataskill_override)
4. [變更 B：`GET /skills`](#4-變更-bget-skills)
5. [變更 C：`GET /skills/{name}`](#5-變更-cget-skillsname)
6. [呼叫端的實際行為（timeout / retry / header / 解析寬容度）](#6-呼叫端的實際行為timeout--retry--header--解析寬容度)
7. [明確**不要**做的事](#7-明確不要做的事)
8. [驗收條件（可直接執行的 checklist）](#8-驗收條件可直接執行的-checklist)
9. [參考實作骨架（FastAPI）](#9-參考實作骨架fastapi)
10. [常見的錯誤實作（anti-patterns）](#10-常見的錯誤實作anti-patterns)

---

## 1. 背景：這些端點被誰呼叫、為什麼需要

### 1.1 呼叫者是誰

有一個 **agent eval 平台**（FastAPI backend + React 前端）。它做兩件事：

1. **eval run**：拿一份題庫（eval set），逐題 `POST /execute` 給 agent server，收到答案後用 LLM judge 判對錯，
   再從 Langfuse 撈回該題的 trace，對判錯的題做「錯在哪一步」的診斷。
2. **Playground（Stage 4，就是這份文件的來由）**：只跑**一題**。開發者在畫面上打一個問題、
   （選填）貼一份**改過的 skill 文字**、按送出，然後看 agent 的回答 + trace。

Playground 存在的理由很具體：Stage 1 的診斷會告訴你「這題錯在讀了 billing skill 之後選錯欄位」，
開發者下一個念頭一定是「**如果 skill 改成這樣寫，這題就會對**」。
在 Stage 4 之前，驗證這個假設的唯一辦法是改一份 eval set、重跑整個 run。
Playground 就是那條便宜的路——**但它需要 agent server 願意接受「這一次呼叫請用我給你的 skill 文字」**。

### 1.2 correlation 機制（**前提，必須已經實作**）

平台為每題產生一個 `correlation_id`，放在 `metadata.trace_data.trace_id`。
**agent server 必須用這個值當它寫進 Langfuse 的 trace id**，平台之後才找得回這條 trace。

> ⚠️ 如果這一項還沒做，請**先做它**。沒有它，Playground 送出的 override 就無從驗證是否生效
> （見 §3.4），整個錯誤定位功能也是失效的。

### 1.3 為什麼 skill 目錄（B、C）是必要的，而不是「有比較好」

Playground 的編輯區如果從**空白 textarea** 開始，開發者只能憑記憶重打一份 skill，
然後測到的是**錯的文字**——實驗結論因此是假的。
所以平台要能先把「agent server 現在真正在用的 skill 全文」抓下來，讓人在**真本**上改。

（平台仍保留手貼的路徑：目錄讀不到時，開發者可以自己貼一份 skill 文字。
目錄是**便利**，不是硬前提。但沒有它，這個功能的體驗會差一個等級。）

---

## 2. 既有契約：`POST /execute`（不可破壞）

這是現在就存在、平台一直在打的端點。**這次的變更完全不改它的既有形狀。**

### Request

```http
POST {AGENT_BASE_URL}/execute
Content-Type: application/json
```

```json
{
  "message": "客戶 A 上個月的帳單金額是多少？",
  "metadata": {
    "trace_data": {
      "trace_id":   "3f9c2b1a-…",
      "session_id": "3f9c2b1a-…",
      "user_id":    "alice",
      "tags":       ["eval_billing_v3"]
    }
  }
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| `message` | `string` | 題目原文，直接餵給 agent |
| `metadata.trace_data.trace_id` | `string` | **必須**用作該次呼叫的 Langfuse trace id |
| `metadata.trace_data.session_id` | `string` | 與 `trace_id` 同值（每題自成一個 session）|
| `metadata.trace_data.user_id` | `string` | 觸發者的帳號，建議寫進 Langfuse 的 user id |
| `metadata.trace_data.tags` | `string[]` | eval run 是 `["eval_<eval set 名稱>"]`；**Playground 固定是 `["playground"]`**。建議寫進 Langfuse trace tags |

### Response

```json
{ "content": "客戶 A 上個月的帳單金額是 12,480 元。" }
```

- **建議永遠回這個形狀。** 平台的解析有寬容度（見 §6.3），但 `{"content": "..."}` 是正規形狀。
- **空字串或全空白的答案會被平台判定為失敗**，不會送去 judge。
  （判一個空字串只會產生一個毫無意義的 incorrect，反而蓋住真正的問題。）

---

## 3. 變更 A：`POST /execute` 支援 `metadata.skill_override`

### 3.1 Request schema（新增的部分）

當 Playground 的使用者提供了候選 skill 時，**且僅在此時**，request body 會多出一個 key：

```json
{
  "message": "客戶 A 上個月的帳單金額是多少？",
  "metadata": {
    "trace_data": {
      "trace_id":   "7ab41d5e-…",
      "session_id": "7ab41d5e-…",
      "user_id":    "alice",
      "tags":       ["playground"]
    },
    "skill_override": {
      "name": "billing",
      "content": "# Billing skill\n\n處理帳單、費用、發票相關問題。\n1. 先用 sql_query 取出該客戶當期帳單…\n"
    }
  }
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `metadata.skill_override` | `object \| absent` | 否 | **不存在時，body 與現在完全相同——連這個 key 都不會出現** |
| `metadata.skill_override.name` | `string` | 是 | 這份文字**取代哪一個 skill** |
| `metadata.skill_override.content` | `string` | 是 | skill 的**完整**文字（不是 diff、不是片段）|

> `name` 必須跟著 `content` 一起走：agent 得知道這份文字是要**取代誰**，
> 否則它無從決定要把哪一份原本的 skill 拿掉。

### 3.2 語意規則（六條，都是硬性要求）

| # | 規則 | 為什麼 |
|---|---|---|
| **1** | **只影響這一次呼叫** | 它是一個實驗，不是一次部署 |
| **2** | **絕不落磁碟、不寫回任何 skill 儲存體** | 寫回需要版本控制與 rollback，那是 Stage 3 的範圍 |
| **3** | **不得洩漏到其他 request**（含同時進行中的其他 request）| 平台可設定併發（`RUN_CONCURRENCY`），同一秒可能有多個 request 在飛；用 module-level 全域變數存 override 會污染別人的呼叫 |
| **4** | **`name` 指名的那份原始 skill 不得再被載入** | 否則 agent 同時看到新舊兩份互相矛盾的指示，實驗結論不可信 |
| **5** | **override 的文字必須出現在該次 trace 的第一個 span 的 system message 裡** | 這是平台**唯一**能證明 override 生效的證據（見 §3.4）|
| **6** | **其他 skill 一律照常運作** | override 是替換**一份** skill，不是清空整個 skill 集合 |

### 3.3 `name` 不存在於目錄中時怎麼辦

**建議做法：照樣採用這份文字，不要回 4xx。**

理由：Playground 允許開發者**手貼**一份 skill（目錄讀不到時就是這條路），
他可能會給一個尚不存在的新 skill 名稱來測試「如果多一份這樣的 skill 會怎樣」。
把它當成「這一次呼叫額外多出來的一份 skill」處理，是最有用的行為。

- 若你的 agent 架構上做不到「注入一份不存在的 skill」，**退而求其次**：忽略未知名稱、
  照常回答，並在 trace 上留一個明顯的紀錄（例如一個 event/span 或 system message 裡的一行
  `# skill_override 'foo' 未套用：目錄中沒有這個 skill`）。
- **無論如何不要靜默忽略**。靜默忽略會讓開發者以為自己測到了新 skill，其實測到的是舊的——
  這比報錯糟得多。

### 3.4 怎麼證明 override 生效（**這一條決定實作方式**）

平台**沒有辦法**自動驗證 agent 真的採用了 override——它只看得到 `/execute` 的回應字串。
唯一的證據來自 trace：

> **注入的 skill 文字，必須出現在該次 trace 第一個 LLM span 的 system message 裡。**

平台的 span 檢視是照 chat-completions 形狀渲染的（`{"messages": [{"role": "system", …}, …]}`），
所以開發者**看得到**那段文字，用肉眼確認「我改的版本確實送進去了」。

因此在實作上，**不要**用「把 override 存在某個地方，讓 agent 之後某個 tool 去讀」以外的方式偷渡；
正確的做法是讓 override 走**與正常 skill 完全相同的注入路徑**：

- 如果 agent 是把選中的 skill 文字**拼進 system prompt** → 就把 override 的文字拼進去（取代原本那份）。
- 如果 agent 是透過 `read_skill` 之類的 **tool 讀取** skill → 在該次 request 的範圍內，
  讓那個 tool 讀到 override 的內容（同一個 tool、同一個 span，只是內容換了）。
  此時 tool 的 output span 就是證據，這也可以接受——**重點是「文字看得見」**。

建議在被覆蓋的那段文字前面加一行可辨識的標記，例如：

```
# Skill: billing (overridden for this call)
<override content>
```

這讓開發者一眼就分得出「這是我改的版本」而不是原本的。

### 3.5 併發與請求範圍（實作重點）

平台可能同時發出多個 `/execute`，其中**只有部分**帶 override。實作必須是 **request-scoped**：

- ✅ 把 override 當參數往下傳，或用 `contextvars.ContextVar`（見 §9 的骨架）。
- ❌ 用 module-level `dict` / 全域變數 / class attribute 暫存。
- ❌ 寫進檔案系統再讀回來（規則 2 已禁止，而且會互相踩踏）。

### 3.6 大小、逾時與安全

- `content` 可能有**數十 KB**（一份完整的 skill markdown）。請確認 server 的 body size limit
  至少 **1 MB**，不要用預設 8 KB 的反向代理設定把它擋掉。
- override 的文字是**不可信輸入**（它是使用者打字打出來的），會被放進 prompt。
  這是這個功能的本質，無法避免；但**不要**把它當成程式碼執行、當成路徑名稱、
  或用它去組任何檔案 I/O 的路徑。
- 沒有做長度上限的必要，但如果你要加，請回 **413** 並在 body 說明上限，
  不要靜默截斷（截斷後測到的是第三種文字，兩邊都不是）。

### 3.7 回應與錯誤碼

| 情況 | 應回 | 平台會怎麼做 |
|---|---|---|
| 正常 | `200` + `{"content": "…"}` | 進入 judge / trace 流程 |
| `skill_override` 存在但缺 `name` 或 `content`，或型別錯 | `400` + 說明文字 | 該題標為 failed，**把你的錯誤訊息前 500 字顯示在 UI 上**（不重試）|
| agent 內部錯誤 | `500` + 說明文字 | 該題標為 failed，訊息同上顯示（不重試）|
| 逾時 / 連線失敗 | — | 平台**會重試**（預設最多 2 次，backoff 1s、2s）|

> **請把錯誤原因寫進 response body。** 平台會把 body 的前 500 字原樣顯示給開發者。
> 一個沒有 body 的 500 在 UI 上等於「壞了，不知道為什麼」。

---

## 4. 變更 B：`GET /skills`

### 4.1 契約

```http
GET {AGENT_BASE_URL}/skills
```

**200 回應（正規形狀）：**

```json
{
  "skills": [
    { "name": "billing",    "description": "帳單、費用、發票相關問題。" },
    { "name": "reporting",  "description": "報表與統計數字的查詢。" },
    { "name": "escalation", "description": "其他 skill 無法回答時的轉介流程。" }
  ]
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `skills[].name` | `string` | **是** | skill 的識別名稱，**同時是 `GET /skills/{name}` 的路徑參數** |
| `skills[].description` | `string \| null` | 否 | 一句話說明，顯示在下拉選單裡幫開發者選 |

### 4.2 `name` 必須是 **URL-safe** 的（重要）

平台是用 `f"{base}/skills/{name}"` **直接串接**路徑的，**沒有做 URL encode**。
所以 skill 名稱請限制在 `[A-Za-z0-9._-]`：

- ❌ `billing / refunds`（含空白與斜線 → 會變成錯誤的路徑）
- ❌ `帳單`（非 ASCII → 行為視 server 而定，不保證）
- ✅ `billing`、`billing_refunds`、`billing-v2`

如果你的 skill 內部名稱本來就含這些字元，請在目錄中**回一個 slug 當 `name`**，
把原名放進 `description`。

### 4.3 失敗時**必須**回非 2xx，而且要說原因

> **絕對不要**在讀不到 skill 時回 `200 {"skills": []}`。

「這個 agent 沒有 skill」與「你的 URL 打錯 / 我的目錄壞了」如果長得一樣，
開發者會默默地憑記憶重打一份 skill，然後測到錯的文字，得到錯的結論。

- **空目錄本身是合法答案**（一個還沒有 skill 的 agent）→ `200 {"skills": []}`。
- **有內容但讀取失敗** → `5xx`（或適當的 4xx）+ body 寫清楚原因。
  平台會把它變成 **HTTP 503 + 你的原因字串**顯示在畫面上。

### 4.4 平台的解析寬容度（你不必用到，但知道了比較安心）

平台的 client 對形狀是寬容的。以下**全部**會被正確解析：

| 回應 body | 結果 |
|---|---|
| `{"skills": [...]}` | ✅ 正規形狀 |
| `{"items": [...]}` / `{"data": [...]}` | ✅ 也接受 |
| `[...]`（裸陣列）| ✅ 也接受 |
| entry 是 `{"name": "...", "description": "..."}` | ✅ 正規形狀 |
| entry 是 `{"skill": "...", "summary": "..."}` | ✅ `skill`→name、`summary`→description |
| entry 是裸字串 `"billing"` | ✅ 當成只有名稱、沒有描述 |
| entry 是 dict 但**沒有名稱** | ⚠️ 該筆被略過；若**每一筆**都沒名稱 → 視為失敗（503）|
| body 不是 JSON | ❌ 視為失敗（503）。**目錄一定要是 JSON** |

**建議照正規形狀實作**——寬容度是為了容錯，不是給實作者選菜單用的。

### 4.5 其他建議

- **排序**：建議依名稱排序，讓下拉選單穩定。（平台不排序，原樣照收。）
- **分頁**：不需要，也**不要**做。skill 數量是個位數到數十個的量級。
- **快取**：這個端點會被打得不頻繁（開 Playground 頁面時一次）。不需要特別優化。

---

## 5. 變更 C：`GET /skills/{name}`

### 5.1 契約

```http
GET {AGENT_BASE_URL}/skills/billing
```

**200 回應（正規形狀）：**

```json
{
  "name": "billing",
  "description": "帳單、費用、發票相關問題。",
  "content": "# Billing skill\n\n處理帳單、費用、發票相關問題。\n\n## 步驟\n1. …\n"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `name` | `string` | 建議 | 回聲即可（平台以路徑上的名稱為準）|
| `content` | `string` | **是** | **agent 目前實際在用的完整 skill 文字** |
| `description` | `string \| null` | 否 | 同目錄裡的描述 |

### 5.2 `content` 的三條要求

1. **必須是全文，不可截斷、不可省略。** 開發者要在這份文字上直接編輯後送回 `skill_override`；
   少一段就等於實驗少一段。
2. **必須是 agent 真的會用的那一份**（不是文件、不是範本、不是註解掉的舊版）。
3. **原樣的換行與縮排**要保留（它會被放進 markdown editor，再原樣送回來）。

### 5.3 錯誤碼

| 情況 | 應回 |
|---|---|
| 找到 | `200` + 上述 body |
| 名稱不存在 | `404` + 說明文字（例如 `{"detail": "no such skill: foo"}`）|
| 讀取失敗 | `5xx` + 原因 |

> ℹ️ 目前平台會把 agent server 的 404 顯示成 **503 + 原因字串**（它只把「假層的 KeyError」對應成 404）。
> **這不是你的 bug**，訊息內容仍然看得到。照樣回 404 就好——語意正確比遷就目前的對應重要。

### 5.4 平台的解析寬容度

| 回應 body | 結果 |
|---|---|
| `{"content": "..."}` | ✅ 正規形狀 |
| `{"text": …}` / `{"skill": …}` / `{"body": …}` | ✅ 也接受 |
| 裸 JSON 字串 `"# Billing skill…"` | ✅ 也接受 |
| **非 JSON 的純文字 body**（`text/plain`）| ✅ 整個 body 當作 content |
| dict 但以上 key 都沒有字串值 | ❌ 失敗（503）|

---

## 6. 呼叫端的實際行為（timeout / retry / header / 解析寬容度）

這一節描述「平台會怎麼打你」，用來檢查你的實作在真實條件下站不站得住。

### 6.1 連線

| 項目 | 值 |
|---|---|
| Base URL | 由平台的 `AGENT_BASE_URL` 設定（例如 `http://agent-host:8080`），尾端斜線會被去掉 |
| 路徑 | `POST {base}/execute`、`GET {base}/skills`、`GET {base}/skills/{name}` |
| Headers | **只有** `Content-Type: application/json`（`/execute`）。**目前不送任何 Authorization header** |
| Redirect | 會跟隨（`follow_redirects=True`）|
| HTTP client | `httpx.AsyncClient` |

> ⚠️ **如果你的 agent server 需要認證**（API key / token），現在的平台**沒有地方放**。
> 請回頭通知 eval 平台這邊，需要先加一個設定欄位。不要假設平台會帶 header。

### 6.2 逾時與重試

| 呼叫 | 逾時 | 重試 |
|---|---|---|
| `POST /execute` | `AGENT_TIMEOUT_S`，預設 **120 秒**（可逐次 run / attempt 覆寫）| **逾時或連線失敗**會重試，最多 `AGENT_MAX_RETRIES`（預設 2）次，backoff 1s、2s |
| `GET /skills`、`GET /skills/{name}` | 同上（共用 `AGENT_TIMEOUT_S`）| **不重試** |

**兩個推論，實作時請注意：**

1. **`/execute` 必須能承受重複的請求。** 逾時重試時，**同一個 `trace_id` 會被送第二次**。
   請確認這不會讓你的 Langfuse 寫入炸掉（同 trace id 多條資料）——
   平台讀 trace 時會拿到那條 trace 的全部 observation。
   最乾淨的處理是讓後到的覆寫或附加即可，**不要因此回 5xx**。
2. **5xx 不會被重試。** 平台把它當成「這題失敗了，原因如下」直接顯示。
   所以請只在真的無法恢復時回 5xx；暫時性問題如果你自己能重試，就自己重試。

### 6.3 `/execute` 回應的解析

| 回應 body | 結果 |
|---|---|
| `{"content": "答案"}` | ✅ 正規形狀 |
| 裸 JSON 字串 `"答案"` | ✅ 接受 |
| 非 JSON 的純文字 | ✅ 整個 body 當作答案 |
| dict 但 `content` 不是字串（或不存在）| ❌ 該題 failed：「`/execute` response was not a usable string」|
| `content` 是空字串或全空白 | ❌ 該題 failed：「`/execute` returned an empty response」|
| `4xx` | ❌ 該題 failed，body 前 500 字顯示在 UI |
| `5xx` | ❌ 該題 failed，body 前 500 字顯示在 UI |

答案字串會被 `strip()` 之後使用。

---

## 7. 明確**不要**做的事

| 不要做 | 原因 |
|---|---|
| `POST/PUT/PATCH/DELETE /skills*`（寫回 skill）| Stage 3 的範圍，需要版本控制與 rollback 才安全。這次不做 |
| 把 override 寫進磁碟或資料庫 | §3.2 規則 2 |
| 讓 override 影響下一次呼叫 / 其他人的呼叫 | §3.2 規則 1、3 |
| 為 override 新增一個獨立端點（例如 `POST /execute-with-skill`）| 平台打的是同一個 `/execute`；分岔的端點兩邊都要維護 |
| 改變沒有 override 時的 request 處理路徑 | eval run 的行為必須與 Stage 4 出現之前**逐位元組相同** |
| 多輪對話 / session 狀態 | agent 在這個系統裡是 stateless 的，`/execute` 是單次呼叫 |
| 目錄分頁、篩選、搜尋 | 數量級不需要，只是多一組要對齊的契約 |

---

## 8. 驗收條件（可直接執行的 checklist）

設 `AGENT=http://localhost:8080`。

### 8.1 目錄

```bash
# ① 目錄回得出來，形狀正確
curl -s $AGENT/skills | jq
# 期待：{"skills":[{"name":"...","description":"..."}, ...]}
# 檢查：每一筆都有非空的 name；name 只含 [A-Za-z0-9._-]

# ② 單一 skill 回得出全文
curl -s $AGENT/skills/billing | jq -r .content
# 期待：完整的 skill markdown，換行與縮排與 agent 實際使用的一致

# ③ 不存在的 skill 回 404，而且說得出原因
curl -s -o /dev/stderr -w '%{http_code}\n' $AGENT/skills/definitely-not-a-skill
# 期待：404，body 有可讀的訊息
```

- [ ] 目錄失敗時回**非 2xx**，body 有原因（可以暫時把 skill 目錄改名來測）
- [ ] **沒有** skill 時回 `200 {"skills": []}`，而不是 500

### 8.2 `/execute` 無 override（回歸測試）

```bash
curl -s $AGENT/execute -H 'Content-Type: application/json' -d '{
  "message": "客戶 A 上個月的帳單金額是多少？",
  "metadata": {"trace_data": {
    "trace_id": "test-no-override-001",
    "session_id": "test-no-override-001",
    "user_id": "alice",
    "tags": ["eval_smoke"]
  }}
}' | jq
```

- [ ] 回 `{"content": "..."}`，內容非空
- [ ] Langfuse 上出現 trace id = `test-no-override-001`
- [ ] **處理路徑與變更前完全相同**（沒有因為新程式碼而多做/少做任何事）

### 8.3 `/execute` 帶 override（核心驗收）

```bash
curl -s $AGENT/execute -H 'Content-Type: application/json' -d '{
  "message": "客戶 A 上個月的帳單金額是多少？",
  "metadata": {
    "trace_data": {
      "trace_id": "test-override-001",
      "session_id": "test-override-001",
      "user_id": "alice",
      "tags": ["playground"]
    },
    "skill_override": {
      "name": "billing",
      "content": "# Billing skill (OVERRIDE MARKER 12345)\n回答帳單問題時，一律先講金額，再講期間。"
    }
  }
}' | jq
```

- [ ] 回 200，`content` 非空
- [ ] **Langfuse 上 trace `test-override-001` 的第一個 LLM span 的 system message 裡，
      找得到字串 `OVERRIDE MARKER 12345`** ← 這一項是整個功能的核心驗收
- [ ] 同一條 trace 裡**找不到**原本 `billing` skill 的文字（規則 4：舊的那份不得再被載入）
- [ ] 其他 skill（`reporting`、`escalation`…）仍然照常可用

### 8.4 隔離性（規則 1、2、3）

```bash
# 送一次帶 override 的，再送一次不帶的，比對第二次的 trace
curl -s $AGENT/execute -d @override.json  -H 'Content-Type: application/json' >/dev/null
curl -s $AGENT/execute -d @plain.json     -H 'Content-Type: application/json' >/dev/null
```

- [ ] 第二次（不帶 override）的 trace 裡**沒有** `OVERRIDE MARKER 12345`
- [ ] 重啟 server 後，`GET /skills/billing` 的 content **仍是原本的**（沒有被寫進磁碟）
- [ ] **併發測試**：同時送 10 個請求，其中 5 個帶不同的 override marker、5 個不帶 →
      每一條 trace 都只含**它自己那一份**（或不含），沒有互相污染
      （這一項會抓出用全域變數存 override 的實作）

### 8.5 錯誤路徑

- [ ] `skill_override` 只給 `content` 沒給 `name` → `400` + 說明
- [ ] `skill_override.content` 是數字/物件而非字串 → `400` + 說明
- [ ] 未知的 `skill_override.name` → 依 §3.3 處理，且**在 trace 上留得下痕跡**
- [ ] 50 KB 的 `content` 送得進來，沒有被 proxy 擋掉（§3.6）

---

## 9. 參考實作骨架（FastAPI）

**這只是骨架**，用來說明「request-scoped 的 override」長什麼樣子；請按你既有的架構調整。

```python
# app/skills.py ------------------------------------------------------------
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

SKILL_DIR = Path("/app/skills")  # 換成你實際的 skill 來源


@dataclass(frozen=True)
class SkillOverride:
    name: str
    content: str


# request-scoped：每個請求各自一份，不會互相污染。
# 用全域 dict 會在併發時踩踏（見 §3.5）。
_override: ContextVar[SkillOverride | None] = ContextVar("skill_override", default=None)


def set_override(o: SkillOverride | None) -> None:
    _override.set(o)


def list_skills() -> list[dict]:
    """目錄。注意：override 不影響目錄——目錄回報的是 server 真正持有的東西。"""
    out = []
    for path in sorted(SKILL_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        out.append({"name": path.stem, "description": _first_line(text)})
    return out


def read_skill(name: str) -> str:
    """agent 內部載入 skill 的**唯一**入口。

    override 在這裡生效，所以不管 skill 是被拼進 system prompt、
    還是被某個 tool 讀出來，走的都是同一條路（§3.4）。
    """
    o = _override.get()
    if o is not None and o.name == name:
        return f"# Skill: {name} (overridden for this call)\n{o.content}"
    path = SKILL_DIR / f"{name}.md"
    if not path.is_file():
        raise KeyError(name)
    return path.read_text(encoding="utf-8")


def available_skill_names() -> list[str]:
    """override 一個目錄裡沒有的名稱時，把它當成多出來的一份 skill（§3.3）。"""
    names = {p.stem for p in SKILL_DIR.glob("*.md")}
    o = _override.get()
    if o is not None:
        names.add(o.name)
    return sorted(names)


def _first_line(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return None


# app/main.py --------------------------------------------------------------
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app import skills

app = FastAPI()


class SkillOverrideIn(BaseModel):
    name: str = Field(min_length=1)
    content: str


class TraceData(BaseModel):
    trace_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    tags: list[str] = []


class Metadata(BaseModel):
    trace_data: TraceData = TraceData()
    # 不存在時就是 None——eval run 的 body 一個 key 都不會多。
    skill_override: SkillOverrideIn | None = None


class ExecuteIn(BaseModel):
    message: str
    metadata: Metadata = Metadata()


@app.post("/execute")
async def execute(payload: ExecuteIn):
    override = payload.metadata.skill_override
    skills.set_override(
        skills.SkillOverride(name=override.name, content=override.content)
        if override else None
    )
    try:
        answer = await run_agent(                       # 你既有的 agent 進入點
            payload.message,
            trace_id=payload.metadata.trace_data.trace_id,   # ← 用作 Langfuse trace id
            session_id=payload.metadata.trace_data.session_id,
            user_id=payload.metadata.trace_data.user_id,
            tags=payload.metadata.trace_data.tags,
        )
    finally:
        skills.set_override(None)                       # contextvar 本來就不外洩，帶著也無妨
    return {"content": answer}


@app.get("/skills")
async def get_skills():
    try:
        return {"skills": skills.list_skills()}
    except Exception as exc:
        # 絕不回空陣列（§4.3）：讀不到就要大聲。
        raise HTTPException(status_code=503, detail=f"could not read skills: {exc}") from exc


@app.get("/skills/{name}")
async def get_skill(name: str):
    try:
        content = skills.read_skill(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no such skill: {name}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"could not read skill {name!r}: {exc}") from exc
    return {"name": name, "content": content, "description": skills._first_line(content)}
```

> **注意骨架裡的 `read_skill`**：它同時被「拼 system prompt」與「`read_skill` tool」兩條路徑使用。
> 這就是 §3.4 說的「走同一條注入路徑」——它同時滿足了「override 生效」與「override 在 trace 上看得見」。
> 如果你的程式碼裡 skill 是在**多個地方**各自被讀出來的，請先把它收斂成單一入口，再套 override。

---

## 10. 常見的錯誤實作（anti-patterns）

| 實作 | 為什麼錯 |
|---|---|
| 用 module-level `dict[str, SkillOverride]` 以 `trace_id` 為 key 暫存 override | 併發下若忘記清理會累積；一旦有重試（同 trace_id 送兩次）語意就模糊。用 contextvar 或參數傳遞 |
| override 時把**所有** skill 都關掉，只留這一份 | 違反規則 6——實驗會同時改變兩件事，結論不可信 |
| override 的文字只影響「決策」但沒有進 prompt / trace | 平台看不到證據（§3.4），開發者無法確認自己測的是哪一份文字 |
| 把 override 寫進 `SKILL_DIR/billing.md` 再讀回來 | 違反規則 2、3：污染了 server 狀態，也污染了別人的呼叫 |
| 讀不到目錄時回 `200 {"skills": []}` | §4.3：讓「沒有 skill」與「壞掉」長得一樣，開發者會憑記憶重打 skill 並測到錯的文字 |
| `GET /skills/{name}` 回摘要或前 N 行 | §5.2：開發者要在全文上編輯 |
| 新增一個 `POST /execute-with-skill` 分岔端點 | 平台打的是 `/execute`；分岔的端點兩邊都要維護且會漂移 |
| skill 名稱含空白或 `/` | §4.2：平台不做 URL encode，路徑會壞掉 |
| 5xx 沒有 body | 平台只能顯示「壞了」，開發者拿不到任何線索 |

---

## 附錄：平台端對應的程式碼（要對照時查這裡）

| 檔案 | 內容 |
|---|---|
| `backend/app/integrations/real/agent.py` | `POST /execute` 的 client：request body 的組法（`build_payload`）、回應解析、錯誤對應 |
| `backend/app/integrations/real/skills.py` | `GET /skills`、`GET /skills/{name}` 的 client 與所有寬容解析規則 |
| `backend/app/integrations/base.py` | `AgentResponse` / `Skill` / `SkillSummary` / `SkillOverride` 的定義 |
| `backend/app/routers/playground.py` | Playground 的 HTTP 端點與 503 / 404 的對應 |
| `backend/app/playground.py` | 一次 attempt 的四個步驟（agent → judge → trace → diagnosis）|
| `backend/app/pipeline.py` | 逾時、重試、取消的政策 |
| `docs/spec.md` §3.3 / §7 / §17 | correlation 機制、Stage 4 範圍、對 agent server 的相依需求總表 |
