import io
from typing import Any, Dict, List
from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
import pandas as pd

from backend.core.forecaster import trigger_forecast_rerun
from backend.core.importers import (
    ImportResult,
    import_inventory,
    import_locations,
    import_products,
    import_quotes,
    import_sales,
    import_suppliers,
)
from backend.core.security import get_current_user, require_role
from backend.core.validators import (
    validate_inventory_csv,
    validate_locations_json,
    validate_products_json,
    validate_quotes_csv,
    validate_sales_csv,
    validate_suppliers_csv,
)

router = APIRouter(prefix="/api/import", tags=["Data Import"])

MAX_CSV_BYTES = 10 * 1024 * 1024  # 10MB

TEMPLATES: Dict[str, str] = {
    "products": (
        "sku_code,name,category,unit\n"
        "FOODS_NEW_001,Sản phẩm thực phẩm mới,FOODS,pcs\n"
        "HOUSEHOLD_NEW_001,Sản phẩm gia dụng mới,HOUSEHOLD,pcs\n"
    ),
    "locations": (
        "code,name,region\n"
        "CA_3,California Store 3,CA\n"
        "TX_2,Texas Store 2,TX\n"
    ),
    "suppliers": (
        "supplier_code,name,lead_time_days,moq,category,payment_terms_days\n"
        "NEW_SUP_001,Nhà cung cấp A,5,20,FOODS,30\n"
        "NEW_SUP_002,Nhà cung cấp B,10,50,HOUSEHOLD,45\n"
    ),
    "quotes": (
        "supplier_code,item_id,store_id,unit_cost,month,lead_time_days,moq,tier1_min_qty,tier1_discount,tier2_min_qty,tier2_discount\n"
        "NEW_SUP_001,FOODS_2_074,CA_1,1.75,2016-06,5,20,100,0.03,500,0.06\n"
    ),
    "inventory": (
        "item_id,store_id,on_hand,on_order\n"
        "FOODS_2_074,CA_1,45,0\n"
        "FOODS_3_795,CA_1,10,20\n"
    ),
    "sales": (
        "item_id,store_id,date,qty_sold,sell_price\n"
        "FOODS_2_074,CA_1,2016-06-01,3,2.50\n"
        "FOODS_2_074,CA_1,2016-06-02,5,2.50\n"
        "FOODS_2_074,CA_1,2016-06-03,2,2.50\n"
    ),
}


def _compute_status(res: ImportResult) -> str:
    if res.rows_inserted + res.rows_updated == 0:
        return "FAILED"
    if res.rows_skipped == 0 and len(res.errors) == 0:
        return "SUCCESS"
    return "PARTIAL"


async def _read_csv_upload(file: UploadFile) -> pd.DataFrame:
    filename = file.filename or ""
    if not filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Định dạng file không hợp lệ. Chỉ chấp nhận file .csv",
        )

    raw_bytes = await file.read()
    if len(raw_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="File CSV không được rỗng",
        )
    if len(raw_bytes) > MAX_CSV_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Kích thước file vượt quá giới hạn 10MB",
        )

    text_content = None
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text_content = raw_bytes.decode(enc)
            if text_content.startswith("\ufeff"):
                text_content = text_content.lstrip("\ufeff")
            break
        except UnicodeDecodeError:
            continue

    if text_content is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Không thể giải mã nội dung file CSV",
        )

    try:
        df = pd.read_csv(io.StringIO(text_content))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Không thể phân tích cấu trúc file CSV: {e}",
        )
    return df


@router.post("/products", summary="Import hoặc cập nhật danh mục sản phẩm (SKU)")
def api_import_products(
    data: List[Dict[str, Any]] = Body(...),
    current_user: Dict[str, Any] = Depends(require_role("ADMIN")),
):
    is_valid, errors = validate_products_json(data)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Dữ liệu sản phẩm không hợp lệ", "errors": errors},
        )
    res = import_products(data, current_user["id"])
    return {
        "status": _compute_status(res),
        **res.to_dict(),
        "sku_codes": [str(item["sku_code"]).strip() for item in data],
    }


@router.post("/locations", summary="Import hoặc cập nhật danh mục kho/cửa hàng")
def api_import_locations(
    data: List[Dict[str, Any]] = Body(...),
    current_user: Dict[str, Any] = Depends(require_role("ADMIN")),
):
    is_valid, errors = validate_locations_json(data)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Dữ liệu cửa hàng không hợp lệ", "errors": errors},
        )
    res = import_locations(data, current_user["id"])
    return {
        "status": _compute_status(res),
        **res.to_dict(),
        "codes": [str(item["code"]).strip() for item in data],
    }


@router.post("/suppliers", summary="Import nhà cung cấp từ file CSV")
async def api_import_suppliers(
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(require_role("ADMIN")),
):
    df = await _read_csv_upload(file)
    is_valid, errors = validate_suppliers_csv(df)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Dữ liệu nhà cung cấp không hợp lệ", "errors": errors},
        )
    res = import_suppliers(df, current_user["id"])
    return {
        "status": _compute_status(res),
        "file": file.filename,
        **res.to_dict(),
    }


@router.post("/quotes", summary="Import báo giá nhà cung cấp từ file CSV")
async def api_import_quotes(
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(require_role("ADMIN")),
):
    df = await _read_csv_upload(file)
    is_valid, errors = validate_quotes_csv(df)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Dữ liệu báo giá không hợp lệ", "errors": errors},
        )
    res = import_quotes(df, current_user["id"])
    return {
        "status": _compute_status(res),
        "file": file.filename,
        **res.to_dict(),
    }


@router.post("/inventory", summary="Cập nhật tồn kho từ file CSV")
async def api_import_inventory(
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(require_role("ADMIN")),
):
    df = await _read_csv_upload(file)
    # Kiểm tra cấu trúc cột, số nguyên >= 0 và trùng lặp; SKU chưa có trong inventory_levels sẽ được import_inventory skip
    is_valid, errors = validate_inventory_csv(df, check_db=False)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Dữ liệu tồn kho không hợp lệ", "errors": errors},
        )
    res = import_inventory(df, current_user["id"])
    return {
        "status": _compute_status(res),
        "file": file.filename,
        **res.to_dict(),
    }


@router.post("/sales", summary="Import lịch sử bán hàng từ file CSV và kích hoạt dự báo")
async def api_import_sales(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(require_role("ADMIN")),
):
    df = await _read_csv_upload(file)
    is_valid, errors = validate_sales_csv(df)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Dữ liệu lịch sử bán hàng không hợp lệ", "errors": errors},
        )
    res = import_sales(df, current_user["id"])

    forecast_rerun_triggered = False
    if res.rows_inserted + res.rows_updated > 0:
        background_tasks.add_task(trigger_forecast_rerun)
        forecast_rerun_triggered = True

    return {
        "status": _compute_status(res),
        "file": file.filename,
        **res.to_dict(),
        "forecast_rerun_triggered": forecast_rerun_triggered,
    }


@router.get("/template/{type}", summary="Tải file CSV mẫu cho từng loại dữ liệu import")
def get_import_template(
    type: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    key = type.strip().lower()
    if key not in TEMPLATES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Loại template '{type}' không hợp lệ. Hỗ trợ: {', '.join(TEMPLATES.keys())}",
        )
    return Response(
        content=TEMPLATES[key],
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="template_{key}.csv"',
        },
    )
