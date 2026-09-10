# 調度助理 × AgentCore 串接

## 一、這是什麼

調度助理 widget 呼叫後端 `POST /api/v1/assistant/chat`（SSE）。`app/service/assistant_service.py`：

```
              問句 + 畫面脈絡（在看哪區 / 哪站 / 虛擬時鐘）
                                │
        proxy 先依脈絡撈好即時資料（alert_service）
                                │
        連同問句丟給 AgentCore Harness（Bedrock）
                                │
        Harness 綜合現況 + 給行動建議 + 用知識庫工具拉 SOP 附出處
        proxy 把撈到的站點組成「開啟站點」按鈕
```

- **所有問題走同一條路（Bedrock）** —— 不怕打錯字 / 口語 / 複合問句，沒有路由 bug。代價：每題 2–5 秒。
- 數字精確靠「proxy 撈好塞進 prompt + 指示照原文引用」，不是叫 LLM 回想。詳見下方「資料包怎麼給」。
- 亂打 / 無意義輸入（`123`、`?!?`、`asdf`）：`_is_noise()` 前置擋掉，回「看不太懂你的問題，可以再說清楚一點嗎？」，不打 Harness。
- 未設 `ASSISTANT_HARNESS_ARN`（正式環境不該發生）：領域內問題回 error「調度助理暫時無法回應，請稍後再試」；
  純離題回「僅回答 YouBike 調度相關問題」+ 相關問題按鈕。
- 呼叫 Harness 失敗（throttle / 5xx）：同一句「暫時無法回應，請稍後再試」，前端顯示錯誤卡 + 重試。
- 前端不用改程式（mock 已移除，一律走後端）；只要後端有在跑。

### 資料包怎麼給（`_gather_context` → `_build_agent_prompt`）

proxy 不給 LLM 工具權限，而是**把查好的資料寫成文字，當使用者訊息的一部分丟過去**：

1. 看 `context.town_code` / `station_uid`（前端本來就送）→ 決定要撈全市還是某區/某站。
2. **帶 `town_code` 的 scoped 查詢**（`alert_service.alerts(town_code=…)`）—— 完整、有該區自己的補取台數，
   絕不會被「全市 limit 1000」在尖峰時截斷。
3. 縮成一小包 JSON：`{"行政區":{空站,滿站,高/中風險,建議補車總量,最優先處理:"站名",待處理站:[…依優先序]}, "全市對照":{數量}}`。
   - `最優先處理` 是明確欄位 —— prompt 要求 LLM 直接用，不要自己從清單挑（挑錯過）。
   - `全市對照` 只放數量，**不放台數** —— 避免全市總量被誤植進行政區的句子（3877 bug）。
4. `_build_agent_prompt` 把 JSON 包成一段文字 + 回答規則（純文字不用 Markdown、≤5 句、數字照原文、問行政區只用行政區區塊…）。
5. 整段當 `messages=[{role:user, content:…}]` 傳給 `invoke_harness`。

---

## 二、AWS 端已建好的資源（Region：ap-northeast-1）

| 資源 | 名稱 / ARN |
|---|---|
| S3 bucket | `imsoft-ubike-agentcore-kb` |
| Managed Knowledge Base | `imsoft-ubike-agentcore-kb`（ID `RFL4TON1NT`） |
| KB data source | `imsoft-ubike-agentcore-kb-src`（S3，已 Sync） |
| Gateway | `imsoft-ubike-agentcore-gateway` |
| Gateway target | `imsoft-ubike-agentcore-kb-target`（MCP → KB connector） |
| Harness | `imsoft_ubike_agentcore_harness`，model：Amazon Nova 2 Lite |
| Harness ARN | `arn:aws:bedrock-agentcore:ap-northeast-1:597671487936:harness/imsoft_ubike_agentcore_harness-ZBEN5wmyW5` |

- KB 內容 = `backend/kb/01~04-*.md`（`_harness_system_prompt.txt` 是 Harness 的 system prompt，不進 KB）。
- Harness playground 已實測：問「調度台數為什麼不是補到剛好脫離紅區」會呼叫 `Kb-Target Retrieve`、回答引用 `02` / `03` 文件。

改 KB 內容的流程：改 `backend/kb/*.md` → 重新上傳到 S3 bucket → KB 頁面按 Sync。

---

## 三、開一組 IAM 憑證給後端

1. IAM → Users → **Create user**，名稱 `imsoft-ubike-agentcore-invoker`。
2. 附一個 inline policy：

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       { "Effect": "Allow",
         "Action": ["bedrock-agentcore:InvokeHarness", "bedrock-agentcore:InvokeAgentRuntime"],
         "Resource": "*" },
       { "Effect": "Allow",
         "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
         "Resource": "*" }
     ]
   }
   ```

3. 該 user → Security credentials → **Create access key** → 用途選 **「Local code」**（本機開發環境）。
4. 記下 **Access key ID** 與 **Secret access key**（Secret 只顯示一次）。

（用自己的 AWS 帳號，不要用工作坊配發帳號 —— 臨時憑證會過期。）

---

## 四、填 backend/.env

`backend/.env` 已存在（有 TDX 兩行），**不要 `cp .env.example` 覆蓋，在後面加**：

```
AWS_REGION=ap-northeast-1
AWS_ACCESS_KEY_ID=<步驟三的>
AWS_SECRET_ACCESS_KEY=<步驟三的>
ASSISTANT_HARNESS_ARN=arn:aws:bedrock-agentcore:ap-northeast-1:597671487936:harness/imsoft_ubike_agentcore_harness-ZBEN5wmyW5
```

REGION / ARN 直接照抄，KEY / SECRET 填步驟三的。`.env` 已被 `.gitignore` 擋，不會進版控。

---

## 五、重啟後端

```bash
cd backend
pkill -f "uvicorn app.main:app"
DEMO_SPEED=60 uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

- `DEMO_SPEED` 要跟 tick 迴圈同值（回放 demo 時；沒跑回放就拿掉）。
- 改 `.py` 有 `--reload` 會自動吃；**改 `.env`（憑證）一定要 pkill 全重啟**。

---

## 六、前端

`npm run dev` 的 Vite 已把 `/api` proxy 到
`http://127.0.0.1:8000`，重啟 dev server 即可。

---

## 七、測試

開右下角調度助理：

| 問法 | 預期 |
|---|---|
| 現在全市概況 | 一句話帶數量 + 補取台數，與 KPI 條一致 |
| 三重區目前狀況 | 用三重「自己的」補車台數（不是全市）；最急站 = 主動警示第 01 筆 |
| 板橋現在該怎麼調度 | 綜合現況 + 一個明確行動建議 + 該區急站按鈕 |
| 空站門檻怎麼定的 / 台數為什麼不是補到剛好 | KB 回答，純文字、≤5 句，結尾「來源：02-…md」 |
| 板僑（打錯字） | 用「正在看板橋」脈絡推測，或反問一句確認 |
| 123 / ?!? | 「看不太懂你的問題，可以再說清楚一點嗎？」+ 相關問題按鈕 |
| 今天天氣如何 | 「僅回答 YouBike 調度相關問題」+ 相關問題按鈕 |

驗收重點：**行政區的數字要跟主控台的 KPI 條、主動警示面板對得上**（同一個時刻）。
卡住看後端 log（`logging.getLogger("assistant")`）。

### 已知限制

- 每題 2–5 秒（Bedrock 往返）。
- `_is_noise` 只擋純數字 / 標點 / 短英文亂碼；中文亂打（「哈哈哈」）仍會進 Harness。
- 多輪脈絡靠 `runtimeSessionId`（Harness 端 Memory），proxy 只送最新一句。

---

## 八、之後正式部署

前後端上 AWS 後：

- 後端 proxy 帶著 IAM role（或 `.env` 憑證）即可，`invoke_harness` 走網路呼叫，跟後端在哪區無關。
- 前端分網域部署時設 `VITE_ASSISTANT_URL=<後端網址>/api/v1/assistant/chat`。
- 進階：把 `/alerts`、`/stations/{uid}/day` 用 Gateway 的 REST/OpenAPI target 掛成 Harness 的工具，即時問答也交給 Agent（需要後端有對外 URL）。目前即時那半在 proxy 內處理，不需要這步。

---

## 九、決賽後清理（避免持續計費）

依序刪：Harness → Gateway（先刪 target 再刪 gateway）→ Knowledge Base（含 data source）→ S3 bucket → 為這些自動建的 IAM service role + 步驟三的 IAM user。

先設 AWS Budgets 告警（$10 / $50 兩檔）。即時查詢不碰 Bedrock；只有知識問答用 Nova 2 Lite，每題約 $0.01。
