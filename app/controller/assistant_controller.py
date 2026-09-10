import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.service import assistant_service

router = APIRouter()


@router.post("/assistant/chat")
async def assistant_chat(req: Request):
    """調度助理對話端點（SSE）。契約見前端 features/assistant/types.ts：
       req  { messages: [{role, content}], context: {town_code, station_uid, virtual_now, thread_id} }
       SSE  data: {"type":"delta"|"sources"|"actions"|"suggestions"|"done"|"error", ...}

    即時意圖直接查 DB 回答（精確、附按鈕）；其餘轉 AgentCore 知識庫。
    """
    body = await req.json()
    messages = body.get("messages") or []
    context = body.get("context") or {}

    def gen():
        try:
            for ev in assistant_service.chat_events(messages, context):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger("assistant").exception("assistant_chat stream failed")
            yield f"data: {json.dumps({'type': 'error', 'message': '助理服務錯誤'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )
