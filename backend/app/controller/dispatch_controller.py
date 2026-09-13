from fastapi import APIRouter, Path, Query

from app.schema.dto import DispatchCreate
from app.service import dispatch_service

router = APIRouter()


@router.get("/dispatch/orders")
def orders(status: str = Query(default="active", pattern="^(active|fulfilled|invalid)$")):
    """調度單清單。★ 不吃 town_code —— 跨區調度的兩端本來就分屬不同區，
    篩掉任一端線就斷了。前端一次全撈，區的切換純前端（體例同 /stations）。"""
    return dispatch_service.list_orders(status)


@router.get("/dispatch/candidates/{uid}")
def candidates(uid: str, action: str | None = Query(default=None,
                                                    pattern="^(refill|remove)$")):
    """某風險站的調度候選（案丙排序）。助理按鈕路徑走 SSE，這支給前端直查／除錯用。"""
    return dispatch_service.candidates(uid, action)


@router.post("/dispatch/orders")
def create(body: DispatchCreate):
    """確認調度。★ 本服務的第一個寫入端點（見 main.py 的 CORS 註解）。
    驗證失敗整批 400，不做 clamp —— 默默改掉使用者按下的數字比退回更糟。"""
    return dispatch_service.create_orders(
        body.anchor_uid, body.action,
        [{"uid": i.uid, "bikes": i.bikes} for i in body.items])


@router.delete("/dispatch/orders/{order_id}")
def cancel(order_id: int = Path(ge=1)):
    """人工撤銷 → status='invalid'。軟刪不 DELETE，撤銷紀錄要留著查。"""
    return dispatch_service.cancel(order_id)
