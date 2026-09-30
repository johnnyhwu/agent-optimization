# Backlog

## 進階備選

不進主幹（框架細節，或只在特定情境才用得到）的觀念。之後要擴充，從這裡挑。

### 資料與後端

- **Keyset / cursor 分頁深入**：offset 分頁在大表上的代價與 keyset 的實作細節（主幹只在 02-1-02 講取捨）
- **Job queue 實作（Celery / RQ / Arq + Redis）**：主幹只講何時該用（02-3-01），不講怎麼架
- **Redis 與快取策略**：cache-aside、失效、雪崩與穿透
- **訊息匯流排 / 共享 event bus**：讓 SSE 與背景任務可以水平擴展（10-1-01 只點出需要它）
- **Idempotency key**：讓重送的請求不會重複執行
- **檔案上傳與物件儲存（S3 類）**：大檔、串流上傳、預簽名 URL
- **DB 讀寫分離、replication、partition**
- **全文檢索**（Postgres FTS / Elasticsearch）
- **GraphQL / gRPC**：與 REST 的取捨
- **WebSocket 實作**：主幹只比較取捨（02-3-03）

### 安全

- **執行不受信任程式碼（sandbox）**：容器隔離、非 root uid、RLIMIT、去除環境變數（repo 有實作：`backend/app/sandbox/`）
- **CSP 與其他安全 header 深入**
- **依賴與 image 漏洞掃描、supply chain 安全**
- **Secrets 管理工具（Vault、雲端 KMS）**

### 前端

- **設計 token 與 CSS 架構**
- **無障礙（accessibility）基礎**
- **Router / state 函式庫**（React Router、Redux、TanStack Query）：主幹講沒有它們時怎麼做（03-2-04），不教它們的 API
- **SSR / Next.js**：與 SPA 的取捨
- **Playwright E2E 測試**

### 部署與維運

- **Helm / Kustomize / GitOps（Argo CD）**
- **Terraform 等 IaC**
- **藍綠、金絲雀部署與 feature flag**
- **CDN**
- **OpenTelemetry 與分散式追蹤**
- **負載測試與效能剖析**

## 先放著的旁支

討論中你選擇「先放著」的主題，主線走完後我會主動提醒。（目前為空）
