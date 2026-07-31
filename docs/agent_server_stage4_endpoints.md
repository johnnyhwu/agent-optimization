# Agent HTTP Server — 要新增/修改的 endpoint 規格

這份文件描述 agent server 要做的三件事。**照著做即可，不需要知道呼叫端是誰。**

| # | 端點 | 動作 | 用途 |
|---|---|---|---|
| 1 | `GET /get_workspace` | **新增** | 一次回傳目前的 config（已移除機密）+ 所有 skill 檔案內容 + 版本字串 |
| 2 | `GET /get_config_version` | **新增** | 只回版本字串，給呼叫端做「我手上的快照過期了沒」的檢查 |
| 3 | `POST /execute` | **修改** | 多支援一個**選填**欄位 `metadata.workspace`，帶進來時用它取代 server 上的 config / skills 來初始化 agent（**僅限這一次呼叫**）|

> **相容性要求**：`metadata.workspace` 不存在時，`/execute` 的行為必須與現在**完全相同**。
> 既有呼叫方不會送這個欄位，不能因為這次改動而受到任何影響。

---

## 1. 前提：workspace 的結構

假設 agent server 上的檔案長這樣（路徑請換成你實際的）：

```
<AGENT_ROOT>/
  config.json
  workspace/
    skills/
      skill_A/
        SKILL.md
        references/
          ref_1.md
          ref_2.md
      skill_B/
        SKILL.md
```

- `config.json` 是 agent 的設定，**有階層**（巢狀 JSON），裡面**含有機密**（LLM API key 等）。
- `workspace/skills/` 底下是 skill 的檔案樹，深度不限。
- 每次 `POST /execute` 會依 `config.json` 重新初始化一個 agent，該 agent 讀得到 `skills/` 底下的檔案。

**本文件用兩個名詞：**

| 名詞 | 意思 |
|---|---|
| **skill 相對路徑** | 相對於 `workspace/skills/` 的路徑，例如 `skill_A/references/ref_1.md`。**一律用 `/` 當分隔符號**，不要用 `\` |
| **config 路徑** | 用 `.` 串起來的 key 路徑，例如 `agents.defaults.api_key` |

---

## 2. 三條共用規則

這三條在多個端點都會用到，**請各寫成一個共用函式**，不要在每個端點各寫一份（兩份實作遲早會不一致）。

### 2.1 機密遮罩 `redact(config) -> (safe_config, redacted_paths)`

**做什麼**：遞迴走訪 config 的每一層，把 key 名稱看起來像機密的葉節點**刪掉**，並記錄它的 config 路徑。

**判斷方式**：把 key 名稱轉小寫，只要**包含**以下任一字串就視為機密：

```
api_key, apikey, secret, token, password, passwd, credential, private_key
```

**回傳兩個東西**：

1. `safe_config`：刪掉機密之後的 config（結構其餘部分完全不變）
2. `redacted_paths`：被刪掉的路徑清單，例如 `["agents.defaults.api_key", "llm.secret_key"]`

**範例**

```json
// config.json
{
  "agents": { "defaults": { "model": "gpt-4o", "api_key": "sk-real-key" } },
  "retries": 3
}
```
↓
```json
// safe_config
{ "agents": { "defaults": { "model": "gpt-4o" } }, "retries": 3 }
// redacted_paths
["agents.defaults.api_key"]
```

> **為什麼要回 `redacted_paths` 而不是安靜地刪掉**：呼叫端會把 config 畫成一個編輯表單。
> 欄位如果無聲消失，使用者不知道它存在，可能自己新增一個同名 key，把真正的金鑰蓋掉。
> 有了這份清單，表單就能把它顯示成「已隱藏、不可編輯」。

**如果 config 的 key 本身含有 `.`**：那個 key 無法用路徑表示。這種情況請直接**不要**在 config.json 裡用含 `.` 的 key。

### 2.2 版本字串 `workspace_version() -> str`

**做什麼**：算出一個字串，內容有任何變化時它就要跟著變。

**步驟**：

1. 取 git 短 commit hash：`git -C <AGENT_ROOT> rev-parse --short HEAD` → 例如 `a1b2c3d`
2. 檢查有沒有未 commit 的改動：`git -C <AGENT_ROOT> status --porcelain`
   - 輸出**是空的** → 版本字串就是 `a1b2c3d`，結束
   - 輸出**非空** → 繼續第 3 步
3. 算內容雜湊：
   - 準備一個 sha256
   - 先餵入 `config.json` 的原始位元組
   - 再把所有 skill 檔案**依相對路徑排序**，逐一餵入「相對路徑的 utf-8 位元組」+ `\0` + 「檔案原始位元組」
   - 取十六進位摘要的**前 7 碼**
4. 版本字串 = `a1b2c3d-dirty.<前7碼>`，例如 `a1b2c3d-dirty.9f3e11c`

**如果 `<AGENT_ROOT>` 不是 git repo**（或 git 指令失敗）：跳過第 1–2 步，直接用第 3 步的雜湊，版本字串 = `nogit.<前7碼>`。**不要因此讓端點失敗。**

> **為什麼不能只用 commit hash**：直接手改 `SKILL.md` 存檔測試是最常見的操作，那時 commit hash 不會變，
> 呼叫端的過期檢查就永遠失效。

### 2.3 錯誤回應

所有端點失敗時：**回非 2xx，而且 body 一定要寫得出原因。**

```json
{ "detail": "could not read workspace: [Errno 13] Permission denied: '/app/workspace/skills'" }
```

> 呼叫端會把這段文字**原樣顯示給使用者看**。一個沒有 body 的 500 對使用者而言等於「壞了，不知道為什麼」。
> **特別注意**：讀不到 skill 時**絕對不可以**回 `200` 加一個空的 `skills: {}`——那會讓「這台 agent 沒有 skill」
> 和「你的路徑設錯了」長得一模一樣。

---

## 3. `GET /get_workspace`（新增）

### Input

無。沒有 query string、沒有 body。

### Output（200）

```json
{
  "version": "a1b2c3d",
  "config": {
    "agents": { "defaults": { "model": "gpt-4o", "temperature": 0.2 } },
    "retries": 3
  },
  "redacted_paths": ["agents.defaults.api_key"],
  "skills": {
    "skill_A/SKILL.md": "# Skill A\n…",
    "skill_A/references/ref_1.md": "…",
    "skill_A/references/ref_2.md": "…",
    "skill_B/SKILL.md": "# Skill B\n…"
  }
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| `version` | `string` | §2.2 算出來的版本字串 |
| `config` | `object` | `config.json` 的內容，**保留原本的巢狀結構**，機密已移除 |
| `redacted_paths` | `string[]` | 被移除的機密路徑；沒有就回 `[]` |
| `skills` | `object` | **扁平**的 `{skill 相對路徑: 檔案完整內容}`；沒有 skill 就回 `{}` |

### 處理邏輯

1. 讀 `config.json`，parse 成 dict。
2. 跑 §2.1 的 `redact()`，得到 `config` 與 `redacted_paths`。
3. 走訪 `workspace/skills/` **底下所有層級的所有檔案**，組成扁平 map：
   - key = 相對於 `workspace/skills/` 的路徑，用 `/` 分隔
   - value = 檔案的完整文字內容（utf-8）
   - **不要只讀 `SKILL.md`**，`references/` 底下的檔案也要讀
   - **不要截斷內容**（使用者要在全文上編輯）
   - 讀不出來的二進位檔（decode 失敗）：跳過它，但**不要**讓整個請求失敗
4. 跑 §2.2 的 `workspace_version()`。
5. 依上面的形狀回傳。

### 錯誤

| 情況 | 回應 |
|---|---|
| `config.json` 讀不到或不是合法 JSON | `500` + 原因 |
| `skills/` 目錄讀不到 | `500` + 原因 |
| `skills/` 存在但底下沒有檔案 | `200`，`skills` 為 `{}`（這是合法狀態）|

### 驗收

```bash
curl -s http://localhost:8080/get_workspace | jq
```
- [ ] `config` 的巢狀結構與 `config.json` 一致
- [ ] `config` 裡**找不到**任何真實的 API key
- [ ] `redacted_paths` 列出了那些被刪掉的路徑
- [ ] `skills` 的 key 包含 `references/` 底下的檔案
- [ ] 任取一個檔案，`skills` 裡的內容與磁碟上**逐字元相同**（沒有被截斷）

---

## 4. `GET /get_config_version`（新增）

### Input

無。

### Output（200）

```json
{ "version": "a1b2c3d-dirty.9f3e11c" }
```

### 處理邏輯

1. 呼叫 §2.2 的 `workspace_version()`。
2. 回傳 `{"version": <字串>}`。

**必須與 `GET /get_workspace` 回的 `version` 用同一個函式算出來。** 兩邊若各寫一份，
呼叫端就會一直誤判「版本不一致」。

### 驗收

```bash
# ① 兩個端點回的版本要一樣
[ "$(curl -s localhost:8080/get_config_version | jq -r .version)" \
  = "$(curl -s localhost:8080/get_workspace | jq -r .version)" ] && echo OK

# ② 手改一個 skill 檔（不 commit），版本必須改變
echo "// test" >> workspace/skills/skill_A/SKILL.md
curl -s localhost:8080/get_config_version | jq -r .version   # 必須與 ① 不同
```
- [ ] 兩個端點的 version 相同
- [ ] 改了 `SKILL.md` 但沒 commit → version 會變
- [ ] 改了 `config.json` 但沒 commit → version 會變
- [ ] 把改動還原 → version 回到原本的值

---

## 5. `POST /execute`（修改既有端點）

### 5.1 Input

既有的形狀（**不變**）：

```json
{
  "message": "客戶 A 上個月的帳單金額是多少？",
  "metadata": {
    "trace_data": {
      "trace_id":   "7ab41d5e-…",
      "session_id": "7ab41d5e-…",
      "user_id":    "alice",
      "tags":       ["playground"]
    }
  }
}
```

- `message`：使用者的問題，直接餵給 agent。
- `metadata.trace_data.trace_id`：**必須**用它當這次呼叫寫進 Langfuse 的 trace id。（既有需求，若尚未實作請一併補上——呼叫端事後要靠它把這條 trace 找回來。）
- `session_id` / `user_id` / `tags`：一併帶進 Langfuse 的對應欄位。

**這次新增的選填欄位**：

```json
{
  "message": "…",
  "metadata": {
    "trace_data": { "…": "…" },
    "workspace": {
      "config": {
        "agents": { "defaults": { "model": "claude-3-5-sonnet" } }
      },
      "skills": {
        "skill_A/SKILL.md": "# Skill A（改過的版本）\n…",
        "skill_A/references/ref_1.md": "…",
        "skill_B/SKILL.md": "…"
      }
    }
  }
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `metadata.workspace` | `object` | 否 | 不存在 → 完全照現有行為跑 |
| `metadata.workspace.config` | `object` | 否 | 要覆蓋的 config，**可以只給部分**。合併規則見 §5.2 |
| `metadata.workspace.skills` | `object` | 否 | 這次呼叫要用的 skills，**是完整內容**。規則見 §5.3 |

`config` 與 `skills` **各自獨立**：可以只給其中一個。

### 5.2 config 的合併規則：**deep merge，不是取代**

> ⚠️ **這是整份文件最容易做錯的地方，請仔細讀。**
>
> ```python
> config = payload["metadata"]["workspace"]["config"]   # ❌ 錯！agent 會沒有 API key
> config = deep_merge(load_config_json(), incoming)     # ✅ 對
> ```
>
> **為什麼**：`GET /get_workspace` 已經把 API key 刪掉了，所以呼叫端手上那份 config **本來就沒有金鑰**。
> 如果你用它整份取代，agent 初始化時就沒有金鑰可用，每一次帶 config 的呼叫都會失敗。

**規則**：以磁碟上的 `config.json` 為底，把傳入的 config **逐層**疊上去。

| 情況 | 行為 |
|---|---|
| 傳入的是 dict，原本也是 dict | **遞迴往下合併** |
| 傳入的是純量（字串/數字/布林）| **取代**原值 |
| 傳入的是 list | **整份取代**，不做逐項合併 |
| 傳入的值是 `null` | 把該值設成 `null`（**不是**刪除這個 key）|
| 傳入的 key 原本不存在 | **新增**它 |
| 原本有、傳入的沒有 | **保留原值**（← 金鑰就是靠這條活下來的）|

**範例**

```json
// 磁碟上的 config.json
{
  "agents": { "defaults": { "model": "gpt-4o", "api_key": "sk-real", "temperature": 0.2 } },
  "retries": 3,
  "tools": ["sql", "search"]
}

// 傳入的 metadata.workspace.config
{
  "agents": { "defaults": { "model": "claude-3-5-sonnet" } },
  "tools": ["sql"]
}

// 合併結果（實際拿去初始化 agent 的東西）
{
  "agents": { "defaults": { "model": "claude-3-5-sonnet", "api_key": "sk-real", "temperature": 0.2 } },
  "retries": 3,
  "tools": ["sql"]
}
```

注意 `api_key` 與 `temperature` **都被保留了**，而 `tools` 是整份被換掉。

**還有一條安全規則**：合併之前，先把傳入的 config 裡**位於機密路徑上的值全部丟掉**
（用 §2.1 對磁碟上的 config 算出來的 `redacted_paths` 當清單）。

> **為什麼**：否則任何人都能透過這個端點改掉 agent 的金鑰，把 agent 指去別的地方。
> 機密永遠只從磁碟上的 `config.json` 讀，**不接受從外面傳進來**。

### 5.3 skills 的規則：**整份取代**

當 `metadata.workspace.skills` 存在時，**那個 map 就是這次呼叫的完整 `skills/` 內容**：

- map 裡有的檔案 → 用傳入的內容
- map 裡**沒有**的檔案 → 這次呼叫**看不到**它（即使磁碟上有）
- `skills` 是 `{}` → 這次呼叫**一個 skill 都沒有**（這是合法的測試情境）
- `skills` 這個 key **不存在** → 照常使用磁碟上的 `skills/`

> **為什麼 config 是 merge、skills 是 replace？** 兩者刻意不同，不要「統一一下」：
> config 因為機密被拿掉了，非 merge 不可；skills 沒有機密，而且整份取代才有辦法表達
> 「把某個 reference 檔刪掉試試看」這種實驗。

### 5.4 處理邏輯（步驟）

```
1. parse request body
2. override = metadata.workspace（可能不存在）

3. 如果 override 不存在：
     → 完全照現有流程跑，跳到第 9 步。不要碰任何新程式碼路徑。

4. 準備 config：
     a. 讀磁碟上的 config.json
     b. 用 §2.1 算出 redacted_paths
     c. 把傳入的 config 中位於 redacted_paths 的值移除
     d. deep merge（磁碟的為底，傳入的疊上去）→ effective_config

5. 準備 skills：
     a. 如果傳入了 skills：
          - 對每個 key 做路徑安全檢查（見 §5.5），任何一個不合格 → 回 400
          - 建立一個「這次呼叫專用」的暫存目錄，例如 tempfile.mkdtemp()
          - 把每個 key 當相對路徑寫進去（父目錄不存在就建）
          - effective_skills_dir = 這個暫存目錄
     b. 沒有傳入 skills → effective_skills_dir = 原本的 workspace/skills/

6. 用 effective_config + effective_skills_dir 初始化 agent
     （agent 的行為完全不變，只是設定與 skill 來源換了）

7. 跑 agent 回答 message，trace 照樣寫進 Langfuse，trace id 用 metadata.trace_data.trace_id

8. finally: 刪掉第 5a 步建立的暫存目錄

9. 回傳 {"content": "<agent 的回答>"}
```

**三條硬性要求**

| # | 要求 | 為什麼 |
|---|---|---|
| 1 | 傳入的 config / skills **只影響這一次呼叫** | 它是一次實驗，不是一次部署 |
| 2 | **絕對不可以**把傳入的內容寫回 `config.json` 或 `workspace/skills/` | 那會污染整台 server，也會影響同時進行中的其他呼叫 |
| 3 | 暫存目錄**每個請求各一份**（用 `tempfile.mkdtemp()`，不要用固定路徑如 `/tmp/skills`），且處理完要刪掉 | 同一時間可能有多個請求在跑，固定路徑會互相踩踏 |

### 5.5 路徑安全檢查

傳入的 `skills` 的 key 會被當成檔案路徑寫到磁碟上，**必須先檢查**。以下任一情況 → 回 `400`：

- 含有 `..`
- 以 `/` 開頭（絕對路徑）
- 含有 `\`
- 含有 NUL 字元
- 是空字串

檢查通過後，再確認「暫存目錄 + 這個相對路徑」解析出來的絕對路徑**仍然在暫存目錄底下**
（`Path(tmp, key).resolve().is_relative_to(Path(tmp).resolve())`），不是的話一樣回 `400`。

### 5.6 Output

不變：

```json
{ "content": "客戶 A 上個月的帳單金額是 12,480 元。" }
```

- 請永遠回這個形狀。
- **不要回空字串或全是空白的答案**——呼叫端會把它當成失敗。真的答不出來時，回一句說明文字。

### 5.7 錯誤

| 情況 | 回應 | 備註 |
|---|---|---|
| 正常 | `200` + `{"content": "…"}` | |
| `workspace.config` 不是 object / `workspace.skills` 不是 `{字串: 字串}` | `400` + 說明 | |
| skill 路徑不合格（§5.5）| `400` + 說明 | 要指出是哪一個 key |
| agent 內部錯誤 | `500` + 說明 | |

> 呼叫端**不會重試** 4xx 與 5xx，會直接把你 body 的前 500 字顯示給使用者。**請把原因寫進去。**
> 只有逾時與連線失敗才會被重試（最多 2 次），所以：暫時性的問題請自己重試，不要回 5xx。

### 5.8 其他要注意的

- **body 會很大**：整棵 skills/ 可能有數百 KB。請確認 server 與前面的反向代理的 body size limit **至少 5 MB**。
- **逾時**：呼叫端的預設逾時是 **120 秒**。
- **呼叫端不會送任何 Authorization header**。如果你的 server 需要認證，請回頭通知呼叫端那邊，需要先加設定。
- ⚠️ **安全提醒**：這個改動之後，任何能打到 `/execute` 的人都能透過 config override 改變 agent 的行為
  （除了機密路徑之外）。這台 server 請**不要**曝露在信任邊界之外。

### 5.9 驗收

```bash
# ① 不帶 workspace —— 回歸測試，行為必須與改動前完全相同
curl -s localhost:8080/execute -H 'Content-Type: application/json' -d '{
  "message": "測試問題",
  "metadata": {"trace_data": {"trace_id": "t-001", "session_id": "t-001",
                              "user_id": "alice", "tags": ["smoke"]}}
}' | jq

# ② 只覆蓋 config 的一個值
curl -s localhost:8080/execute -H 'Content-Type: application/json' -d '{
  "message": "測試問題",
  "metadata": {
    "trace_data": {"trace_id": "t-002", "session_id": "t-002", "user_id": "alice", "tags": ["playground"]},
    "workspace": {"config": {"agents": {"defaults": {"temperature": 0.9}}}}
  }
}' | jq

# ③ 覆蓋 skills
curl -s localhost:8080/execute -H 'Content-Type: application/json' -d '{
  "message": "測試問題",
  "metadata": {
    "trace_data": {"trace_id": "t-003", "session_id": "t-003", "user_id": "alice", "tags": ["playground"]},
    "workspace": {"skills": {"skill_A/SKILL.md": "# Skill A\n出現 OVERRIDE-MARKER-12345 這串字。"}}
  }
}' | jq

# ④ 路徑攻擊必須被擋下
curl -s -w '\n%{http_code}\n' localhost:8080/execute -H 'Content-Type: application/json' -d '{
  "message": "x",
  "metadata": {"trace_data": {"trace_id": "t-004"},
               "workspace": {"skills": {"../../etc/evil.md": "x"}}}
}'
```

- [ ] ① 回 200，且 Langfuse 上出現 trace id `t-001`
- [ ] ② 回 200，且 agent **仍然能正常呼叫 LLM**（← 這一項在驗證 API key 沒有被 deep merge 弄丟，是最重要的一項）
- [ ] ② 的 `temperature` 確實變成 0.9，而 `model`、`retries` 等其他值都沒變
- [ ] ③ 回 200，且 trace 裡看得到 `OVERRIDE-MARKER-12345`
- [ ] ③ 之後，磁碟上的 `workspace/skills/skill_A/SKILL.md` **內容沒有被改到**
- [ ] ③ 之後，`GET /get_config_version` 的版本**沒有變**（確認什麼都沒被寫回去）
- [ ] ③ 之後再跑一次 ①，trace 裡**沒有** `OVERRIDE-MARKER-12345`（沒有殘留到下一次呼叫）
- [ ] ④ 回 `400`
- [ ] **併發測試**：同時送 10 個請求，5 個帶不同的 marker、5 個不帶 → 每條 trace 只含它自己那一份
- [ ] 處理完之後，暫存目錄有被刪掉（`ls /tmp` 不會愈積愈多）

---

## 6. 不要做的事

| 不要做 | 原因 |
|---|---|
| 寫入類的 skill / config 端點（`POST`/`PUT`/`DELETE`）| 這次不做。之後要做需要版本控制與 rollback |
| 把傳入的 config / skills 寫回磁碟 | §5.4 要求 2 |
| 用固定路徑（如 `/tmp/skills`）當暫存目錄 | 併發會互相踩踏 |
| 用全域變數 / module-level dict 暫存這次的 override | 同上；請用參數往下傳，或 `contextvars` |
| 把傳入的 config 整份拿去初始化 agent | §5.2：agent 會沒有 API key |
| 讀不到 skill 時回 `200 {"skills": {}}` | §2.3：會讓「沒有 skill」和「路徑設錯」長得一樣 |
| `get_workspace` 只回 `SKILL.md`、略過 `references/` | 使用者要能編輯 reference 檔 |
| 截斷檔案內容或只回摘要 | 使用者要在全文上編輯 |
| 為 workspace override 另開一個端點（如 `/execute_with_workspace`）| 呼叫端打的是 `/execute`；兩個端點會漂移 |
| 新增 `Authorization` 的檢查而不通知呼叫端 | 呼叫端目前不送任何 header，加了就全部打不通 |

---

## 7. 參考實作骨架（FastAPI）

**這是骨架，用來說明結構**；請按你既有的程式碼調整。

```python
# workspace.py --------------------------------------------------------------
import hashlib, json, shutil, subprocess, tempfile
from pathlib import Path

AGENT_ROOT = Path("/app")
CONFIG_PATH = AGENT_ROOT / "config.json"
SKILLS_DIR = AGENT_ROOT / "workspace" / "skills"

SECRET_HINTS = ("api_key", "apikey", "secret", "token",
                "password", "passwd", "credential", "private_key")


# --- §2.1 機密遮罩 ---------------------------------------------------------

def redact(config: dict, prefix: str = "") -> tuple[dict, list[str]]:
    safe, paths = {}, []
    for key, value in config.items():
        path = f"{prefix}{key}"
        if any(hint in key.lower() for hint in SECRET_HINTS):
            paths.append(path)
            continue
        if isinstance(value, dict):
            sub_safe, sub_paths = redact(value, f"{path}.")
            safe[key], paths = sub_safe, paths + sub_paths
        else:
            safe[key] = value
    return safe, paths


def strip_paths(config: dict, paths: list[str]) -> dict:
    """把傳入的 config 中位於機密路徑上的值丟掉（§5.2 的安全規則）。"""
    out = json.loads(json.dumps(config))  # 深拷貝，不動到呼叫者的物件
    for path in paths:
        node, parts = out, path.split(".")
        for part in parts[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict):
            node.pop(parts[-1], None)
    return out


# --- §5.2 deep merge -------------------------------------------------------

def deep_merge(base: dict, incoming: dict) -> dict:
    """base 為底，incoming 疊上去。base 有、incoming 沒有的 key 一律保留。"""
    out = json.loads(json.dumps(base))
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)   # 兩邊都是 dict → 遞迴
        else:
            out[key] = value                          # 純量 / list / None → 取代
    return out


# --- §2.2 版本字串 ---------------------------------------------------------

def read_skills() -> dict[str, str]:
    out = {}
    if not SKILLS_DIR.is_dir():
        return out
    for path in sorted(SKILLS_DIR.rglob("*")):
        if not path.is_file():
            continue
        try:
            out[path.relative_to(SKILLS_DIR).as_posix()] = path.read_text("utf-8")
        except UnicodeDecodeError:
            continue      # 二進位檔跳過，但不讓整個請求失敗
    return out


def _content_hash() -> str:
    h = hashlib.sha256()
    h.update(CONFIG_PATH.read_bytes())
    for rel, text in sorted(read_skills().items()):
        h.update(rel.encode() + b"\0" + text.encode())
    return h.hexdigest()[:7]


def workspace_version() -> str:
    try:
        commit = subprocess.run(
            ["git", "-C", str(AGENT_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(AGENT_ROOT), "status", "--porcelain"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
    except Exception:
        return f"nogit.{_content_hash()}"
    return f"{commit}-dirty.{_content_hash()}" if dirty else commit


# --- §5.4/5.5 這次呼叫專用的 skills 目錄 -----------------------------------

def materialize_skills(skills: dict[str, str]) -> str:
    """把傳入的 skills 寫進一個「這個請求專用」的暫存目錄，回傳路徑。"""
    tmp = Path(tempfile.mkdtemp(prefix="skills-"))   # 每個請求各一份
    root = tmp.resolve()
    for rel, content in skills.items():
        if (not rel or ".." in rel or rel.startswith("/")
                or "\\" in rel or "\0" in rel):
            shutil.rmtree(tmp, ignore_errors=True)
            raise ValueError(f"unsafe skill path: {rel!r}")
        target = (tmp / rel).resolve()
        if not target.is_relative_to(root):
            shutil.rmtree(tmp, ignore_errors=True)
            raise ValueError(f"unsafe skill path: {rel!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return str(tmp)


# main.py -------------------------------------------------------------------
import json
import shutil
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import workspace as ws

app = FastAPI()


@app.get("/get_workspace")
async def get_workspace():
    try:
        raw = json.loads(ws.CONFIG_PATH.read_text("utf-8"))
        safe, redacted = ws.redact(raw)
        return {
            "version": ws.workspace_version(),
            "config": safe,
            "redacted_paths": redacted,
            "skills": ws.read_skills(),      # 讀不到會 raise，不會回空 dict
        }
    except Exception as exc:
        raise HTTPException(500, f"could not read workspace: {exc}") from exc


@app.get("/get_config_version")
async def get_config_version():
    try:
        return {"version": ws.workspace_version()}   # 與上面同一個函式
    except Exception as exc:
        raise HTTPException(500, f"could not compute version: {exc}") from exc


class WorkspaceIn(BaseModel):
    config: dict | None = None
    skills: dict[str, str] | None = None


class TraceData(BaseModel):
    trace_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    tags: list[str] = []


class Metadata(BaseModel):
    trace_data: TraceData = TraceData()
    workspace: WorkspaceIn | None = None     # 不存在就是 None


class ExecuteIn(BaseModel):
    message: str
    metadata: Metadata = Metadata()


@app.post("/execute")
async def execute(payload: ExecuteIn):
    override = payload.metadata.workspace
    disk_config = json.loads(ws.CONFIG_PATH.read_text("utf-8"))

    effective_config = disk_config
    skills_dir, tmp_dir = str(ws.SKILLS_DIR), None

    if override is not None:
        if override.config is not None:
            _, redacted = ws.redact(disk_config)
            incoming = ws.strip_paths(override.config, redacted)  # 機密不接受外來值
            effective_config = ws.deep_merge(disk_config, incoming)  # ← 不是取代！
        if override.skills is not None:
            try:
                tmp_dir = ws.materialize_skills(override.skills)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            skills_dir = tmp_dir

    try:
        answer = await run_agent(                       # 你既有的 agent 進入點
            payload.message,
            config=effective_config,
            skills_dir=skills_dir,
            trace_id=payload.metadata.trace_data.trace_id,      # ← Langfuse trace id
            session_id=payload.metadata.trace_data.session_id,
            user_id=payload.metadata.trace_data.user_id,
            tags=payload.metadata.trace_data.tags,
        )
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)   # 一定要刪

    return {"content": answer}
```
