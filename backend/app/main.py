from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.controller import (assistant_controller, dispatch_controller,
                            health_controller, predict_controller,
                            station_controller)
from app.errors import AppError

app = FastAPI(title="YouBike 缺車預測後端",
              version="0.1.0",
              description="DeepAR H=6（3 小時）水位預測。規格見 meet/20260828/計劃-後端服務.md")

# demo 用：單站檢視頁以 file:// 直開，瀏覽器 origin 是 null，不開 CORS 打不進來。
# ★ 2026-09-12 訂正：本服務**已經有變更操作** —— POST/DELETE /dispatch/orders
#   是第一個寫入端點，原本「全開只讀不寫」的理由不再成立。現況是任何網頁都
#   能對它寫調度單。
#   維持全開的取捨：無 auth（VITE_AUTH_MODE=mock）、資料可清（TRUNCATE 一行）、
#   無個資，比賽 demo 範圍可接受；**交件文件要寫明這是 demo 取捨**。
#   要收緊就把 allow_origins 改成 CloudFront 網域白名單 —— 改這一行即可，
#   不需要動端點。
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(station_controller.router, prefix="/api/v1")
app.include_router(predict_controller.router, prefix="/api/v1")
app.include_router(assistant_controller.router, prefix="/api/v1")
app.include_router(dispatch_controller.router, prefix="/api/v1")
app.include_router(health_controller.router, prefix="/api/v1")


@app.exception_handler(AppError)
def app_error(_: Request, e: AppError):
    return JSONResponse(status_code=e.http,
                        content={"error": {"code": e.code, "message": e.message}})
