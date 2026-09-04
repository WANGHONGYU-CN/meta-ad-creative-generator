"""产品接口：产品是一等实体（决策 20）——任务、场景库、参考图继承全部挂产品 id。"""
from fastapi import APIRouter, HTTPException

from server.schemas import ProductCreate, ProductPatch
from server.services import state_store as st

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("")
def list_products():
    return st.list_products()


@router.post("", status_code=201)
def create_product(body: ProductCreate):
    try:
        return st.create_product(
            body.name.strip(), body.info, body.brand_name, body.ad_language
        )
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.patch("/{product_id}")
def patch_product(product_id: int, body: ProductPatch):
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "没有要修改的字段")
    if "name" in fields:
        fields["name"] = fields["name"].strip()
    try:
        return st.patch_product(product_id, fields)
    except KeyError:
        raise HTTPException(404, "产品不存在")
    except ValueError as e:
        raise HTTPException(409, str(e))
