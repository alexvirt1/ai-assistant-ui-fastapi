"""GET /api/cloud: is a cloud model available, and what has it cost this month."""

from fastapi import APIRouter, Depends

from ..identity import current_user_id
from . import models, usage

router = APIRouter(prefix="/api/cloud", tags=["cloud"])


@router.get("")
async def cloud_status(user_id: str = Depends(current_user_id)) -> dict:
    spec = models.default_model()
    try:
        spent = await usage.month_total(user_id)
    except Exception:
        spent = None
    return {
        "configured": spec is not None,
        "model": spec.describe() if spec else None,
        "budget_usd": usage.budget_usd(),
        "month_spent_usd": None if spent is None else round(spent, 4),
    }
