from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.controller import (assistant_controller, health_controller,
                            predict_controller, station_controller)
from app.errors import AppError

app = FastAPI(title="YouBike 缺車預測後端",
              version="0.1.0",
              description="DeepAR H=6（3 小時）水位預測。規格見 meet/20260828/計劃-後端服務.md")

# demo 用：單站檢視頁以 file:// 直開，瀏覽器 origin 是 null，不開 CORS 打不進來。
# 全開只讀不寫（本服務沒有帶憑證的變更操作），比賽 demo 範圍可接受。
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(station_controller.router, prefix="/api/v1")
app.include_router(predict_controller.router, prefix="/api/v1")
app.include_router(assistant_controller.router, prefix="/api/v1")
app.include_router(health_controller.router, prefix="/api/v1")


@app.exception_handler(AppError)
def app_error(_: Request, e: AppError):
    return JSONResponse(status_code=e.http,
                        content={"error": {"code": e.code, "message": e.message}})
