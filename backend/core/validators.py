import datetime as dt
import re
from typing import Any, List, Tuple
import pandas as pd

from backend.core.database import execute_sql

ALLOWED_CATEGORIES = {"FOODS", "HOUSEHOLD", "HOBBIES"}
CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")


def _check_required_columns(df: pd.DataFrame, required: list) -> list:
    """Kiểm tra cột bắt buộc, trả về list cột thiếu."""
    return [col for col in required if col not in df.columns]


def _build_error(
    row_idx: int,
    column: str,
    value: Any,
    message: str,
    hint: str = None,
) -> dict:
    return {
        "row": int(row_idx) + 2,  # +2 vì dòng 1 là header, index bắt đầu từ 0
        "column": column,
        "value": "" if pd.isna(value) else str(value),
        "message": message,
        "hint": hint,
    }


def _parse_int(val: Any) -> int:
    if pd.isna(val) or isinstance(val, bool) or str(val).strip() == "":
        raise ValueError("not an int")
    f_val = float(str(val).strip())
    if not f_val.is_integer():
        raise ValueError("not an int")
    return int(f_val)


def _parse_float(val: Any) -> float:
    if pd.isna(val) or isinstance(val, bool) or str(val).strip() == "":
        raise ValueError("not a float")
    return float(str(val).strip())


def validate_products_json(data: list) -> Tuple[bool, List[dict]]:
    if not isinstance(data, list) or len(data) == 0:
        return False, [_build_error(-1, "body", "", "Danh sách sản phẩm không được rỗng")]

    errors: List[dict] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            errors.append(_build_error(idx, "body", item, "Mỗi phần tử phải là một object"))
            continue

        raw_sku = item.get("sku_code")
        sku_code = "" if raw_sku is None else str(raw_sku).strip()
        if not sku_code or not CODE_PATTERN.match(sku_code):
            errors.append(
                _build_error(
                    idx,
                    "sku_code",
                    raw_sku,
                    "sku_code bắt buộc, không rỗng và không chứa ký tự đặc biệt",
                )
            )

        raw_name = item.get("name")
        name = "" if raw_name is None else str(raw_name).strip()
        if not name:
            errors.append(_build_error(idx, "name", raw_name, "name là bắt buộc và không được rỗng"))

        raw_cat = item.get("category")
        category = "" if raw_cat is None else str(raw_cat).strip()
        if category not in ALLOWED_CATEGORIES:
            errors.append(
                _build_error(
                    idx,
                    "category",
                    raw_cat,
                    "category phải là FOODS, HOUSEHOLD hoặc HOBBIES",
                )
            )

        raw_unit = item.get("unit")
        if raw_unit is None or str(raw_unit).strip() == "":
            item["unit"] = "pcs"
        else:
            item["unit"] = str(raw_unit).strip()

    return len(errors) == 0, errors


def validate_locations_json(data: list) -> Tuple[bool, List[dict]]:
    if not isinstance(data, list) or len(data) == 0:
        return False, [_build_error(-1, "body", "", "Danh sách kho/cửa hàng không được rỗng")]

    errors: List[dict] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            errors.append(_build_error(idx, "body", item, "Mỗi phần tử phải là một object"))
            continue

        raw_code = item.get("code")
        code_str = "" if raw_code is None else str(raw_code)
        if not code_str.strip() or any(c.isspace() for c in code_str):
            errors.append(
                _build_error(
                    idx,
                    "code",
                    raw_code,
                    "code là bắt buộc, không rỗng và không chứa khoảng trắng",
                )
            )

        raw_name = item.get("name")
        name = "" if raw_name is None else str(raw_name).strip()
        if not name:
            errors.append(_build_error(idx, "name", raw_name, "name là bắt buộc và không được rỗng"))

    return len(errors) == 0, errors


def validate_sales_csv(df: pd.DataFrame) -> Tuple[bool, List[dict]]:
    required = ["item_id", "store_id", "date", "qty_sold"]
    missing = _check_required_columns(df, required)
    if missing:
        return False, [
            _build_error(-1, col, "", f"Thiếu cột bắt buộc: '{col}'") for col in missing
        ]

    valid_skus = {r["sku_code"] for r in execute_sql("SELECT sku_code FROM products", {})}
    valid_stores = {r["code"] for r in execute_sql("SELECT code FROM locations", {})}
    today = dt.date.today()

    errors: List[dict] = []
    seen_keys = set()

    for idx, row in df.iterrows():
        item_id = "" if pd.isna(row["item_id"]) else str(row["item_id"]).strip()
        store_id = "" if pd.isna(row["store_id"]) else str(row["store_id"]).strip()
        raw_date = row["date"]
        date_str = "" if pd.isna(raw_date) else str(raw_date).strip()

        # 2 & 3. date đúng định dạng YYYY-MM-DD và không trong tương lai
        try:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
                raise ValueError("invalid date format")
            parsed_date = dt.date.fromisoformat(date_str)
            if parsed_date > today:
                errors.append(
                    _build_error(idx, "date", raw_date, "Ngày bán không được nằm trong tương lai")
                )
        except ValueError:
            errors.append(
                _build_error(idx, "date", raw_date, "date phải đúng định dạng YYYY-MM-DD")
            )

        # 4. qty_sold >= 0 và là số nguyên
        raw_qty = row["qty_sold"]
        try:
            qty_val = _parse_int(raw_qty)
            if qty_val < 0:
                errors.append(
                    _build_error(idx, "qty_sold", raw_qty, "qty_sold phải >= 0")
                )
        except ValueError:
            errors.append(
                _build_error(idx, "qty_sold", raw_qty, "qty_sold phải là số nguyên >= 0")
            )

        # 5. sell_price > 0 nếu có giá trị
        if "sell_price" in df.columns:
            raw_price = row["sell_price"]
            if not pd.isna(raw_price) and str(raw_price).strip() != "":
                try:
                    price_val = _parse_float(raw_price)
                    if price_val <= 0:
                        errors.append(
                            _build_error(idx, "sell_price", raw_price, "sell_price phải > 0")
                        )
                except ValueError:
                    errors.append(
                        _build_error(idx, "sell_price", raw_price, "sell_price phải là số > 0")
                    )

        # 6. item_id tồn tại trong products.sku_code
        if item_id not in valid_skus:
            errors.append(
                _build_error(
                    idx,
                    "item_id",
                    item_id,
                    f"SKU '{item_id}' chưa có trong hệ thống",
                    hint="Gọi POST /api/import/products trước",
                )
            )

        # 7. store_id tồn tại trong locations.code
        if store_id not in valid_stores:
            errors.append(
                _build_error(
                    idx,
                    "store_id",
                    store_id,
                    f"Cửa hàng '{store_id}' chưa có trong hệ thống",
                    hint="Gọi POST /api/import/locations trước",
                )
            )

        # 8. Không có dòng trùng (item_id, store_id, date) trong chính file
        dup_key = (item_id, store_id, date_str)
        if dup_key in seen_keys:
            errors.append(
                _build_error(
                    idx,
                    "item_id,store_id,date",
                    f"{item_id},{store_id},{date_str}",
                    "Dòng trùng lặp (item_id, store_id, date) trong file",
                )
            )
        else:
            seen_keys.add(dup_key)

    return len(errors) == 0, errors


def validate_inventory_csv(df: pd.DataFrame, check_db: bool = True) -> Tuple[bool, List[dict]]:
    required = ["item_id", "store_id", "on_hand", "on_order"]
    missing = _check_required_columns(df, required)
    if missing:
        return False, [
            _build_error(-1, col, "", f"Thiếu cột bắt buộc: '{col}'") for col in missing
        ]

    valid_skus = set()
    valid_stores = set()
    existing_pairs = set()
    if check_db:
        valid_skus = {r["sku_code"] for r in execute_sql("SELECT sku_code FROM products", {})}
        valid_stores = {r["code"] for r in execute_sql("SELECT code FROM locations", {})}
        pair_rows = execute_sql(
            """SELECT p.sku_code, l.code AS store_code
               FROM inventory_levels il
               JOIN products p ON il.product_id = p.id
               JOIN locations l ON il.location_id = l.id""",
            {},
        )
        existing_pairs = {(r["sku_code"], r["store_code"]) for r in pair_rows}

    errors: List[dict] = []
    seen_keys = set()

    for idx, row in df.iterrows():
        item_id = "" if pd.isna(row["item_id"]) else str(row["item_id"]).strip()
        store_id = "" if pd.isna(row["store_id"]) else str(row["store_id"]).strip()

        # 1. on_hand >= 0, on_order >= 0, cả hai là số nguyên
        for col in ("on_hand", "on_order"):
            raw_val = row[col]
            try:
                ival = _parse_int(raw_val)
                if ival < 0:
                    errors.append(_build_error(idx, col, raw_val, f"{col} phải >= 0"))
            except ValueError:
                errors.append(_build_error(idx, col, raw_val, f"{col} phải là số nguyên >= 0"))

        if check_db:
            # 2. item_id tồn tại trong products.sku_code
            sku_ok = item_id in valid_skus
            if not sku_ok:
                errors.append(
                    _build_error(
                        idx,
                        "item_id",
                        item_id,
                        f"SKU '{item_id}' chưa có trong hệ thống",
                        hint="Gọi POST /api/import/products trước",
                    )
                )

            # 3. store_id tồn tại trong locations.code
            store_ok = store_id in valid_stores
            if not store_ok:
                errors.append(
                    _build_error(
                        idx,
                        "store_id",
                        store_id,
                        f"Cửa hàng '{store_id}' chưa có trong hệ thống",
                        hint="Gọi POST /api/import/locations trước",
                    )
                )

            # 4. Cặp (item_id, store_id) đã có trong inventory_levels
            if sku_ok and store_ok and (item_id, store_id) not in existing_pairs:
                errors.append(
                    _build_error(
                        idx,
                        "item_id,store_id",
                        f"{item_id},{store_id}",
                        "Chưa có bản ghi tồn kho cho cặp này",
                        hint="Chỉ cập nhật SKU đã có, không tạo mới tự động",
                    )
                )

        # 5. Không có dòng trùng (item_id, store_id) trong chính file
        dup_key = (item_id, store_id)
        if dup_key in seen_keys:
            errors.append(
                _build_error(
                    idx,
                    "item_id,store_id",
                    f"{item_id},{store_id}",
                    "Dòng trùng lặp (item_id, store_id) trong file",
                )
            )
        else:
            seen_keys.add(dup_key)

    return len(errors) == 0, errors


def validate_suppliers_csv(df: pd.DataFrame) -> Tuple[bool, List[dict]]:
    required = ["supplier_code", "name", "lead_time_days", "moq"]
    missing = _check_required_columns(df, required)
    if missing:
        return False, [
            _build_error(-1, col, "", f"Thiếu cột bắt buộc: '{col}'") for col in missing
        ]

    errors: List[dict] = []
    seen_codes = set()

    for idx, row in df.iterrows():
        raw_code = row["supplier_code"]
        code = "" if pd.isna(raw_code) else str(raw_code).strip()
        raw_name = row["name"]
        name = "" if pd.isna(raw_name) else str(raw_name).strip()

        if not name:
            errors.append(_build_error(idx, "name", raw_name, "name không được rỗng"))

        # 1. lead_time_days > 0, là số nguyên
        raw_lt = row["lead_time_days"]
        try:
            lt_val = _parse_int(raw_lt)
            if lt_val <= 0:
                errors.append(_build_error(idx, "lead_time_days", raw_lt, "lead_time_days phải > 0"))
        except ValueError:
            errors.append(
                _build_error(idx, "lead_time_days", raw_lt, "lead_time_days phải là số nguyên > 0")
            )

        # 2. moq > 0, là số nguyên
        raw_moq = row["moq"]
        try:
            moq_val = _parse_int(raw_moq)
            if moq_val <= 0:
                errors.append(_build_error(idx, "moq", raw_moq, "moq phải > 0"))
        except ValueError:
            errors.append(_build_error(idx, "moq", raw_moq, "moq phải là số nguyên > 0"))

        # 3. payment_terms_days >= 0 nếu có
        if "payment_terms_days" in df.columns:
            raw_pt = row["payment_terms_days"]
            if not pd.isna(raw_pt) and str(raw_pt).strip() != "":
                try:
                    pt_val = _parse_int(raw_pt)
                    if pt_val < 0:
                        errors.append(
                            _build_error(
                                idx, "payment_terms_days", raw_pt, "payment_terms_days phải >= 0"
                            )
                        )
                except ValueError:
                    errors.append(
                        _build_error(
                            idx,
                            "payment_terms_days",
                            raw_pt,
                            "payment_terms_days phải là số nguyên >= 0",
                        )
                    )

        # 4. supplier_code không chứa ký tự đặc biệt (chỉ chữ, số, _, -)
        if not code or not CODE_PATTERN.match(code):
            errors.append(
                _build_error(
                    idx,
                    "supplier_code",
                    raw_code,
                    "supplier_code chỉ được chứa chữ, số, _, -",
                )
            )

        # 5. Không có dòng trùng supplier_code trong chính file
        if code in seen_codes:
            errors.append(
                _build_error(
                    idx,
                    "supplier_code",
                    code,
                    "Dòng trùng lặp supplier_code trong file",
                )
            )
        else:
            seen_codes.add(code)

    return len(errors) == 0, errors


def validate_quotes_csv(df: pd.DataFrame) -> Tuple[bool, List[dict]]:
    required = ["supplier_code", "item_id", "store_id", "unit_cost", "month"]
    missing = _check_required_columns(df, required)
    if missing:
        return False, [
            _build_error(-1, col, "", f"Thiếu cột bắt buộc: '{col}'") for col in missing
        ]

    valid_suppliers = {r["code"] for r in execute_sql("SELECT code FROM suppliers", {})}
    valid_skus = {r["sku_code"] for r in execute_sql("SELECT sku_code FROM products", {})}
    valid_stores = {r["code"] for r in execute_sql("SELECT code FROM locations", {})}

    errors: List[dict] = []
    seen_keys = set()

    for idx, row in df.iterrows():
        sup_code = "" if pd.isna(row["supplier_code"]) else str(row["supplier_code"]).strip()
        item_id = "" if pd.isna(row["item_id"]) else str(row["item_id"]).strip()
        store_id = "" if pd.isna(row["store_id"]) else str(row["store_id"]).strip()
        raw_month = row["month"]
        month_str = "" if pd.isna(raw_month) else str(raw_month).strip()

        # 1. unit_cost > 0
        raw_cost = row["unit_cost"]
        try:
            cost_val = _parse_float(raw_cost)
            if cost_val <= 0:
                errors.append(_build_error(idx, "unit_cost", raw_cost, "unit_cost phải > 0"))
        except ValueError:
            errors.append(_build_error(idx, "unit_cost", raw_cost, "unit_cost phải là số > 0"))

        # 2. month đúng định dạng YYYY-MM
        try:
            if not MONTH_PATTERN.match(month_str):
                raise ValueError("invalid month format")
            dt.datetime.strptime(month_str, "%Y-%m")
        except ValueError:
            errors.append(_build_error(idx, "month", raw_month, "month phải đúng định dạng YYYY-MM"))

        # 3. tier1_discount, tier2_discount trong [0, 1] nếu có
        for disc_col in ("tier1_discount", "tier2_discount"):
            if disc_col in df.columns:
                raw_disc = row[disc_col]
                if not pd.isna(raw_disc) and str(raw_disc).strip() != "":
                    try:
                        d_val = _parse_float(raw_disc)
                        if not (0.0 <= d_val <= 1.0):
                            errors.append(
                                _build_error(
                                    idx, disc_col, raw_disc, f"{disc_col} phải nằm trong đoạn [0, 1]"
                                )
                            )
                    except ValueError:
                        errors.append(
                            _build_error(idx, disc_col, raw_disc, f"{disc_col} phải là số trong [0, 1]")
                        )

        # 4. tier1_min_qty < tier2_min_qty nếu cả hai có
        t1_qty = None
        t2_qty = None
        if "tier1_min_qty" in df.columns and not pd.isna(row["tier1_min_qty"]) and str(row["tier1_min_qty"]).strip() != "":
            try:
                t1_qty = _parse_int(row["tier1_min_qty"])
            except ValueError:
                errors.append(_build_error(idx, "tier1_min_qty", row["tier1_min_qty"], "tier1_min_qty phải là số nguyên"))
        if "tier2_min_qty" in df.columns and not pd.isna(row["tier2_min_qty"]) and str(row["tier2_min_qty"]).strip() != "":
            try:
                t2_qty = _parse_int(row["tier2_min_qty"])
            except ValueError:
                errors.append(_build_error(idx, "tier2_min_qty", row["tier2_min_qty"], "tier2_min_qty phải là số nguyên"))

        if t1_qty is not None and t2_qty is not None and t1_qty >= t2_qty:
            errors.append(
                _build_error(
                    idx,
                    "tier1_min_qty,tier2_min_qty",
                    f"{t1_qty},{t2_qty}",
                    "tier1_min_qty phải nhỏ hơn tier2_min_qty",
                )
            )

        # 5. supplier_code tồn tại trong suppliers.code
        if sup_code not in valid_suppliers:
            errors.append(
                _build_error(
                    idx,
                    "supplier_code",
                    sup_code,
                    f"Nhà cung cấp '{sup_code}' chưa có trong hệ thống",
                    hint="Gọi POST /api/import/suppliers trước",
                )
            )

        # 6. item_id tồn tại trong products.sku_code
        if item_id not in valid_skus:
            errors.append(
                _build_error(
                    idx,
                    "item_id",
                    item_id,
                    f"SKU '{item_id}' chưa có trong hệ thống",
                    hint="Gọi POST /api/import/products trước",
                )
            )

        # 7. store_id tồn tại trong locations.code
        if store_id not in valid_stores:
            errors.append(
                _build_error(
                    idx,
                    "store_id",
                    store_id,
                    f"Cửa hàng '{store_id}' chưa có trong hệ thống",
                    hint="Gọi POST /api/import/locations trước",
                )
            )

        # 8. Không có dòng trùng (supplier_code, item_id, store_id, month)
        dup_key = (sup_code, item_id, store_id, month_str)
        if dup_key in seen_keys:
            errors.append(
                _build_error(
                    idx,
                    "supplier_code,item_id,store_id,month",
                    f"{sup_code},{item_id},{store_id},{month_str}",
                    "Dòng trùng lặp (supplier_code, item_id, store_id, month) trong file",
                )
            )
        else:
            seen_keys.add(dup_key)

    return len(errors) == 0, errors
