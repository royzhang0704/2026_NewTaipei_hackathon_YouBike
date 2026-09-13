# 新北市公共自行車營運調度數據視覺化及預測模型

> 2026 新北市 AI 智慧城市黑客松 · 交通局命題「YouBike 智慧調度」

把新北市 YouBike2.0 的歷史數據，變成**未來 3 小時的車輛水位預測**，
再翻成調度人員直接可執行的**風險燈號與派車台數**。

| | |
|---|---|
| 🎬 Live Demo | 〔待補：部署網址〕 |
| 📹 Demo 錄影 | 〔待補：錄影連結〕 |
| 📑 提案簡報 | 〔待補：簡報檔〕 |

---

## 競賽基本資訊

| 項目 | 內容 |
|---|---|
| 賽事 | [2026 新北市 AI 智慧城市黑客松](https://www.digitimes.com.tw/seminar/smartcity_hackathon/) |
| 主辦 | DIGITIMES |
| 組別 | 交通局 |
| 命題 | 【YouBike 智慧調度】新北市公共自行車營運調度數據視覺化及預測模型 |
| 決賽 | 2026/9/12（六）– 9/13（日），新北市政府 603 大禮堂 |
| 資料集 | 新北市資料開放平台 — YouBike2.0 租賃站歷史數據（6 個月） |
| 技術限制 | 開發環境指定 AWS；模型僅限 Amazon Bedrock、Amazon SageMaker AI，不可呼叫外部 API |
| 成果 | 〔待補：名次／獎項〕 |

### 評分標準（交通局組）

| 項目 | 權重 |
|---|---|
| 資料應用性 | 30% |
| 技術可行性 | 25% |
| 完成度 | 25% |
| 主題契合度 | 20% |

決賽評選形式：5 分鐘簡報 + 3 分鐘問答。

### 命題痛點 → 本專案對應

| 命題痛點 | 本專案作法 |
|---|---|
| 尖峰時段「無車可借／無位可還」 | 缺車與滿站雙邊判定，門檻隨站點規模調整 |
| 現行系統只有即時資料，無法預測 | SageMaker DeepAR 時序模型，每 30 分鐘全站預測未來 3 小時水位 |
| 空滿持續發生，缺少警示機制 | 分級風險燈號＋告警清單，附具體調度動作（補車／取車 N 台） |

---

## 團隊成員

| 角色 | 成員 | 負責範圍 |
|---|---|---|
| PM | 林義旻 | 專案管理、提案文件與簡報、繳交範疇確認 |
| SA | 黃一珊 | 需求分析、提案文件與簡報 |
| SD | 鄭明勳 | 後端服務、資料預處理與模型訓練、資料模型與系統圖面（ER／UML） |
| Cloud Engineer | 張志強 | AWS 雲端架構、SageMaker 與後端上雲部署 |
| UI/UX | 陳冠年 | 介面設計、前端畫面實作 |

---

## 功能

- **全市總覽**：地圖呈現 29 個行政區、1,538 站的即時水位與風險燈號，KPI 摘要列
- **告警清單**：依風險等級、持續時間、站點規模排序，標示已調派台數
- **單站檢視**：過去 9 小時實況 + 未來 3 小時預測區間（q19／q50／q90）與建議動作
- **調度助理**：以 Amazon Bedrock AgentCore 回答調度問題；回覆中的數字經後端比對驗證
- **調度單**：勾選來源站、調整台數後下單，地圖畫出調度路線；可撤銷，排程每輪自動收單
- **Demo 回放**：以歷史數據驅動虛擬時鐘，重現一天內的預測與告警變化

---

## 技術棧

| 層 | 技術 |
|---|---|
| 前端 | React 19、Vite、TypeScript、Tailwind v4、MapLibre（react-map-gl）、ECharts、TanStack Query、zustand |
| 後端 | Python 3.12、FastAPI、uvicorn、psycopg、uv |
| 資料庫 | PostgreSQL 17 |
| 預測模型 | Amazon SageMaker DeepAR（context 24 小時、預測 3 小時，30 分鐘一格） |
| 生成式 AI | Amazon Bedrock AgentCore |
| 雲端 | CloudFront、S3、ALB、ECS、RDS |

---

## 專案結構

```
.
├── backend/     FastAPI 後端、排程、預測與調度助理（系統架構見 backend/README.md）
├── frontend/    React 前端
└── docs/        提案與設計文件（PDF）
```

---

## 快速開始

需求：Python 3.12 + [uv](https://docs.astral.sh/uv/)、Node.js、PostgreSQL 17、可用的 AWS 帳號（SageMaker／Bedrock）。

```bash
# 後端
cd backend
cp .env.example .env              # 填入 AWS 憑證與 AgentCore Harness ARN
uv run python aws/deploy_endpoint.py
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端（另開終端）
cd frontend
npm install
npm run dev                       # http://localhost:5173，/api 轉到 127.0.0.1:8000
```

資料庫還原、Demo 回放、排程與排查細節見 [`backend/README.md`](backend/README.md) 與 [`backend/COMMANDS.md`](backend/COMMANDS.md)。

> ⚠️ SageMaker endpoint 按秒計費，Demo 結束後請刪除。

---

## 文件

| 文件 | 內容 |
|---|---|
| [`docs/初步提案構想_v1.pdf`](docs/初步提案構想_v1.pdf) | 專案初期構想（架構與內容以最終版本為準） |
| [`docs/AWS-雲端架構.pdf`](docs/AWS-雲端架構.pdf) | AWS 雲端架構圖（最終版；README 內嵌用 `.svg`） |
| [`docs/資料預處理_v1.pdf`](docs/資料預處理_v1.pdf) | 資料清理、重採樣與品質檢核 |
| [`docs/模型訓練_v1.pdf`](docs/模型訓練_v1.pdf) | DeepAR 訓練設定與評估 |
| [`docs/資料模型與系統圖面_v2.pdf`](docs/資料模型與系統圖面_v2.pdf) | ER、DB schema、元件圖、時序圖、狀態圖 |
| [`backend/README.md`](backend/README.md) | 模型、風險判定、API、已知限制 |
| [`backend/調度助理-AgentCore串接.md`](backend/調度助理-AgentCore串接.md) | 調度助理串接說明 |

---

## 授權

[MIT License](LICENSE)

資料來源：新北市資料開放平台。依賽會規則，競賽提供之資料授權主辦單位用於活動宣傳及後續開發。
