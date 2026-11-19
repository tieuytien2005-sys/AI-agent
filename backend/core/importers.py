import calendar
from dataclasses import dataclass, field
import json
import time
from typing import Any, Dict, List
import pandas as pd

from backend.core.database import execute_sql


@dataclass
class ImportResult:
    rows_total:    int = 0
    rows_inserted: int = 0
    rows_updated:  int = 0
    rows_skipped:  int = 0
    errors:        list = field(default_factory=list)
    duration_ms:   int = 0

    def to_dict(self) -> dict:
        return {
            "rows_total":    self.rows_total,
            "rows_inserted": self.rows_inserted,
            "rows_updated":  self.rows_updated,
            "rows_skipped":  self.rows_skipped,
            "errors":        self.errors,
            "duration_ms":   self.duration_ms,
        }


def _get_product_map() -> dict:
    """Trả về {sku_code: product_id} cho tất cả products."""
    rows = execute_sql("SELECT id, sku_code FROM products", {})
    return {r["sku_code"]: r["id"] for r in rows}


def _get_location_map() -> dict:
    """Trả về {code: location_id} cho tất cả locations."""
    rows = execute_sql("SELECT id, code FROM locations", {})
    return {r["code"]: r["id"] for r in rows}


def _get_supplier_map() -> dict:
    """Trả về {code: supplier_id} cho tất cả suppliers."""
    rows = execute_sql("SELECT id, code FROM suppliers", {})
    return {r["code"]: r["id"] for r in rows}


def _write_audit_log(user_id: int, action: str, entity: str, after_data: dict):
    execute_sql(
        """
        INSERT INTO audit_logs
          (actor_type, actor_id, action, entity, after_data)
        VALUES ('USER', %(uid)s, %(action)s, %(entity)s, %(data)s::jsonb)
        """,
        {
            "uid": user_id,
            "action": action,
            "entity": entity,
            "data": json.dumps(after_data),
        },
    )


def import_products(data: List[Dict[str, Any]], user_id: int) -> ImportResult:
    t0 = time.perf_counter()
    result = ImportResult(rows_total=len(data))

    for item in data:
        sku_code = str(item["sku_code"]).strip()
        name = str(item["name"]).strip()
        category = str(item["category"]).strip()
        unit = str(item.get("unit") or "pcs").strip() or "pcs"

        rows = execute_sql(
            """
            INSERT INTO products (sku_code, name, category, unit, is_active)
            VALUES (%(sku_code)s, %(name)s, %(category)s, %(unit)s, TRUE)
            ON CONFLICT (sku_code)
            DO UPDATE SET name     = EXCLUDED.name,
                          category = EXCLUDED.category,
                          unit     = EXCLUDED.unit
            RETURNING (xmax = 0) AS inserted
            """,
            {
                "sku_code": sku_code,
                "name": name,
                "category": category,
                "unit": unit,
            },
        )
        if rows and rows[0]["inserted"]:
            result.rows_inserted += 1
        else:
            result.rows_updated += 1

    result.duration_ms = int((time.perf_counter() - t0) * 1000)
    _write_audit_log(
        user_id,
        "IMPORT_PRODUCTS",
        "products",
        {
            "rows": result.rows_inserted + result.rows_updated,
            "rows_inserted": result.rows_inserted,
            "rows_updated": result.rows_updated,
        },
    )
    return result


def import_locations(data: List[Dict[str, Any]], user_id: int) -> ImportResult:
    t0 = time.perf_counter()
    result = ImportResult(rows_total=len(data))

    for item in data:
        code = str(item["code"]).strip()
        name = str(item["name"]).strip()
        region = item.get("region")
        region_val = None if region is None or str(region).strip() == "" else str(region).strip()

        rows = execute_sql(
            """
            INSERT INTO locations (code, name, region)
            VALUES (%(code)s, %(name)s, %(region)s)
            ON CONFLICT (code)
            DO UPDATE SET name = EXCLUDED.name, region = EXCLUDED.region
            RETURNING (xmax = 0) AS inserted
            """,
            {"code": code, "name": name, "region": region_val},
        )
        if rows and rows[0]["inserted"]:
            result.rows_inserted += 1
        else:
            result.rows_updated += 1

    result.duration_ms = int((time.perf_counter() - t0) * 1000)
    _write_audit_log(
        user_id,
        "IMPORT_LOCATIONS",
        "locations",
        {
            "rows": result.rows_inserted + result.rows_updated,
            "rows_inserted": result.rows_inserted,
            "rows_updated": result.rows_updated,
        },
    )
    return result


def import_suppliers(df: pd.DataFrame, user_id: int) -> ImportResult:
    t0 = time.perf_counter()
    result = ImportResult(rows_total=len(df))
    _get_supplier_map()

    for _, row in df.iterrows():
        code = str(row["supplier_code"]).strip()
        name = str(row["name"]).strip()
        lead_time_days = int(float(row["lead_time_days"]))
        moq = int(float(row["moq"]))

        category = None
        if "category" in df.columns and not pd.isna(row["category"]) and str(row["category"]).strip() != "":
            category = str(row["category"]).strip()

        payment_terms_days = 30
        if (
            "payment_terms_days" in df.columns
            and not pd.isna(row["payment_terms_days"])
            and str(row["payment_terms_days"]).strip() != ""
        ):
            payment_terms_days = int(float(row["payment_terms_days"]))

        rows = execute_sql(
            """
            INSERT INTO suppliers (code, name, category,
              lead_time_days, moq, payment_terms_days, is_active)
            VALUES (%(code)s, %(name)s, %(category)s,
                    %(lead_time_days)s, %(moq)s, %(payment_terms_days)s, TRUE)
            ON CONFLICT (code)
            DO UPDATE SET name = EXCLUDED.name,
                          category = EXCLUDED.category,
                          lead_time_days = EXCLUDED.lead_time_days,
                          moq = EXCLUDED.moq,
                          payment_terms_days = EXCLUDED.payment_terms_days
            RETURNING (xmax = 0) AS inserted
            """,
            {
                "code": code,
                "name": name,
                "category": category,
                "lead_time_days": lead_time_days,
                "moq": moq,
                "payment_terms_days": payment_terms_days,
            },
        )
        if rows and rows[0]["inserted"]:
            result.rows_inserted += 1
        else:
            result.rows_updated += 1

    result.duration_ms = int((time.perf_counter() - t0) * 1000)
    _write_audit_log(
        user_id,
        "IMPORT_SUPPLIERS",
        "suppliers",
        {
            "rows": result.rows_inserted + result.rows_updated,
            "rows_inserted": result.rows_inserted,
            "rows_updated": result.rows_updated,
        },
    )
    return result


def import_sales(df: pd.DataFrame, user_id: int) -> ImportResult:
    t0 = time.perf_counter()
    result = ImportResult(rows_total=len(df))
    product_map = _get_product_map()
    location_map = _get_location_map()

    dates_imported: List[str] = []

    for i, row in df.iterrows():
        item_id = "" if pd.isna(row["item_id"]) else str(row["item_id"]).strip()
        store_id = "" if pd.isna(row["store_id"]) else str(row["store_id"]).strip()
        pid = product_map.get(item_id)
        lid = location_map.get(store_id)

        if pid is None or lid is None:
            result.rows_skipped += 1
            result.errors.append(
                {
                    "row": int(i) + 2,
                    "column": "item_id,store_id",
                    "value": f"{item_id},{store_id}",
                    "message": "Không tìm thấy SKU hoặc cửa hàng trong hệ thống",
                    "hint": "Gọi POST /api/import/products hoặc /api/import/locations trước",
                }
            )
            continue

        sale_date = str(row["date"]).strip()
        qty_sold = int(float(row["qty_sold"]))
        sell_price = None
        if (
            "sell_price" in df.columns
            and not pd.isna(row["sell_price"])
            and str(row["sell_price"]).strip() != ""
        ):
            sell_price = round(float(row["sell_price"]), 2)

        rows = execute_sql(
            """
            INSERT INTO demand_history
              (product_id, location_id, sale_date, qty_sold, sell_price)
            VALUES (%(pid)s, %(lid)s, %(date)s, %(qty)s, %(price)s)
            ON CONFLICT (product_id, location_id, sale_date)
            DO UPDATE SET qty_sold   = EXCLUDED.qty_sold,
                          sell_price = EXCLUDED.sell_price
            RETURNING (xmax = 0) AS inserted
            """,
            {
                "pid": pid,
                "lid": lid,
                "date": sale_date,
                "qty": qty_sold,
                "price": sell_price,
            },
        )
        if rows and rows[0]["inserted"]:
            result.rows_inserted += 1
        else:
            result.rows_updated += 1
        dates_imported.append(sale_date)

    result.duration_ms = int((time.perf_counter() - t0) * 1000)
    min_date = min(dates_imported) if dates_imported else None
    max_date = max(dates_imported) if dates_imported else None
    _write_audit_log(
        user_id,
        "IMPORT_SALES",
        "demand_history",
        {
            "rows": result.rows_inserted + result.rows_updated,
            "date_range": [min_date, max_date],
        },
    )
    return result


def import_inventory(df: pd.DataFrame, user_id: int) -> ImportResult:
    t0 = time.perf_counter()
    result = ImportResult(rows_total=len(df))
    product_map = _get_product_map()
    location_map = _get_location_map()

    for i, row in df.iterrows():
        item_id = "" if pd.isna(row["item_id"]) else str(row["item_id"]).strip()
        store_id = "" if pd.isna(row["store_id"]) else str(row["store_id"]).strip()
        pid = product_map.get(item_id)
        lid = location_map.get(store_id)

        if pid is None or lid is None:
            result.rows_skipped += 1
            result.errors.append(
                {
                    "row": int(i) + 2,
                    "column": "item_id,store_id",
                    "value": f"{item_id},{store_id}",
                    "message": "Chưa có bản ghi tồn kho, bỏ qua",
                    "hint": "inventory_levels chỉ cập nhật SKU đã có",
                }
            )
            continue

        on_hand = int(float(row["on_hand"]))
        on_order = int(float(row["on_order"]))

        updated = execute_sql(
            """
            UPDATE inventory_levels
            SET on_hand    = %(on_hand)s,
                on_order   = %(on_order)s,
                updated_at = now()
            WHERE product_id  = %(pid)s
              AND location_id = %(lid)s
            RETURNING 1 AS updated
            """,
            {
                "on_hand": on_hand,
                "on_order": on_order,
                "pid": pid,
                "lid": lid,
            },
        )
        if not updated:
            result.rows_skipped += 1
            result.errors.append(
                {
                    "row": int(i) + 2,
                    "column": "item_id,store_id",
                    "value": f"{row['item_id']},{row['store_id']}",
                    "message": "Chưa có bản ghi tồn kho, bỏ qua",
                    "hint": "inventory_levels chỉ cập nhật SKU đã có",
                }
            )
        else:
            result.rows_updated += 1

    result.duration_ms = int((time.perf_counter() - t0) * 1000)
    _write_audit_log(
        user_id,
        "IMPORT_INVENTORY",
        "inventory_levels",
        {
            "rows": result.rows_updated,
            "rows_updated": result.rows_updated,
            "rows_skipped": result.rows_skipped,
        },
    )
    return result


def import_quotes(df: pd.DataFrame, user_id: int) -> ImportResult:
    t0 = time.perf_counter()
    result = ImportResult(rows_total=len(df))
    supplier_map = _get_supplier_map()
    product_map = _get_product_map()
    location_map = _get_location_map()

    sup_defaults_rows = execute_sql(
        "SELECT id, lead_time_days, moq FROM suppliers", {}
    )
    sup_defaults = {r["id"]: (r["lead_time_days"], r["moq"]) for r in sup_defaults_rows}

    for i, row in df.iterrows():
        sup_code = "" if pd.isna(row["supplier_code"]) else str(row["supplier_code"]).strip()
        item_id = "" if pd.isna(row["item_id"]) else str(row["item_id"]).strip()
        store_id = "" if pd.isna(row["store_id"]) else str(row["store_id"]).strip()

        sid = supplier_map.get(sup_code)
        pid = product_map.get(item_id)
        lid = location_map.get(store_id)

        if sid is None or pid is None or lid is None:
            result.rows_skipped += 1
            result.errors.append(
                {
                    "row": int(i) + 2,
                    "column": "supplier_code,item_id,store_id",
                    "value": f"{sup_code},{item_id},{store_id}",
                    "message": "Không tìm thấy nhà cung cấp, SKU hoặc cửa hàng",
                    "hint": "Import danh mục gốc trước khi import báo giá",
                }
            )
            continue

        month_str = str(row["month"]).strip()
        year_int, month_int = [int(x) for x in month_str.split("-")]
        last_day = calendar.monthrange(year_int, month_int)[1]
        valid_from = f"{month_str}-01"
        valid_to = f"{month_str}-{last_day:02d}"

        unit_cost = round(float(row["unit_cost"]), 2)
        default_lt, default_moq = sup_defaults.get(sid, (7, 1))

        lead_time_days = default_lt
        if (
            "lead_time_days" in df.columns
            and not pd.isna(row["lead_time_days"])
            and str(row["lead_time_days"]).strip() != ""
        ):
            lead_time_days = int(float(row["lead_time_days"]))

        moq = default_moq
        if "moq" in df.columns and not pd.isna(row["moq"]) and str(row["moq"]).strip() != "":
            moq = int(float(row["moq"]))

        # Vô hiệu hóa báo giá cũ cùng kỳ (đảm bảo valid_to >= valid_from theo CHECK constraint)
        execute_sql(
            """
            UPDATE supplier_quotes
            SET valid_to = GREATEST(valid_from, %(valid_from)s::date - 1)
            WHERE supplier_id = %(sid)s
              AND product_id  = %(pid)s
              AND location_id = %(lid)s
              AND valid_from <= %(valid_from)s::date
              AND (valid_to IS NULL OR valid_to >= %(valid_from)s::date)
            """,
            {
                "valid_from": valid_from,
                "sid": sid,
                "pid": pid,
                "lid": lid,
            },
        )

        quote_rows = execute_sql(
            """
            INSERT INTO supplier_quotes
              (supplier_id, product_id, location_id, base_unit_price,
               lead_time_days, moq, valid_from, valid_to)
            VALUES
              (%(sid)s, %(pid)s, %(lid)s, %(unit_cost)s,
               %(lt)s, %(moq)s, %(valid_from)s::date, %(valid_to)s::date)
            RETURNING id
            """,
            {
                "sid": sid,
                "pid": pid,
                "lid": lid,
                "unit_cost": unit_cost,
                "lt": lead_time_days,
                "moq": moq,
                "valid_from": valid_from,
                "valid_to": valid_to,
            },
        )
        quote_id = quote_rows[0]["id"]

        # Bậc 1: (quote_id, 1, unit_cost)
        breaks = [(1, unit_cost)]

        # Bậc 2: tier1
        if (
            "tier1_min_qty" in df.columns
            and not pd.isna(row["tier1_min_qty"])
            and str(row["tier1_min_qty"]).strip() != ""
        ):
            t1_qty = int(float(row["tier1_min_qty"]))
            t1_disc = 0.0
            if (
                "tier1_discount" in df.columns
                and not pd.isna(row["tier1_discount"])
                and str(row["tier1_discount"]).strip() != ""
            ):
                t1_disc = float(row["tier1_discount"])
            t1_price = round(unit_cost * (1.0 - t1_disc), 2)
            breaks.append((t1_qty, t1_price))

        # Bậc 3: tier2
        if (
            "tier2_min_qty" in df.columns
            and not pd.isna(row["tier2_min_qty"])
            and str(row["tier2_min_qty"]).strip() != ""
        ):
            t2_qty = int(float(row["tier2_min_qty"]))
            t2_disc = 0.0
            if (
                "tier2_discount" in df.columns
                and not pd.isna(row["tier2_discount"])
                and str(row["tier2_discount"]).strip() != ""
            ):
                t2_disc = float(row["tier2_discount"])
            t2_price = round(unit_cost * (1.0 - t2_disc), 2)
            breaks.append((t2_qty, t2_price))

        for min_qty, price in breaks:
            execute_sql(
                """
                INSERT INTO quote_price_breaks (quote_id, min_qty, unit_price)
                VALUES (%(qid)s, %(min_qty)s, %(price)s)
                ON CONFLICT (quote_id, min_qty)
                DO UPDATE SET unit_price = EXCLUDED.unit_price
                """,
                {"qid": quote_id, "min_qty": min_qty, "price": price},
            )

        result.rows_inserted += 1

    result.duration_ms = int((time.perf_counter() - t0) * 1000)
    _write_audit_log(
        user_id,
        "IMPORT_QUOTES",
        "supplier_quotes",
        {
            "rows": result.rows_inserted,
            "rows_inserted": result.rows_inserted,
            "rows_skipped": result.rows_skipped,
        },
    )
    return result
