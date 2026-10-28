from fastapi import APIRouter

router = APIRouter(
    prefix="/template",
    tags=["template"],
)

@router.get("/")
async def get_template():
    return {}
