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
import time

import boto3

REGION   = "ap-northeast-1"
# ★ demo2604：訓練集只到 2026-03-31，demo 回放 2026-05 時模型沒見過答案。
#   舊的 youbike-deepar-h6 Model/Config 物件仍在（不計費），名稱要錯開。
JOB      = "youbike-deepar-demo2604-20260831-084604"
IMAGE    = "633353088612.dkr.ecr.ap-northeast-1.amazonaws.com/forecasting-deepar:1"  # sm_train.py，六個 job 都用它跑完
MODEL    = "youbike-deepar-demo2604"
CONFIG   = "youbike-deepar-demo2604-config"
ENDPOINT = "youbike-deepar-demo2604"
INSTANCE = "ml.m5.large"   # 1,550 條序列 × 100 samples 用不到 c5.2xlarge；不夠再 update_endpoint

sm  = boto3.client("sagemaker", region_name=REGION)
job = sm.describe_training_job(TrainingJobName=JOB)
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
