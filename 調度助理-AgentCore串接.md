# 調度助理 × AgentCore 串接

## 一、這是什麼

調度助理 widget 呼叫後端 `POST /api/v1/assistant/chat`（SSE）。後端在 `app/service/assistant/` package：

| 模組 | 職責 |
|---|---|
| `common.py` | 文字對照、所有意圖比對用正則、罐頭訊息、prompt 規則清單、串流參數 |
| `scope.py` | `is_noise` / 打招呼 / 別的縣市 前置；範圍解析（某站 / 某區 / 全市）+ 對話範圍延續 `effective_ctx` |
| `datapkg.py` | `resolve_scope(q,ctx) -> Scope`（純函式、可直接斷言）→ `gather_context` 依 `Scope.kind`（compare/district/city）dispatch 到 `_pkg_*` 分支，組資料包 JSON + 按鈕 + `{type:list}`/`{type:table}` panel |
| `llm.py` | `build_agent_prompt`、`invoke_harness`、數字事後驗證、純模板降級、除錯 dump |
| `events.py` | `chat_events` —— 只做編排 |


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
「檢查它吐什麼」，不自己寫答案。`events.chat_events()` 流程（函式名已對應拆包後的 package，
`scope.*` / `datapkg.*` / `llm.*` 前綴是模組）：

```
chat_events(messages, ctx)
 ├─ scope.last_user() + 長度上限（> 400 字）── 超過 → error                  ← 不打 LLM
 ├─ scope.is_noise(q)? ────────────────────── 是 → 死字串「看不太懂…」+ chip  ← 不打 LLM
 ├─ GREETING_RE 命中（你好 / hi / 謝謝…）? ─── 是 → 死字串自我介紹 + chip     ← 不打 LLM、不撈資料
 ├─ OTHER_CITY_RE 命中且問句沒點到新北的區/站? 是 → 「不在新北市範圍」+ chip    ← 不打 LLM
 ├─ 沒有 ASSISTANT_HARNESS_ARN? ───────────── 是 → error / 服務範圍說明        ← 不打 LLM（正式環境不會發生）
 ├─ scope.effective_ctx(messages, ctx) ······ 範圍延續：這句沒帶範圍就沿用「最近 3 句內」最近指定的（純邏輯）
 ├─ datapkg.gather_context(q, ctx) ·········· 查 DB（alert_service）組資料包 JSON + 按鈕 + panel（清單/表格）  ← 不打 LLM，只撈數字
 │      └─ 內部先呼叫 resolve_scope(q, ctx) 決定 kind（compare / district / city），
 │         再 dispatch 到對應 `_pkg_*` 分支撈資料、`_attach_station`/`_attach_donors` 補站點與調度來源
 ├─ llm.build_agent_prompt(q, ctx, data, panel)  把「資料包 + 問句 + 十來條規則」拼成一段文字
 ├─ llm.invoke_harness(prompt, sid) ────────────────────────────────────  ★ 這裡才打 Bedrock / Nova
 │      └─ Nova 讀資料包、聽懂問題、挑重點、寫出人話回答（＋清單/比較題只寫 1～2 句總結）
 ├─ llm.strip_source_line() ················· 切掉 Nova 自己加的「來源：<檔名>」
 ├─ llm.unverified_numbers(answer, data) ···· 答案裡 ≥2 位數的數字若不在資料包 → 丟掉整段 Nova 答案
 │      └─ 改用 llm.templated_answer(data)                                    ← 退回死模板（應該很少）
 ├─ _tail(): {type:list|table}（清單/比較表）+ {type:actions}（按鈕）+ {type:suggestions}（追問 chip，`_followups`）  ← 永遠是程式組的
 └─ except（Bedrock throttle / 5xx / 憑證錯）→ llm.templated_answer() 或 error   ← 降級才用死模板
```

預設 `STREAM_LIVE=False`（收齊再送）：整段生成完才驗證數字，出糗數字不會先流到畫面。
設 `ASSISTANT_STREAM=1` 可切回逐字串流（此時數字問題只記 log、不攔）。

**各階段的實際判斷細節（不只是「有沒有打 LLM」，還有「怎麼判斷」）：**

- **`resolve_scope` 的範圍判斷是三層關鍵字強度**，不是單一 regex：
  - `strong`（各區 / 其他行政區 / 全市）：不管有沒有點名區、有沒有 `ctx.town_code`，一律當全市——
    這一層解決「其他行政區呢？」被誤判成某個舊區的 bug。
  - `whole`（新北市 / 全新北）：只要問句裡沒有同時點名一個區，就當全市——解決「新北市目前概況」
    被 `ctx.town_code` 劫走、答成篩選中的舊區的 bug。
  - `weak`（整體 / 全部 / 所有）：只有「沒點名任何區」且「`ctx.town_code` 也沒設」才當全市，
    否則視為在問目前正看的那一區——解決「板橋站整體狀況？」被 `weak` 關鍵字誤判成全市的 bug。
- **`_attach_station` 的 panel 抑制**：`_build_list()`（清單/表格 panel）只在「沒有解出單一站點」時才組，
  且要放在 `_attach_station` 之後才判斷——早期版本先組 panel 導致「捷運海山站要不要補車？」
  的「補車」二字誤命中清單題 regex，答非所問地跳出整個土城區的表格。
- **`find_station` 的模糊比對**：先找「完整站名」在問句裡的精確子字串命中（贏者全拿），
  只有完全沒有精確命中時才退而比對「問句片段」（如「1號出口」）在站名裡的命中，
  且用「命中片段長度」而非「站名長度」排序——否則「捷運七張站(1號出口)」會被
  「1號出口」這個泛用片段誤導到別的、名字更長的站。
- **`_attach_donors`（調度來源）的兩道防線**：
  1. 距離上限 `ASSISTANT_DONOR_MAX_KM`（預設 5 km，haversine 直線距離）——避免建議一個 12.7 km
     外的滿站當調出點；超過範圍就退化成「建議由調度中心備用車補入」。
  2. Coverage-walk：依距離近到遠累加候選站的 `可調出上限`，一旦累加量 ≥ 目標站的「建議補 N 台」
     就停止並截斷候選清單——保證「文字建議提到幾站」跟「下方調出按鈕給幾顆 chip」永遠一致，
     不會出現「文字寫兩站、按鈕給三顆」的落差。累加仍不足時另外標注「尚缺 N 台建議調度中心補入」。
- **`unverified_numbers` 的千分位逗號**：Nova 偶爾把「3877」寫成「3,877」，若直接用 `\d+` 抓數字
  會被逗號切成 "3" + "877" 兩段，"877" 不會逐字出現在 JSON 資料包裡（資料包裡是連續的 "3877"），
  因而被誤判成幻覺數字、觸發不必要的模板降級。修法是驗證前先用
  `re.sub(r"(?<=\d)[,，](?=\d)", "", answer)` 把千分位逗號拿掉再比對，同時 prompt 規則②
  也明講「不要加千分位逗號」從源頭減少發生。
- **「其他行政區呢？」的雙層修法**：範圍判斷本身（見上面 `strong` 那層）先保證撈到的是全市資料，
  但即使資料對了，Nova 2 Lite 仍會被問句裡「其他」兩個字卡住、回「其他區未提供」。
  `build_agent_prompt` 因此在偵測到「已判定看全市 + 問句含『其他/其餘/別的…』之類的擴大詞」時，
  把送給模型的**問句文字本身**正規化成「現在全市整體概況如何？」（如果去掉擴大詞後幾乎沒剩其他內容），
  從根本上避免模型看到觸發詞。同時把追問 chip 本身也從「其他行政區呢？」改成「看全市整體概況」，
  讓最常見的路徑一開始就不會撞到這個陷阱。
- **按鈕的組成順序**：`gather_context` 最後把按鈕分成「切換行政區」（最多 1 顆，問的區跟目前畫面不同才給）
  和「開啟站點」（`select_station`，依序去重，最多 `donor_cap` 顆——沒有調度來源時預設 3，
  有調度來源建議時會擴大成 `1 + len(donor_buttons)`，讓「本站」+ 全部調出候選站都能點開）；
  有清單/表格 panel 時則不再重複給站點 chip（清單本身已經可點）。
- **`RULES` 為什麼留在 user message 尾巴、不併進 `systemPrompt`**：Harness 的 `invoke_harness` API
  有獨立的 `systemPrompt` 參數，理論上該把「角色定位」跟「12 條回答規則」都搬過去、跟每次都不同的
  資料包+問句分開送，比較乾淨。實測發現：Nova 2 Lite 對「離生成點較遠」的指令遵循度會明顯下降——
  規則搬進 `systemPrompt` 後，規則①「不要用 Markdown」直接被無視，問「這個畫面怎麼閱讀？」這類問題
  會生出長篇 `**粗體**`／條列標題的說明，而不是原本 3～5 句樸素文字。A/B 測過三種組合鎖定原因：
  規則全進 systemPrompt → 長文；規則全留 user 尾巴 → 正常；只角色/KB 進 systemPrompt、規則留
  user 尾巴 → 正常。所以現在是：`common.SYSTEM_PROMPT`（走 `systemPrompt` 參數）只放角色定位/KB
  行為（`kb/_harness_system_prompt.txt`），`RULES` 12 條繼續由 `build_agent_prompt()` 接在
  user message 最後——這是有實測依據的取捨，不要因為想要「架構更乾淨」就把它們搬回去。

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
   - 問「從哪調車過來 / 調度來源」時（`_ASK_SOURCE_RE`）另塞 `可調出候選`，規則 ⑪：
     - 候選 = **風險模型說「該取車」的滿站**（`dispatch.action=="remove"`）；每站帶 `可調出上限`
       ＝該站自己的建議取車量（取到這個數它仍健康）。**刻意不叫「建議取 N 台」**——那是指令式字眼，
       Nova / 使用者會讀成「從這裡取 N 台」（曾發生：回「取 6 台、取 5 台」讓人以為 6+5 要湊）。
       規則 ⑪ 要求句子圍繞「湊滿本站的建議補 N 台」，`可調出上限` 只當「這站最多能給多少」。
     - 有目標站 → 近站（`ASSISTANT_DONOR_MAX_KM`，預設 5 km 內）依**直線距離**排序，**從最近的累加
       『可調出上限』、湊到本站『建議補 N 台』就停** —— 資料包的 `可調出候選` / 下方「調出點：X」按鈕 /
       Nova 的 prose 都是這同一組（規則 ⑪ 叫它「全部依序提到」，不多挑不少挑）。
     - 附近餘裕站合計還不夠 N → `可調出候選` 給有的、`可調出備註` 補「尚缺 M 台用調度中心備用車」。
     - 最近的滿站也超過距離上限（例：龜吼在萬里）→ 不列站、不做按鈕，`可調出備註` = 全用備車。
     - 有 `可調出備註`（附近沒餘裕站 / 本區沒滿站）→ 說「由調度中心的備用車 / 調度站預備車補入」，不列遠站
       （**系統沒有備車庫存 / 地點資料，不要編**）。
     - 一定要講本站『建議補 N 台』的台數。**絕不從空站 / 待補站調車**（Nova 曾把「最急空站」當成來源）。
     - 註：用這個 dashboard 的人**就是**調度中心，所以不寫「交給調度中心排定」這種話。
   - **清單題**（哪些站要補車 / 取車、有哪些空站 / 滿站、待處理站）→ 後端另發一個 `{"type":"list", title, items:[{name, meta, uid}]}`
     事件，由前端渲染成可點列表（站名 + 台數 badge，數字不經 LLM）；此時 LLM 只寫 1～2 句總結、不逐站條列，
     逐站「開啟」按鈕也省掉（清單列本身可點）。
     **不觸發清單**：狀態題 / 知識題 / 問數量（`_LIST_SKIP_RE`）；**問句指向某一站時**（`_find_station` 命中，
     例：「『捷運海山站』要不要補車」——「補車」二字會誤命中 `_LIST_REFILL_RE`，但這是單站問題不該整區列出）。
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
# 比賽規則：Bedrock 請求需 < 1 RPS。不設這行＝用預設值 1.05（節流開著，安全預設）。
# 正式環境 / 比賽結束後想拿掉人為限制 → 明確設成 0。
ASSISTANT_BEDROCK_MIN_INTERVAL=1.05
```

KEY / SECRET 填步驟三的。`.env` 已被 `.gitignore` 擋，不會進版控。

`ASSISTANT_BEDROCK_MIN_INTERVAL` 這行**不是 AWS 端要設定的東西**，純粹是後端自己的節流器（`llm._throttle()`）；
沒有對應的 AWS console 步驟，只有這個環境變數。部署到 AWS 上時，改成在部署設定（AgentCore Runtime /
ECS task definition 之類，看實際怎麼部署）裡設同名環境變數，語意一樣：不設或設正數＝節流開著，設 `0`＝關閉。

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
| 南港展覽館 / 內湖區 / 台北車站 | 「不在新北市 YouBike 範圍」（`_OTHER_CITY_RE`）——不硬套目前行政區的資料。「板橋南港路口」這種新北路名不誤判 |
| 123 / ?!? / asdfgh | 「看不太懂你的問題，可以再說清楚一點嗎？」+ 相關問題按鈕 |
| 今天天氣如何 | 不編造調度數字（Harness 依 system prompt 婉拒） |
| 貼 400 字以上 | 「問題太長了，請精簡在 400 字以內」 |
| 板橋站整體狀況（篩在別區時）| 回**板橋**——「整體 / 全部」讓步給問句點名的區（三層城市關鍵字，見已知限制）|
| 捷運七張站(1號出口)可以從那邊補車？| 回七張的現況 + 附近餘裕站。① `find_station` 靠「命中的最長片段」挑站，不是站名最長的（否則「1號出口」會選到名字最長的站）；② 打錯「那/哪」也算；③ 單站檢視開著、問句沒點別的區 → 就當在問開著的那站 |

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
| Bedrock 請求節流 | 同一 process 內，打 Bedrock 的請求間隔 ≥ `ASSISTANT_BEDROCK_MIN_INTERVAL` 秒（預設 1.05）——比賽規則需求，正式環境設 0 關閉 | `llm._throttle()` |

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
- Bedrock 請求節流只保證「同一個 process 內」的間隔 —— 不同 process／多 instance 同時打同一 AWS 帳號，
  各自都合規但加起來理論上仍可能超過 1 RPS；真要跨 process 硬保證需要分散式節流（Redis token bucket 之類），
  目前規模用「口頭約定不要同時多處打同一帳號」代替。

---

## 八、之後正式部署

前後端上 AWS 後：

- 後端 proxy 帶著 IAM role（或 `.env` 憑證）即可，`invoke_harness` 走網路呼叫，跟後端在哪區無關。
- 前端分網域部署時設 `VITE_ASSISTANT_URL=<後端網址>/api/v1/assistant/chat`。
- **把 `ASSISTANT_BEDROCK_MIN_INTERVAL` 設成 `0`**——比賽期間 1 RPS 的人為限制拿掉，改吃 Bedrock
  帳號本身的真實配額；流量大就買 Provisioned Throughput，不要再靠這個 sleep-based 節流器。
- 進階：把 `/alerts`、`/stations/{uid}/day` 用 Gateway 的 REST/OpenAPI target 掛成 Harness 的工具，即時問答也交給 Agent（需要後端有對外 URL）。目前即時那半在 proxy 內處理，不需要這步。

---

## 九、決賽後清理（避免持續計費）

依序刪：Harness → Gateway（先刪 target 再刪 gateway）→ Knowledge Base（含 data source）→ S3 bucket → 為這些自動建的 IAM service role + 步驟三的 IAM user。

先設 AWS Budgets 告警（$10 / $50 兩檔）。即時查詢不碰 Bedrock；只有知識問答用 Nova 2 Lite，每題約 $0.01。
