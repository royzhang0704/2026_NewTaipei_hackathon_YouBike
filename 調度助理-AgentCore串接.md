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
        （問的區 ≠ 畫面正在看的區時，另給一顆「切到 X 區地圖」filter_town 按鈕，不自動切）
```

- **所有問題走同一條路（Bedrock）** —— 不怕打錯字 / 口語 / 複合問句，沒有路由 bug。代價：每題 2–5 秒。
- 數字精確靠「proxy 撈好塞進 prompt + 指示照原文引用」，不是叫 LLM 回想。詳見下方「資料包怎麼給」。
- 亂打 / 無意義輸入（`123`、`?!?`、`asdf`）：`_is_noise()` 前置擋掉，回「看不太懂你的問題，可以再說清楚一點嗎？」，不打 Harness。
- 未設 `ASSISTANT_HARNESS_ARN`（正式環境不該發生）：領域內問題回 error「調度助理暫時無法回應，請稍後再試」；
  純離題回「僅回答 YouBike 調度相關問題」+ 相關問題按鈕。
- 呼叫 Harness 失敗（throttle / 5xx）：同一句「暫時無法回應，請稍後再試」，前端顯示錯誤卡 + 重試。
- 前端不用改程式（mock 已移除，一律走後端）；只要後端有在跑。

### 一次請求實際走的路（LLM vs 程式）

**正常情況下，答案那段文字是 Nova（Bedrock Harness）寫的。** 這支程式碼負責「餵給它什麼」和
「檢查它吐什麼」，不自己寫答案。`chat_events()` 流程：

```
chat_events(messages, ctx)
 ├─ _last_user() + 長度上限（> 400 字）─────── 超過 → error                 ← 不打 LLM
 ├─ _is_noise(q)? ─────────────────────────── 是 → 死字串「看不太懂…」+ chip  ← 不打 LLM
 ├─ _GREETING_RE 命中（你好 / hi / 謝謝…）? ── 是 → 死字串自我介紹 + chip     ← 不打 LLM、不撈資料
 ├─ 沒有 ASSISTANT_HARNESS_ARN? ───────────── 是 → error / 服務範圍說明        ← 不打 LLM（正式環境不會發生）
 ├─ _effective_ctx(messages, ctx) ·········· 範圍延續：這句沒帶範圍就沿用「最近 3 句內」最近指定的（純邏輯）
 ├─ _gather_context(q, ctx) ················ 查 DB（alert_service）組資料包 JSON + 按鈕 + panel（清單/表格）  ← 不打 LLM，只撈數字
 ├─ _build_agent_prompt(q, ctx, data, panel)  把「資料包 + 問句 + 十來條規則」拼成一段文字
 ├─ _invoke_harness(prompt, sid) ─────────────────────────────────────────  ★ 這裡才打 Bedrock / Nova
 │      └─ Nova 讀資料包、聽懂問題、挑重點、寫出人話回答（＋清單/比較題只寫 1～2 句總結）
 ├─ _strip_source_line() ··················· 切掉 Nova 自己加的「來源：<檔名>」
 ├─ _unverified_numbers(answer, data) ······ 答案裡 ≥2 位數的數字若不在資料包 → 丟掉整段 Nova 答案
 │      └─ 改用 _templated_answer(data)                                      ← 退回死模板（應該很少）
 ├─ _tail(): {type:list|table}（清單/比較表）+ {type:actions}（按鈕）+ {type:suggestions}（追問 chip）  ← 永遠是程式組的
 └─ except（Bedrock throttle / 5xx / 憑證錯）→ _templated_answer() 或 error   ← 降級才用死模板
```

預設 `_STREAM_LIVE=False`（收齊再送）：整段生成完才驗證數字，出糗數字不會先流到畫面。
設 `ASSISTANT_STREAM=1` 可切回逐字串流（此時數字問題只記 log、不攔）。

| 使用者看到的東西 | 誰產生 |
|---|---|
| 答案那段話（判斷、建議、口語理解） | **Nova**。程式只給資料包 + 規則 |
| 資料包 / 清單裡的數字（空站 N、補 X 台） | 程式查 DB。**故意不讓 LLM 回想**，才能跟主控台一致 |
| 「開啟站點」「切到 X 區地圖」按鈕 | 程式，永不經 LLM |
| 清單 / 比較表格（`{type:list}` / `{type:table}`） | 程式從資料包組，數字不經 LLM |
| 下方追問 chip | 程式（`_followups`），永不經 LLM |
| 亂打的罐頭回覆、打招呼的自我介紹、Bedrock 掛掉的摘要 | 程式死字串 / `_templated_answer`（降級路徑） |

### 資料包怎麼給（`_gather_context` → `_build_agent_prompt`）

proxy 不給 LLM 工具權限，而是**把查好的資料寫成文字，當使用者訊息的一部分丟過去**：

1. 看 `context.town_code` / `station_uid`（前端本來就送）→ 決定要撈全市還是某區/某站。
2. **帶 `town_code` 的 scoped 查詢**（`alert_service.alerts(town_code=…)`）—— 完整、有該區自己的補取台數，
   絕不會被「全市 limit 1000」在尖峰時截斷。
3. 縮成一小包 JSON：`{"行政區":{空站,滿站,高/中風險,供需健康站數,站數合計,建議補車總量,建議取車總量,無預測站數,最優先處理:"站名",待處理站:[…依優先序]}, "全市對照":{數量}}`。
   - `最優先處理` 是明確欄位 —— prompt 要求 LLM 直接用，不要自己從清單挑（挑錯過）。
   - `供需健康站數` = 無風險的站（被問「安全 / 正常」時用這個，不是滿站）；`站數合計` 跟 KPI 的「模型覆蓋 N 站」對得上。
   - 問句含「空站 / 滿站」時，另塞 `空站站點` / `滿站站點` 站名清單（≤12～15），讓 LLM 能「列出來」而不是只回數量。
   - **清單題**（哪些站要補車 / 取車、有哪些空站 / 滿站、待處理站）→ 後端另發一個 `{"type":"list", title, items:[{name, meta, uid}]}`
     事件，由前端渲染成可點列表（站名 + 台數 badge，數字不經 LLM）；此時 LLM 只寫 1～2 句總結、不逐站條列，
     逐站「開啟」按鈕也省掉（清單列本身可點）。狀態題 / 知識題 / 問數量（`_LIST_SKIP_RE`）不觸發清單。
   - `全市對照` 只放數量，**不放台數** —— 避免全市總量被誤植進行政區的句子（3877 bug）。
   - 多區時改成 `{"行政區比較":[{name,空站,滿站,高/中風險,供需健康站數,建議補取總量,最優先處理}…]}`（不含待處理站清單，省 token）；
     並另發 `{"type":"table", title, columns:[{key,label,align}], rows:[{name, town, cells:[…]}]}` 事件，前端渲染成比較表格
     （區名可點 → 切到該區），LLM 只寫 1～2 句總結（哪區最吃緊 / 最輕）。面板寬度為此加寬到 440px。
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

```
AWS_REGION=ap-northeast-1
AWS_ACCESS_KEY_ID=<步驟三的>
AWS_SECRET_ACCESS_KEY=<步驟三的>
ASSISTANT_HARNESS_ARN=arn:aws:bedrock-agentcore:ap-northeast-1:597671487936:harness/imsoft_ubike_agentcore_harness-ZBEN5wmyW5
```

KEY / SECRET 填步驟三的。`.env` 已被 `.gitignore` 擋，不會進版控。

---

## 五、重啟後端

```bash
cd backend
pkill -f "uvicorn app.main:app"
DEMO_SPEED=60 uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

- `DEMO_SPEED` 要跟 tick 迴圈同值（回放 demo 時；沒跑回放就拿掉）。
- 改 `.py` 有 `--reload` 會自動吃；**改 `.env`（憑證）一定要 pkill 全重啟**。
- 想看「到底送什麼給 Harness」：起服務時加 `ASSISTANT_DEBUG_DUMP=1`，每題會寫一個
  `assistant_debug/<時間>_<問題>.txt`（prompt 全文 + 資料包 pretty + panel + Nova 原始回覆 + 數字驗證 + 最終輸出）。

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
| 板橋哪些站要優先補車 / 有哪些滿站 | 一句總結 + `{type:list}` 可點清單（站名 + 台數 badge）；不逐站條列 |
| 比較板橋、三重、中和 | 一句總結（哪區最吃緊 / 最輕）+ `{type:table}` 比較表（區名可點切區）；面板寬 440px |
| 哪些站是安全的 | 用「供需健康站數」回答（無風險的站），說明不逐站列 —— 不是把滿站當安全 |
| 空站門檻怎麼定的 / 台數為什麼不是補到剛好 | KB 回答，純文字、≤5 句，**不附「來源：」**（KB 檔名是機器字串，已一律隱藏） |
| 板僑（打錯字）/ 土城關誰（不成句） | 用「正在看」脈絡推測；只丟地名時開頭補一行「（理解為：X 目前調度狀況）」 |
| 你好 / hi / 謝謝 | 一句自我介紹（`_GREETING_RE`），不撈資料、不打 Harness |
| 123 / ?!? / asdfgh | 「看不太懂你的問題，可以再說清楚一點嗎？」+ 相關問題按鈕 |
| 今天天氣如何 | 不編造調度數字（Harness 依 system prompt 婉拒） |
| 貼 400 字以上 | 「問題太長了，請精簡在 400 字以內」 |
| 板橋站整體狀況（篩在別區時）| 回**板橋**——「整體 / 全部」讓步給問句點名的區（三層城市關鍵字，見已知限制）|

驗收重點：**行政區的數字要跟主控台的 KPI 條、主動警示面板對得上**（同一個時刻）。
卡住看後端 log（`logging.getLogger("assistant")`）。

### 上線前硬化（已做）

| 項目 | 做法 | 位置 |
|---|---|---|
| 數字事後驗證 | 答案裡 ≥2 位數的數字若不在資料包 JSON 裡 → 退回 `_templated_answer` 純模板摘要 | `_unverified_numbers` |
| Bedrock 掛掉的退化路徑 | `except` 內不再直接報錯：有資料包就用模板組一句「…（調度助理暫時無法回應，以上為系統即時摘要）」 | `chat_events` |
| 收齊再送 | 預設整段生成完才驗證再送出（避免出糗數字先流到畫面）；`ASSISTANT_STREAM=1` 可切回逐字 | `_STREAM_LIVE` |
| session id | 前端每個對話 mint UUID（`context.thread_id`）當 `runtimeSessionId`；不再用「第一句話雜湊」（會撞） | `_session_id` |
| 打招呼過濾 | 你好 / hi / 謝謝… → 一句自我介紹，不撈資料、不打 Harness | `_GREETING_RE` |
| 結構化清單 / 比較表 | 清單題 / 多區比較 → `{type:list}` / `{type:table}` 事件，數字由程式從資料組，LLM 只寫總結 | `_build_list` / `_compare_table` |
| 切區按鈕 | 問的區 ≠ 畫面正在看的區 → 給一顆 `filter_town` 按鈕，不自動切 | `_gather_context` |
| 三層城市關鍵字 | strong（各區/其他行政區/全市）一律全市；whole（新北市）沒點名區才全市；weak（整體/全部/所有）沒點名區也沒在篩區才全市 | `_CITY_*_RE` |
| 除錯 dump | `ASSISTANT_DEBUG_DUMP=1` → 每題寫一個檔（prompt + 資料包 + Nova 原始回覆 + 驗證 + 最終輸出） | `_dump_debug` |
| 回歸測試 | `eval/` 黃金題庫 27 題，改 prompt / 換模型後 `.venv/bin/python eval/run.py` | `backend/eval/` |

### 已知限制

- 每題 2–5 秒（Bedrock 往返）；預設「收齊再送」，使用者要等整段。
- `_is_noise` 擋純數字 / 標點 / 短英文亂碼；中文亂打（「哈哈哈」）仍會進 Harness。
- 意圖路由仍是 regex + 實體比對（非 LLM 分類）——決賽後再換。
- 模型是 Nova 2 Lite（Harness console 設定），偶爾規則遵循不穩；要更穩換 Nova Pro / Claude Haiku。
- 多輪脈絡靠 `runtimeSessionId`（Harness 端 Memory），proxy 只送最新一句、每輪重塞資料包。
- 範圍延續：當前問句沒帶範圍（區名 / 全市 / 這站）時，`_effective_ctx` 會沿用**最近 3 句內**
  最近一次明確指定的範圍（「先問全市、再問哪些站要補車」延續全市）；超過 3 句就當作換話題，
  掉回畫面篩選的區。純打招呼（`_GREETING_RE`）直接回自我介紹，不套範圍、不撈資料。
- 城市關鍵字分三層強度（見上表）：「板橋整體狀況」的「整體」會讓步給「板橋」→ 回板橋，不是全市。
- 之後若想改成「LLM 自己驅動撈資料（tool calling）」，設計與取捨見 `../調度助理-v2-架構參考.md`（目前決定不動）。

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
