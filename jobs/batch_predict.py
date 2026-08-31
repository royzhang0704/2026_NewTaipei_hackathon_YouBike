# ════════════════════════════════════════════════════════════
# jobs/batch_predict.py —— Job B：批次預測，寫 forecast_history
#
# 對應：計劃-TDX排程與初始化.md §3「Job B」。由 Job A 成功後同程序呼叫，
# 也可以自己跑（補打某個 origin）。
#
# ★ 多站併一個 invoke：DeepAR 的 instances 是陣列，50 站/批 ≈ 32 次 invoke，
#   不是 1,600 次單發（endpoint_repo.invoke_batch）。
#
# ★ 只打「本輪有新鮮資料」的站（anchor == slot）——這是設計決定，理由：
#   level30 沒有本輪列的站（Job A 判定過期的那些），history_repo.tail()
#   會退回更早的 anchor，模型吐的 6 格有一部分落在過去。
#   把它寫成 origin=本輪 的預測是在說謊。這些站的即時預測仍可走 /predict
#   （單站路徑會誠實地用該站自己的 anchor）。跳過數記在 detail。
#
# ★ 單站錯誤不中斷整批：INSUFFICIENT_HISTORY / STATION_UNKNOWN 等
#   逐站記數跳過（計劃 §3 Job B）。一站壞掉不該讓 1,599 站沒有預測。
#
# 用法：
#   ENDPOINT_MOCK=1 uv run python -m jobs.batch_predict          # 全量鏈路
#   uv run python -m jobs.batch_predict --limit 50               # 真 endpoint 只驗 1 批
#   uv run python -m jobs.batch_predict --slot '2026-08-28 15:30'
#   uv run python -m jobs.batch_predict --dry-run                # 只組 payload 不打
#
# ⚠ 真 endpoint 計費中（ml.m5.large）——不用時照 計劃-AWS_Endpoint.md §5 三連刪
# ════════════════════════════════════════════════════════════
import argparse
import sys
import time
from datetime import datetime, timedelta

from app import config
from app.errors import AppError
from app.repository import endpoint_repo, job_run_repo, sys_config_repo
from app.repository.db import get_conn
from app.service.predict_service import build_payload

JOB_NAME = "batch_predict"


def eligible_stations() -> list[dict]:
    """對象：cat 非 NULL，或有指向「有 cat 站」的 proxy（計劃 §3 Job B 步驟 1）。

    ★ 條件與 build_payload() 第 1 步一致 —— 這裡先篩掉，是為了不要對
      注定丟 STATION_UNKNOWN 的站白跑一輪查詢。真正的把關仍在 build_payload。
    """
    with get_conn().cursor() as cur:
        cur.execute("""
            SELECT s.station_uid, s.capacity
              FROM hackathon_backend_station s
         LEFT JOIN hackathon_backend_station p ON p.station_uid = s.proxy_station_uid
             WHERE s.cat IS NOT NULL OR p.cat IS NOT NULL
             ORDER BY s.station_uid
        """)
        return cur.fetchall()


def clip_factory(cap: int | None):
    """後處理的 clip —— 規則與 predict_service.predict_one 第 5 步逐字相同
    （負二項支撐 0..∞，小站會預測出超過車樁的台數；capacity 缺值只夾下界）。

    ⚠ 這是刻意的重複：硬性約束禁止改動 predict_one，所以不能把它抽成
      共用函式再讓兩邊呼叫。改其中一邊時，另一邊必須跟著改。
      出處：app/service/predict_service.py 的 clip()。
    """
    def clip(v: float) -> float:
        v = max(float(v), 0.0)
        return round(min(v, cap) if cap is not None else v, 2)
    return clip


def collect(stations: list[dict], slot: datetime) -> tuple[list, dict]:
    """逐站組 payload。回傳 (items, stats)。

    items 每筆 = {"uid", "cap", "instance"}；順序即送出順序，
    invoke_batch 靠位置對回站號。
    """
    items, stats = [], {}
    ctx_filled = []

    def bump(k):
        stats[k] = stats.get(k, 0) + 1

    for st in stations:
        try:
            b = build_payload(st["station_uid"], at=slot)
        except AppError as e:
            bump(e.code)                     # INSUFFICIENT_HISTORY / STATION_UNKNOWN…
            continue
        except Exception as e:               # 沒預期到的錯也不能中斷整批
            bump(f"UNEXPECTED:{type(e).__name__}")
            continue

        if b["history"]["anchor"] != slot:
            bump("STALE_ANCHOR")             # 本輪沒有新鮮資料的站，見檔頭
            continue

        inst = b["payload"]["instances"][0]
        ctx_filled.append(sum(v is not None for v in inst["target"]))
        # 週期補值的量要看得到 —— context 看起來滿了，但有一部分不是實測
        stats["imputed_slots"] = stats.get("imputed_slots", 0) + b["imputed_slots"]
        stats["still_missing"] = stats.get("still_missing", 0) + b["missing_slots"]
        items.append({"uid": st["station_uid"], "cap": st["capacity"],
                      "instance": inst})

    if ctx_filled:
        ctx_filled.sort()
        stats["context_min"] = ctx_filled[0]
        stats["context_median"] = ctx_filled[len(ctx_filled) // 2]
    return items, stats


def predict(items: list, batch_size: int) -> tuple[list, dict]:
    """分批 invoke。回傳 (rows, stats)；rows 是 forecast_history 的列。"""
    step = timedelta(minutes=config.FREQ_MIN)
    rows, stats = [], {"batches": 0, "invoke_sec": 0.0, "mock": None}

    for i in range(0, len(items), batch_size):
        chunk = items[i:i + batch_size]
        payload = {
            "instances": [c["instance"] for c in chunk],
            "configuration": {"num_samples": config.NUM_SAMPLES,
                              "output_types": ["quantiles"],
                              "quantiles": [config.Q_LO, config.Q_MID, config.Q_HI]},
        }
        t0 = time.time()
        preds = endpoint_repo.invoke_batch(payload)
        stats["invoke_sec"] += time.time() - t0
        stats["batches"] += 1

        for c, p in zip(chunk, preds):
            stats["mock"] = p["mock"]
            clip = clip_factory(c["cap"])
            # anchor 已在 collect() 確認 == slot，所以 at = slot + 30m×(k+1)
            anchor = datetime.strptime(c["instance"]["start"], "%Y-%m-%d %H:%M:%S") \
                     + step * (len(c["instance"]["target"]) - 1)
            for k in range(config.H):
                rows.append((c["uid"], anchor, anchor + step * (k + 1),
                             clip(p["q19"][k]), clip(p["q50"][k]), clip(p["q90"][k])))
    stats["invoke_sec"] = round(stats["invoke_sec"], 2)
    return rows, stats


def upsert(rows: list, model_job: str) -> int:
    """同 origin 重跑覆蓋（PK station_uid, origin, at）。"""
    with get_conn().transaction(), get_conn().cursor() as cur:
        cur.executemany(
            """
            INSERT INTO hackathon_backend_forecast_history
                   (station_uid, origin, at, q19, q50, q90, model_job)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (station_uid, origin, at) DO UPDATE SET
                   q19 = EXCLUDED.q19, q50 = EXCLUDED.q50, q90 = EXCLUDED.q90,
                   model_job = EXCLUDED.model_job, created_at = now()
            """, [r + (model_job,) for r in rows])
        return cur.rowcount


def run(slot: datetime, batch_size: int = config.PREDICT_BATCH_SIZE,
        limit: int | None = None, dry_run: bool = False) -> str:
    """跑一輪批打。Job A 觸發時呼叫這支；回傳一句話摘要。"""
    run_id = None if dry_run else job_run_repo.start(JOB_NAME, slot)
    try:
        stations = eligible_stations()
        if limit:
            stations = stations[:limit]
        items, s1 = collect(stations, slot)
        print(f"   對象 {len(stations)} 站 → 可打 {len(items)}"
              f"（跳過 {len(stations) - len(items)}：{s1}）")

        if not items:
            msg = "沒有任何站可打（全部被跳過）"
            if run_id:
                job_run_repo.finish(run_id, "skipped", stations_ok=0,
                                    error=msg, detail=s1)
            return msg

        if dry_run:
            inst = items[0]["instance"]
            return (f"--dry-run：{len(items)} 站、"
                    f"{-(-len(items) // batch_size)} 批；首站 {items[0]['uid']} "
                    f"start={inst['start']} target={len(inst['target'])} 格 "
                    f"dynamic_feat={len(inst['dynamic_feat'][0])} 格（未打 endpoint）")

        rows, s2 = predict(items, batch_size)
        # ★ mock 的列要能從 model_job 認出來，別混進驗收統計
        model_job = ("MOCK:" if s2["mock"] else "") + config.MODEL_INFO["job"]
        n = upsert(rows, model_job)

        detail = {**s1, **s2, "stations": len(items), "rows": n}
        if sys_config_repo.is_virtual():
            detail["virtual_now"] = str(sys_config_repo.effective_now())
        # ★ 預測終點 = origin + H×30 分。前端拿它顯示「預測涵蓋到幾點」，
        #   維運拿它跟 now 比就知道預測有沒有斷。mock 的不寫（那不是真預測）。
        if not s2["mock"]:
            sys_config_repo.set(sys_config_repo.K_FORECAST_END,
                                slot + timedelta(minutes=config.FREQ_MIN * config.H))
        if run_id:
            job_run_repo.finish(run_id, "success", stations_ok=len(items),
                                rows_written=n, detail=detail)
        return (f"{len(items)} 站 × {config.H} 格 = {n} 列，"
                f"{s2['batches']} 批 / {s2['invoke_sec']}s"
                f"{'（MOCK）' if s2['mock'] else ''}")

    except Exception as e:
        if run_id:
            job_run_repo.finish(run_id, "failed", error=f"{type(e).__name__}: {e}")
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description="Job B：批次預測寫 forecast_history")
    ap.add_argument("--slot", default=None,
                    help="origin（ISO；預設取 level30 最新 slot）")
    ap.add_argument("--limit", type=int, default=None,
                    help="只打前 N 站（真 endpoint 驗 1 批用 --limit 50）")
    ap.add_argument("--batch", type=int, default=config.PREDICT_BATCH_SIZE)
    ap.add_argument("--dry-run", action="store_true", help="只組 payload 不打 endpoint")
    a = ap.parse_args()

    if a.slot:
        slot = datetime.fromisoformat(a.slot)
    else:
        with get_conn().cursor() as cur:
            cur.execute("SELECT max(slot) AS s FROM hackathon_backend_level30")
            slot = cur.fetchone()["s"]
    print(f"── origin {slot}｜mock={config.MOCK}｜batch={a.batch}")

    try:
        print(f"   {run(slot, a.batch, a.limit, a.dry_run)}")
    except Exception as e:
        print(f"✗ {type(e).__name__}: {e}", file=sys.stderr)
        raise

    if not a.dry_run:
        with get_conn().cursor() as cur:
            cur.execute("""
                SELECT count(*) AS 列數, count(DISTINCT station_uid) AS 站數,
                       count(*) FILTER (WHERE NOT (q19 <= q50 AND q50 <= q90)) AS 分位數交叉,
                       min(at) AS 最早, max(at) AS 最晚
                  FROM hackathon_backend_forecast_history WHERE origin = %s""", (slot,))
            r = cur.fetchone()
        print(f"✓ origin={slot}：{r['站數']} 站 / {r['列數']} 列"
              f"（每站 {r['列數'] // max(r['站數'], 1)} 格）｜"
              f"q19≤q50≤q90 交叉 {r['分位數交叉']} 列｜{r['最早']} ~ {r['最晚']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
