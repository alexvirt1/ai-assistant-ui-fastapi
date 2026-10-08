"""HTTP surface for the model picker.

GET  /api/models/current  the model in use - no VM call, so it is cheap
GET  /api/models          that plus the chat-capable models on the VM
PUT  /api/models/current  switch, body {"model": "<tag, role or 'default'>"}
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import selection

router = APIRouter(prefix="/api/models", tags=["models"])


class SelectModelRequest(BaseModel):
    model: str


def _current() -> dict:
    return {"current": selection.current_tag(), "default": selection.default_tag()}


@router.get("/current")
async def get_current() -> dict:
    return _current()


@router.get("")
async def list_models() -> dict:
    models = await selection.catalog()
    return {
        **_current(),
        # An empty list is ambiguous on its own; this tells the picker to say
        # "VM unreachable" rather than "no models".
        "reachable": bool(models),
        "models": [m.to_dict() for m in models],
    }


@router.put("/current")
async def select_model(request: SelectModelRequest) -> dict:
    try:
        await selection.select(request.model)
    except selection.ModelSelectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _current()
