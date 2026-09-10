# 調度助理 —— 黃金題庫回歸測試

改 `assistant_service.py` 的 prompt、換 Harness 模型、動 `_gather_context` 之後跑一次，
擋掉「改 A 壞 B」的回歸。**這不是單元測試，是對真實 Bedrock 回覆做斷言**，所以要能連上 AWS。

## 前置

1. `backend/.env` 已設 `ASSISTANT_HARNESS_ARN` + AWS 憑證（見 `../調度助理-AgentCore串接.md` §3〜§4）
2. demo 資料已載入 DB，且虛擬時鐘落在有資料的區間
   （照平常起服務即可：`DEMO_SPEED=60 uv run uvicorn app.main:app`，讓它先跑一下）
3. `python-dotenv`（通常已隨專案安裝；沒有的話 `uv pip install python-dotenv`）

沒有憑證也能跑，只是每題會是 `非預期 error`——可先用來確認題庫格式沒寫錯。

## 用法

```bash
cd backend
.venv/bin/python eval/run.py                       # 全部
.venv/bin/python eval/run.py --only district-compare,noise-digits
```

離開碼 0 = 全過，1 = 有失敗。可掛進 CI 或 pre-push。

## 斷言的三類事

| 類別 | 怎麼查 |
|---|---|
| ① 事實 | `expect.numbers_in_data`：答案裡 ≥ 2 位數的數字，逐一比對 `_gather_context` 撈出的資料包 JSON，找不到就是幻覺 / 算錯 |
| ② 格式 | `expect.not_contains`（`**`、`##`、`來源`）＋ `expect.max_sentences` 句數上限 |
| ③ 行為 | `expect.is_error`（亂打 / 過長要擋）、`expect.contains`（該提到的關鍵字） |

## 加題

`golden.jsonl` 一行一題：

```json
{"id": "唯一名稱",
 "messages": [{"role": "user", "content": "問句"}],
 "context": {"town_code": null, "station_uid": null},
 "expect": {"contains": ["板橋"], "not_contains": ["來源", "**"],
            "max_sentences": 5, "numbers_in_data": true, "is_error": false}}
```

`expect` 每個欄位都可省略。數字會隨虛擬時鐘變動，所以 `contains` 只放**結構性關鍵字**
（區名、「補」、「高風險」…），數值正確性交給 `numbers_in_data`。

## 已知限制

- 依賴虛擬時鐘當下的資料，換時間點跑結果會不同——比對的是「答案 vs 當下資料包」的一致性，不是固定答案。
- 每跑一次全題庫 = 十幾次 Harness 呼叫，有成本，別放進每次 commit 的 hook。
