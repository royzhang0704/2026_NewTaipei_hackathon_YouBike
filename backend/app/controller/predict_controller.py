from fastapi import APIRouter

from app.schema.dto import PredictRequest
from app.service import predict_service

router = APIRouter()


@router.post("/predict")
def predict(req: PredictRequest):
    return predict_service.predict_one(req.station_uid, req.at, req.is_holiday)
