# 系統開發學習筆記：大綱

> 狀態：**已經使用者確認**，可以進入討論層（使用者挑一篇開始）。
> 共 11 章、60 篇。每篇一個獨立觀念，目標閱讀 15～20 分鐘；每章最後一篇是整合練習。

## 怎麼讀這份大綱

**技術棧（從 repo 偵測）**：Backend = Python 3.12 + FastAPI + SQLAlchemy 2（async, asyncpg）+ Alembic + Pydantic；DB = PostgreSQL 16；Frontend = React 18 + Vite（無 router、無 state library）；容器 = Docker + Docker Compose + nginx；部署 = Kubernetes manifests；CI = Azure Pipelines；認證 = Keycloak（OIDC / JWT）；即時推播 = SSE。筆記範例沿用這套技術棧。

**篇編號**：`章(2位)-小節(1位)-篇(2位)`。成稿位置：`notes/<章>/<章>-<小節>/<篇編號>.md`，例如 `notes/01/01-2/01-2-01.md`。暫存檔：`sessions/<篇編號>.md`。

**repo 涵蓋狀態**（用來讓你一眼看到 repo 缺了什麼；括號內是查證到的位置）：

- **有做**：repo 有實作這個觀念
- **做法不同**：repo 有處理同一個問題，但選了另一種做法（討論時再評價取捨）
- **未做**：repo 沒有實作（觀念仍然必備，所以照樣納入）

**進度**：未開始 / 討論中 / 已完成

---

## 第 0 章　全貌地圖

> 位置：整張地圖本身。以「一個請求從瀏覽器到 DB 再回來」為主線，後面每一章都是在這條路上的某一站。

### 0-1　一個請求的旅程

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 00-1-01 | 一個請求的一生 | 按下按鈕之後，請求依序經過瀏覽器、nginx、後端、DB，再原路回來；每一站在做什麼 | 有做（`frontend/nginx.conf.template` → `backend/app/main.py` → `backend/app/db.py`） | 討論中 |
| 00-1-02 | 整合練習：用 curl 追一個請求 | 用 curl 手動走過每一層，親眼看到每一站的輸入與輸出 | 有做（`docker-compose.yml` 可起整套） | 未開始 |

---

## 第 1 章　資料庫

> 位置：地圖最底層。請求最後抵達的地方，也是資料唯一被長期保存的地方。

### 1-1　資料建模

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 01-1-01 | 關聯式模型與 Schema 設計 | 為什麼資料要拆成多張表？一對多、多對多、關聯表怎麼把現實世界拆開再接起來 | 有做（`backend/app/models.py`：EvalSet→Question→Run→QuestionResult、`EvalSetRole` 關聯表） | 未開始 |
| 01-1-02 | 約束與參照完整性 | PK / FK / UNIQUE / NOT NULL 與 `ON DELETE CASCADE` vs `SET NULL`：讓資料庫替你守住「不可能的資料不會出現」 | 有做（`models.py` 的 `UniqueConstraint`、`ForeignKey(..., ondelete=...)`） | 未開始 |
| 01-1-03 | SQL、ORM 與 N+1 | ORM 幫你做了什麼、藏了什麼；一行程式為什麼會偷偷變成一百個查詢 | 有做（SQLAlchemy 2；`backend/app/routers/export.py` 用 `selectinload`） | 未開始 |

### 1-2　交易與併發

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 01-2-01 | Transaction 與 ACID | 「一組操作要嘛全成功、要嘛全沒發生」是怎麼做到的，以及 commit / rollback 的實際含意 | 有做（`backend/app/db.py` 對 commit / rollback 的取捨） | 未開始 |
| 01-2-02 | 併發問題、Isolation 與鎖 | 兩個請求同時改同一筆資料會怎樣；isolation level、MVCC、悲觀鎖與樂觀鎖怎麼選 | 做法不同（靠 UNIQUE + `on_conflict_do_nothing`，見 `backend/app/services/user_settings.py:243`；未使用顯式鎖或版本欄位） | 未開始 |

### 1-3　效能與演進

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 01-3-01 | Index 的原理 | B-tree 為什麼讓查詢變快、什麼時候反而拖慢寫入、怎麼看 EXPLAIN | 有做（`backend/alembic/versions/0005_list_indexes.py`） | 未開始 |
| 01-3-02 | Migration：Schema 怎麼隨版本演進 | 程式碼會 git 回滾，資料庫不會；migration 如何讓 schema 有版本、可重現、可上線 | 有做（`backend/alembic/versions/0001`～`0017`、`backend/docker-entrypoint.sh`） | 未開始 |
| 01-3-03 | 整合練習：設計並遷移一個小 schema | 從需求出發設計 3～4 張表，加約束與索引，用 migration 建出來並用 SQL 驗證 | 有做（可對照 `models.py` + `alembic/`） | 未開始 |

---

## 第 2 章　Backend

> 位置：夾在 nginx 與 DB 之間的那一站。接收請求、決定要不要放行、去 DB 拿資料、組成回應。

### 2-1　API 設計

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 02-1-01 | HTTP 解剖 | 一個請求與回應到底長什麼樣：method、path、header、body、status code，以及底層是 TCP 上的文字 | 有做（FastAPI 全站） | 未開始 |
| 02-1-02 | REST 資源設計、Status Code 與分頁 | URL 怎麼設計、該回哪個 status code、清單怎麼分頁（offset vs keyset） | 做法不同（offset/limit 分頁，`backend/app/routers/runs.py:281`；資料量大時的代價再展開） | 未開始 |
| 02-1-03 | 驗證與錯誤處理 | 為什麼「在邊界一次驗證」比到處檢查好；Pydantic schema 怎麼運作；錯誤該回什麼格式 | 有做（`backend/app/schemas.py` 79 個 model；未見自訂全域 exception handler） | 未開始 |

### 2-2　應用架構與併發

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 02-2-01 | Web Server、async 與 Connection Pool | uvicorn / ASGI / event loop 怎麼同時服務很多請求；DB 連線池為什麼才是真正的併發上限 | 有做（`backend/app/db.py`、`backend/Dockerfile` 的 uvicorn） | 未開始 |
| 02-2-02 | 分層與依賴注入 | router / service / model 各管什麼；`Depends` 怎麼把「每個請求一個 DB session」注入進來 | 有做（`backend/app/routers/`、`backend/app/services/`、`db.py:get_session`） | 未開始 |
| 02-2-03 | 外部服務：介面隔離、Timeout 與 Retry | 呼叫別人的服務一定會慢、會壞；怎麼用介面隔離它、設 timeout、決定誰負責 retry | 有做（`backend/app/integrations/base.py` / `fake.py` / `real/`；retry 在 `real/optimizer.py`） | 未開始 |

### 2-3　長時間任務與即時推播

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 02-3-01 | 背景任務：進程內 task vs Job Queue | 請求結束後還要繼續跑的工作放哪；in-process 的便利與代價、什麼時候該上 queue | 做法不同（`asyncio.create_task`，`backend/app/orchestrator.py`；因此部署被限制為單副本） | 未開始 |
| 02-3-02 | 崩潰恢復與狀態機 | 進程重啟時，「跑到一半」的工作怎麼辦；用明確的狀態機與啟動時清理（reaper）讓系統自癒 | 有做（`backend/app/main.py:reap_interrupted_runs`） | 未開始 |
| 02-3-03 | SSE、WebSocket 與 Polling | 伺服器怎麼把進度推給瀏覽器；三種做法的取捨；慢讀者與斷線重連怎麼處理 | 有做（`backend/app/sse.py`、`backend/app/routers/runs.py`） | 未開始 |
| 02-3-04 | 整合練習：帶進度串流的背景工作 API | 寫一個最小 FastAPI：POST 啟動工作、背景執行、SSE 回報進度、重啟後狀態正確 | 有做（可對照 `orchestrator.py` + `sse.py`） | 未開始 |

---

## 第 3 章　Frontend

> 位置：請求的起點與終點。瀏覽器拿到程式、發出請求、把回應變成畫面。

### 3-1　瀏覽器與前端基礎

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 03-1-01 | SPA 與建置：瀏覽器怎麼拿到並執行前端 | HTML / JS / CSS 怎麼變成畫面；為什麼要 build、bundle、檔名加 hash | 有做（`frontend/vite.config.js`、`frontend/Dockerfile` builder stage） | 未開始 |
| 03-1-02 | React：狀態驅動 UI 與元件責任 | 「畫面 = f(state)」的心智模型；state 放哪一層；規則邏輯為什麼要抽成純函式 | 有做（`frontend/src/App.jsx`、`frontend/CLAUDE.md` 的 pure module 規則） | 未開始 |

### 3-2　前端與後端互動

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 03-2-01 | 呼叫 API：async、Loading 與錯誤狀態 | fetch 的運作、一個請求的三種狀態（載入中／成功／失敗）、統一封裝 | 有做（`frontend/src/api.js`） | 未開始 |
| 03-2-02 | 同源政策與 CORS | 瀏覽器為什麼擋跨來源請求；CORS 是什麼、什麼時候可以整個繞開 | 有做（`backend/app/main.py` CORSMiddleware；nginx 同源代理） | 未開始 |
| 03-2-03 | 前端接收 SSE 串流 | EventSource 與 fetch 讀串流的差別；斷線重連、事件遺失時的 resync | 有做（`frontend/src/api.js:openStream`） | 未開始 |
| 03-2-04 | 前端狀態放哪、路由怎麼做 | 記憶體 / URL / localStorage / 後端各自適合放什麼；沒有 router 函式庫時的 hash 路由 | 做法不同（`frontend/src/useHashRoute.js`、`useSessionState.js`、`wizard_draft.js`） | 未開始 |
| 03-2-05 | 整合練習：React 前端接 API 與 SSE | 寫一個最小 Vite + React：列表、送出、串流進度，處理 loading / error | 有做（可對照 `api.js` + `components/`） | 未開始 |

---

## 第 4 章　認證與安全

> 位置：橫跨每一站的能力。瀏覽器持有憑證、nginx 轉送、後端驗證、DB 存放機密。放在前後端之後，因為需要兩邊都看過才講得清楚。

### 4-1　認證

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 04-1-01 | 認證概念：Session、Token 與 CSRF | 「你是誰」怎麼在無狀態的 HTTP 上被記住；session cookie 與 bearer token 的取捨（含 CSRF 為何存在） | 做法不同（bearer token in header，`backend/app/auth.py`；未用 session cookie） | 未開始 |
| 04-1-02 | JWT：簽章、Claims 與後端驗證 | JWT 解決什麼問題、簽章怎麼驗、JWKS 是什麼、驗證時要檢查哪些欄位 | 有做（`backend/app/keycloak.py:verify_token`） | 未開始 |
| 04-1-03 | OIDC / SSO 與 Token 生命週期 | 為什麼登入要交給 identity provider；authorization code 流程；access / refresh token 各自的壽命與風險 | 有做（`frontend/src/auth.js`（keycloak-js）、`backend/app/agent_sso.py`） | 未開始 |

### 4-2　授權與機密

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 04-2-01 | 授權模型：角色與資源層級檢查 | 認證之後「你能做什麼」；全域角色 vs 每個資源自己的角色；防 IDOR 的檢查放哪 | 有做（`backend/app/auth.py:role_for/require_owner`、`EvalSetRole`） | 未開始 |
| 04-2-02 | 密碼與機密處理：Hash、加密與環境變數 | 密碼為什麼存 hash 不存密文；需要還原的機密怎麼加密；secrets 不該出現在哪裡 | 做法不同（密碼委派給 Keycloak，未自行 hash；機密以 Fernet 加密，`backend/app/services/user_secrets.py`） | 未開始 |

### 4-3　常見攻擊與濫用

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 04-3-01 | 注入與 XSS | SQL injection 與 XSS 的共同根源（資料被當成程式）；參數化查詢、輸出跳脫、CSP | 做法不同（SQL 走 ORM 參數化；前端用 `dangerouslySetInnerHTML` 渲染 markdown，`frontend/src/components/Documentation.jsx:112`，來源為自家文件；未見 sanitize 或 CSP header） | 未開始 |
| 04-3-02 | Rate Limiting 與濫用防護 | 擋住暴力嘗試與失控的 client；固定窗口、滑動窗口、token bucket 怎麼選、放哪一層 | 未做 | 未開始 |
| 04-3-03 | 整合練習：帶 JWT 驗證與角色檢查的最小 API | 自己簽發與驗證 JWT，做 owner / viewer 兩種角色的資源，加上簡單限流 | 有做（可對照 `auth.py` + `keycloak.py`） | 未開始 |

---

## 第 5 章　測試

> 位置：不在請求路徑上，但守著每一站。放在容器化與 CI/CD 之前，因為 CI 存在的目的就是自動跑測試。

### 5-1　測試

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 05-1-01 | 測試策略：測什麼、怎麼分層 | 單元 / 整合 / E2E 的成本與收益；為什麼「測行為不測實作」；測試該在什麼時候寫 | 有做（`backend/tests/` 約 80 個檔；前端 `node --test`；E2E 未做） | 未開始 |
| 05-1-02 | 測試替身與 DB 測試 | fake、mock、stub 的差別；用 fake 實作隔離外部服務；DB 測試該用真的 Postgres 還是替身 | 有做（`backend/app/integrations/fake.py`、`backend/tests/conftest.py`、CI 內起 Postgres） | 未開始 |
| 05-1-03 | 前端純函式測試與契約測試 | 沒有 DOM 也能測邏輯；把「靜默失敗的約定」（CSS token、prop 詞彙、lockfile）變成測試 | 有做（`frontend/src/css_contract.test.js`、`ui_vocabulary.test.js`、`lockfile_contract.test.js`） | 未開始 |
| 05-1-04 | 整合練習：替一個小服務寫完整測試 | 對一個含 DB 與外部呼叫的小 API，寫出單元、fake 替身、DB 整合三層測試 | 有做（可對照 `backend/tests/`） | 未開始 |

---

## 第 6 章　容器化

> 位置：把前面每一站（後端、前端、DB）各自包成可搬走的盒子。

### 6-1　Docker 與 Compose

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 06-1-01 | 容器與 Image：為什麼、Layer 與 Cache | 容器解決「在我機器上可以跑」；image 是分層檔案系統；Dockerfile 指令順序為什麼影響建置速度 | 有做（`backend/Dockerfile`：先 COPY requirements 再 COPY 原始碼） | 未開始 |
| 06-1-02 | Dockerfile 實戰：多階段建置與最小權限 | multi-stage 怎麼把建置工具留在成品外；為什麼不該用 root 跑；pin 版本的理由 | 有做（`frontend/Dockerfile` dev/builder/runner 三階段、非 root） | 未開始 |
| 06-1-03 | Docker Compose：多服務、網路與 Healthcheck | 多個容器怎麼互相找到對方（DNS、network）；`depends_on` 與 healthcheck 怎麼保證啟動順序 | 有做（`docker-compose.yml`） | 未開始 |
| 06-1-04 | 開發與正式形態：Override、Volume 與資料持久化 | 同一份 compose 怎麼切開發 / 正式；容器刪了資料為什麼不會不見（volume） | 有做（`docker-compose.override.yml`、`docker-compose.prod.yml`、`agentopt_pgdata`） | 未開始 |
| 06-1-05 | 整合練習：把前端、後端、DB 用 Compose 跑起來 | 為一個最小三層應用寫 Dockerfile 與 compose，一行指令起整套 | 有做（可對照 `Makefile` + `scripts/dev.sh`） | 未開始 |

---

## 第 7 章　部署與反向代理

> 位置：瀏覽器與後端之間的那一站（nginx），以及整套東西跑在哪（Kubernetes）。

### 7-1　反向代理與部署

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 07-1-01 | 反向代理：nginx 在做什麼 | 為什麼前面要放一層代理；`proxy_pass`、同源、buffering 與 timeout（SSE 為什麼會被代理卡住） | 有做（`frontend/nginx.conf.template`） | 未開始 |
| 07-1-02 | 靜態資源快取與執行期設定注入 | 檔名有 hash 才能快取一年；「一個 image 跑所有環境」怎麼靠啟動時產生 config 檔做到 | 有做（`nginx.conf.template` 的 `expires`、`frontend/docker-entrypoint.d/10-app-config.sh`） | 未開始 |
| 07-1-03 | TLS / HTTPS 與 Ingress | HTTPS 保護什麼、憑證怎麼運作、在哪一層終止 TLS | 有做（設定層：`deploy/k8s/50-ingress.yaml`；憑證簽發不在 repo 內） | 未開始 |
| 07-1-04 | Kubernetes 基礎與設定 / Secret 管理 | Pod / Deployment / Service / Ingress 各是什麼；ConfigMap 與 Secret 怎麼把設定與程式分開 | 有做（`deploy/k8s/*.yaml`） | 未開始 |
| 07-1-05 | 健康檢查、部署時 Migration 與擴展限制 | liveness / readiness / startup probe 差在哪；migration 在部署流程哪裡跑；狀態放進程內就不能水平擴展 | 有做（`40-backend-deployment.yaml`、`35-migrate-job.yaml`；`replicas: 1` 與 `Recreate` 有明確說明；probe 只有單一 `/health`） | 未開始 |
| 07-1-06 | 整合練習：把最小應用部署到本機 Kubernetes | 用 kind / minikube 部署前端、後端、DB，含 probe、Secret 與 migration Job | 有做（可對照 `deploy/k8s/`） | 未開始 |

---

## 第 8 章　CI/CD

> 位置：程式碼進入主幹之後到跑在伺服器之前的自動化流水線。

### 8-1　持續整合與交付

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 08-1-01 | CI/CD 概念與 Pipeline：在 Image 內跑測試 | CI 與 CD 各是什麼、pipeline 的階段；為什麼要「測你即將出貨的那個 image」 | 有做（`backend/azure-pipelines.yml`、`frontend/azure-pipelines.yml`、`db/azure-pipelines.yml`） | 未開始 |
| 08-1-02 | 品質關卡：Lint、型別檢查與可重現建置 | 自動化檢查能擋掉哪類問題；lockfile 為什麼是可重現建置的關鍵 | 未做（明確註記無 ruff / black / mypy，見 `backend/azure-pipelines.yml:159`；lockfile 以 `pnpm --frozen-lockfile` 與契約測試保護，屬於有做） | 未開始 |
| 08-1-03 | 映像版本與部署策略：Tag、Registry、Rolling 與 Rollback | image tag 怎麼取才能回滾；rolling / recreate / blue-green 的取捨 | 做法不同（build id + commit sha 雙 tag、刻意不用 `latest`；CD 發布 pipeline 不在 repo 內，k8s 用 `Recreate`） | 未開始 |
| 08-1-04 | 整合練習：寫一條 build → test → push 的 pipeline | 為最小應用寫一條 CI pipeline：路徑觸發、起 Postgres 跑測試、PR 不 push image | 有做（可對照 `backend/azure-pipelines.yml`） | 未開始 |

---

## 第 9 章　可觀測性與維運

> 位置：不在請求路徑上，但系統上線後你唯一的眼睛。放在最後，因為需要先有東西在跑才有東西可觀測。

### 9-1　Log、指標與備份

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 09-1-01 | Logging：等級、格式與 Request ID | log 該記什麼、不該記什麼；結構化 log 與 request id 怎麼讓你在一堆 log 中追一個請求 | 做法不同（`backend/app/main.py` 的純文字 `basicConfig`；未見 structured logging 或 request id） | 未開始 |
| 09-1-02 | Metrics、Health 與告警 | 「壞了」與「變慢了」怎麼被發現；RED / USE 指標、告警該設在哪 | 未做（僅 `/health`，`backend/app/main.py`） | 未開始 |
| 09-1-03 | 備份與災難復原 | 沒測過還原的備份不算備份；RPO / RTO；Postgres 備份怎麼做 | 未做（repo 內無備份機制；僅有 `agentopt_pgdata` volume） | 未開始 |
| 09-1-04 | 整合練習：替一個小服務加上 log、metrics 與備份 | 加 request id、暴露 `/metrics`、做一次備份與還原演練 | 未做 | 未開始 |

---

## 第 10 章　從零設計一個系統

> 位置：把整張地圖收回來。第 0 章是「看懂一個系統」，這一章是「自己設計一個」。

### 10-1　設計與總整理

| 篇編號 | 標題 | 一句話摘要 | repo 涵蓋狀態 | 進度 |
|---|---|---|---|---|
| 10-1-01 | 從需求到設計：流程與 Scale-out 取捨 | 需求 → 資料模型 → API → 前端 → 部署的設計順序；單進程設計的邊界，以及要擴展時第一步該改什麼 | 有做（單進程 + 記憶體 pub/sub 的取捨，見 `deploy/k8s/40-backend-deployment.yaml` 註解） | 未開始 |
| 10-1-02 | 最終整合練習：蓋一個小型完整系統 | 從空目錄出發：DB + 後端 + 前端 + 認證 + 測試 + Compose + CI，一次做完 | 有做（全 repo 可對照） | 未開始 |

---

## 統計

| 章 | 篇數 |
|---|---|
| 0 全貌地圖 | 2 |
| 1 資料庫 | 8 |
| 2 Backend | 10 |
| 3 Frontend | 7 |
| 4 認證與安全 | 8 |
| 5 測試 | 4 |
| 6 容器化 | 5 |
| 7 部署與反向代理 | 6 |
| 8 CI/CD | 4 |
| 9 可觀測性與維運 | 4 |
| 10 從零設計一個系統 | 2 |
| **合計** | **60** |
