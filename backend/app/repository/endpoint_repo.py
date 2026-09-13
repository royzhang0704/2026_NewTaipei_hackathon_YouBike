# ════════════════════════════════════════════════════════════
# endpoint_repo —— 唯一碰 AWS 的地方。
# ENDPOINT_MOCK=1 時回固定分位數：只驗鏈路不驗預測，回應帶 mock=True。
# ════════════════════════════════════════════════════════════
import json

from app import config
from app.errors import AppError


def invoke(payload: dict) -> dict:
    """回傳 {"q19": [H], "q50": [H], "q90": [H], "mock": bool}"""
    if config.MOCK:
        target = payload["instances"][0]["target"]
        last = next((v for v in reversed(target) if v is not None), 5)
        return {"q19": [max(0, last - 2)] * config.H,
                "q50": [float(last)] * config.H,
                "q90": [last + 2] * config.H,
                "mock": True}

    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        rt = boto3.client("sagemaker-runtime", region_name=config.REGION)
        res = rt.invoke_endpoint(EndpointName=config.ENDPOINT_NAME,
                                 ContentType="application/json",
                                 Body=json.dumps(payload))
        body = json.loads(res["Body"].read())
        q = body["predictions"][0]["quantiles"]
        return {"q19": q[config.Q_LO], "q50": q[config.Q_MID],
                "q90": q[config.Q_HI], "mock": False}
    except (BotoCoreError, ClientError) as e:
        raise AppError("ENDPOINT_UNAVAILABLE", 503,
                       f"endpoint {config.ENDPOINT_NAME} 無法使用：{e}") from e


def invoke_batch(payload: dict) -> list[dict]:
    """多站一次打。回傳與 instances 等長的 list，每筆
    {"q19": [H], "q50": [H], "q90": [H], "mock": bool}。

    ★ 為什麼另開一支而不改 invoke()：invoke() 只讀 instances[0] /
      predictions[0]，是 /predict 單站路徑的契約，動它等於動線上服務。
      這支只給 Job B 批打用（計劃 §3 Job B 步驟 2：50 站/批 ≈ 32 次 invoke，
      不是 1,600 次單發）。

    ⚠ 回傳順序 = instances 的順序。SageMaker 的 predictions 與 instances
      一一對應，呼叫端靠位置對回站號 —— 不要在中間過濾或排序。
    """
    insts = payload["instances"]
    if config.MOCK:
        out = []
        for inst in insts:
            last = next((v for v in reversed(inst["target"]) if v is not None), 5)
            out.append({"q19": [max(0, last - 2)] * config.H,
                        "q50": [float(last)] * config.H,
                        "q90": [last + 2] * config.H,
                        "mock": True})
        return out

    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        rt = boto3.client("sagemaker-runtime", region_name=config.REGION)
        res = rt.invoke_endpoint(EndpointName=config.ENDPOINT_NAME,
                                 ContentType="application/json",
                                 Body=json.dumps(payload))
        preds = json.loads(res["Body"].read())["predictions"]
    except (BotoCoreError, ClientError) as e:
        raise AppError("ENDPOINT_UNAVAILABLE", 503,
                       f"endpoint {config.ENDPOINT_NAME} 無法使用：{e}") from e

    if len(preds) != len(insts):
        # 對不齊就整批作廢 —— 硬對回去會把 A 站的預測寫到 B 站名下
        raise AppError("ENDPOINT_BAD_RESPONSE", 502,
                       f"回 {len(preds)} 筆預測但送了 {len(insts)} 站")
    return [{"q19": p["quantiles"][config.Q_LO],
             "q50": p["quantiles"][config.Q_MID],
             "q90": p["quantiles"][config.Q_HI],
             "mock": False} for p in preds]
