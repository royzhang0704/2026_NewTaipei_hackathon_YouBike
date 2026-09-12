#!/usr/bin/env python3
# ════════════════════════════════════════════════════════════
# deploy_endpoint.py —— model.tar.gz → youbike-deepar-demo2604 endpoint
# 規格：meet/20260828/計劃-AWS_Endpoint.md §2（三段：Model → Config → Endpoint）
#
# ★ RoleArn 與 ModelDataUrl 不手打，從 training job 描述取。
# ★ endpoint 按時計費，建了就一直算 —— 用完就刪（三個物件都要刪，
#   指令見計劃 §5）。Demo 前一天建、Demo 完就刪。
#
# 執行（使用者自己跑）：
#   cd backend && uv run python aws/deploy_endpoint.py
# ════════════════════════════════════════════════════════════
import os
import time

import boto3

# ★ 與 ml-deepar/sm_train.py 同一套環境變數：source env.hackathon.sh 就整組切到賽會帳號。
#   不設時走自有帳號 ap-northeast-1，行為與改版前相同。
REGION   = os.environ.get("SM_REGION", "ap-northeast-1")
# ★ demo2604：訓練集只到 2026-03-31，demo 回放 2026-05 時模型沒見過答案。
#   舊的 youbike-deepar-h6 Model/Config 物件仍在（不計費），名稱要錯開。
#
# ★ 以下四項可用環境變數覆寫，預設值就是 demo2604 那一組（行為不變）。
#   為什麼要能覆寫：換一顆模型評估時，同時存在的 Model / Config / Endpoint
#   名稱都必須錯開，手改四處容易漏改一處而把新權重掛到舊名字上 ——
#   那正是 think-report/training/model-card.md §5-1① 那種「安靜地錯」。
#   用法見 meet/20260910/AWS指令-訓練與端點.md §4。
JOB      = os.environ.get("SM_JOB",      "youbike-deepar-demo2604-20260831-084604")
MODEL    = os.environ.get("SM_MODEL",    "youbike-deepar-demo2604")
CONFIG   = os.environ.get("SM_CONFIG",   MODEL + "-config")
ENDPOINT = os.environ.get("SM_ENDPOINT", MODEL)
# ★ DeepAR image 的 ECR 帳號每區不同 —— 只改 REGION 不改 IMAGE 會 ValidationException，
#   而錯誤訊息只說找不到 image、不指向區域。所以由 REGION 推導，不讓兩者脫鉤。
#   （與 ml-deepar/sm_train.py 的 DEEPAR_ACCOUNTS 同一份表，兩邊要一起維護）
DEEPAR_ACCOUNTS = {
    "ap-northeast-1": "633353088612",
    "us-west-2":      "156387875391",
    "us-east-1":      "522234722520",
}
if os.environ.get("SM_IMAGE"):
    IMAGE = os.environ["SM_IMAGE"]
elif REGION in DEEPAR_ACCOUNTS:
    IMAGE = f"{DEEPAR_ACCOUNTS[REGION]}.dkr.ecr.{REGION}.amazonaws.com/forecasting-deepar:1"
else:
    raise SystemExit(f"✗ 不認得 {REGION} 的 DeepAR image 帳號。"
                     f"已知：{', '.join(DEEPAR_ACCOUNTS)}。"
                     f"其他區請用 SM_IMAGE 直接給完整 URI。")
INSTANCE = os.environ.get("SM_INSTANCE", "ml.m5.large")   # 1,550 條序列 × 100 samples 用不到 c5.2xlarge；不夠再 update_endpoint

sm  = boto3.client("sagemaker", region_name=REGION)
job = sm.describe_training_job(TrainingJobName=JOB)
print(f"region         {REGION}")
print(f"training job   {JOB}")
print(f"model data     {job['ModelArtifacts']['S3ModelArtifacts']}")
print(f"role           {job['RoleArn']}")

# ── 1  Model ─────────────────────────────────────────────
print(f"\n[1/3] create_model {MODEL}")
sm.create_model(
    ModelName=MODEL,
    PrimaryContainer={"Image": IMAGE,
                      "ModelDataUrl": job["ModelArtifacts"]["S3ModelArtifacts"]},
    ExecutionRoleArn=job["RoleArn"])

# ── 2  EndpointConfig ────────────────────────────────────
print(f"[2/3] create_endpoint_config {CONFIG}  ({INSTANCE} × 1)")
sm.create_endpoint_config(
    EndpointConfigName=CONFIG,
    ProductionVariants=[{"VariantName": "AllTraffic",
                         "ModelName": MODEL,
                         "InstanceType": INSTANCE,
                         "InitialInstanceCount": 1,
                         "InitialVariantWeight": 1.0}])

# ── 3  Endpoint ──────────────────────────────────────────
print(f"[3/3] create_endpoint {ENDPOINT}  （等待 InService，約 5~8 分鐘）")
t0 = time.time()
sm.create_endpoint(EndpointName=ENDPOINT, EndpointConfigName=CONFIG)
sm.get_waiter("endpoint_in_service").wait(
    EndpointName=ENDPOINT, WaiterConfig={"Delay": 30, "MaxAttempts": 40})
st = sm.describe_endpoint(EndpointName=ENDPOINT)["EndpointStatus"]
print(f"\n{ENDPOINT}  {st}  ({time.time()-t0:.0f}s)")
print("⚠ 現在開始按時計費。用完就刪（計劃-AWS_Endpoint.md §5 三連刪）。")
