"""
Nạp dữ liệu PoC vào PostgreSQL theo schema.sql.

Ánh xạ chính:
  selected_skus.csv            -> locations, products, inventory_policies
  suppliers.csv (bỏ *_true)    -> suppliers
  sales_clean_long.csv         -> demand_history
  inventory_snapshot.csv       -> inventory_levels, inventory_policies.default_supplier_id
  supplier_quotes.csv          -> supplier_quotes, quote_price_breaks
  supplier_performance.csv     -> supplier_evaluations
  purchase_orders.csv          -> purchase_requests + purchase_request_lines
                                  + request_decisions (APPROVE) + purchase_orders
  inventory_daily.csv          -> KHÔNG nạp (schema hiện chưa có bảng lịch sử tồn theo ngày)

Lưu ý:
  - Tiền tệ là USD, vì giá lấy từ M5. Schema mặc định là VND nên script ghi rõ USD.
  - Đơn mua PoC có status IN_TRANSIT, schema không có. Ánh xạ sang SENT.
  - Đơn mua PoC có source SIMULATED_POLICY, schema dùng SIMULATED.

Cách dùng:
  python load_to_postgres.py --dsn "postgresql://app:<pw>@localhost:5432/procurement" \
                             --clean-dir ./output --proc-dir ./procurement_data
"""

import argparse
import datetime as dt
import math
import os

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

SYSTEM_EMAIL = "system@poc.local"
PO_STATUS_MAP = {"RECEIVED": "RECEIVED", "IN_TRANSIT": "SENT"}
M5_START = dt.date(2011, 1, 29)
M5_END = dt.date(2016, 5, 22)
DEFAULT_SAFETY_DAYS = 7
DEFAULT_TARGET_COVER = 21
DEFAULT_MAX_STOCK = 60
CURRENCY = "USD"

DATA_TABLES = [
    "purchase_request_lines", "purchase_orders", "request_decisions", "purchase_requests",
    "supplier_evaluations", "quote_price_breaks", "supplier_quotes", "demand_history",
    "inventory_levels", "inventory_policies", "products", "locations", "suppliers",
]


def clean(v):
    """Chuyển NaN của pandas thành None cho PostgreSQL và đổi kiểu numpy sang kiểu Python."""
    if v is None or pd.isna(v):
        return None
    return v.item() if hasattr(v, "item") else v


def month_bounds(month: str):
    p = pd.Period(month, "M")
    return p.start_time.date(), p.end_time.date()


def reset(cur):
    cur.execute("TRUNCATE " + ", ".join(DATA_TABLES) + " RESTART IDENTITY CASCADE")


def ensure_system_user(cur) -> int:
    cur.execute(
        """INSERT INTO users(email, full_name, password_hash, role, is_active)
           VALUES (%s, 'Hệ thống (dữ liệu mô phỏng)', '!disabled', 'ADMIN', FALSE)
           ON CONFLICT (email) DO UPDATE SET full_name = EXCLUDED.full_name
           RETURNING id""",
        (SYSTEM_EMAIL,),
    )
    return cur.fetchone()[0]


def load_locations(cur, sel):
    mapping = {}
    for _, r in sel.drop_duplicates("store_id").iterrows():
        cur.execute(
            "INSERT INTO locations(code, name, region) VALUES (%s, %s, %s) RETURNING id",
            (r["store_id"], r["store_id"], r["state_id"]),
        )
        mapping[r["store_id"]] = cur.fetchone()[0]
    return mapping


def load_products(cur, sel):
    mapping = {}
    for _, r in sel.drop_duplicates("item_id").iterrows():
        cur.execute(
            """INSERT INTO products(sku_code, name, category, m5_item_id)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (r["item_id"], r["item_id"], r["cat_id"], r["item_id"]),
        )
        mapping[r["item_id"]] = cur.fetchone()[0]
    return mapping


def load_suppliers(cur, suppliers):
    mapping = {}
    for _, r in suppliers.iterrows():
        cur.execute(
            """INSERT INTO suppliers(code, name, category, lead_time_days, moq,
                                     payment_terms_days, is_active)
               VALUES (%s, %s, %s, %s, %s, %s, TRUE) RETURNING id""",
            (r["supplier_id"], r["supplier_name"], r["category"], int(r["lead_time_days"]),
             int(r["moq"]), int(r["payment_terms_days"])),
        )
        mapping[r["supplier_id"]] = cur.fetchone()[0]
    return mapping


def load_policies_and_levels(cur, snap, loc, prod, sup):
    for _, r in snap.iterrows():
        p, l = prod[r["item_id"]], loc[r["store_id"]]
        cur.execute(
            """INSERT INTO inventory_policies(product_id, location_id, safety_days,
                   target_cover_days, max_stock_days, default_supplier_id)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (p, l, DEFAULT_SAFETY_DAYS, DEFAULT_TARGET_COVER, DEFAULT_MAX_STOCK,
             sup.get(r["supplier_default"])),
        )
        cur.execute(
            """INSERT INTO inventory_levels(product_id, location_id, on_hand, on_order)
               VALUES (%s, %s, %s, %s)""",
            (p, l, int(r["on_hand"]), int(r["on_order"])),
        )


def load_demand(cur, long, loc, prod):
    rows = []
    for r in long.itertuples(index=False):
        rows.append((
            prod[r.item_id], loc[r.store_id], r.date, int(r.sales),
            clean(r.sell_price), bool(r.is_stockout),
            clean(r.event_name_1) is not None,
        ))
    with cur.copy(
        "COPY demand_history (product_id, location_id, sale_date, qty_sold, sell_price, "
        "is_stockout, is_event) FROM STDIN"
    ) as cp:
        for row in rows:
            cp.write_row(row)
    return len(rows)


def load_quotes(cur, quotes, loc, prod, sup):
    n_breaks = 0
    for r in quotes.itertuples(index=False):
        start, end = month_bounds(r.month)
        cur.execute(
            """INSERT INTO supplier_quotes(supplier_id, product_id, location_id,
                   base_unit_price, currency, lead_time_days, moq, valid_from, valid_to)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (sup[r.supplier_id], prod[r.item_id], loc[r.store_id],
             round(float(r.unit_cost), 2), CURRENCY, int(r.lead_time_days), int(r.moq),
             start, end),
        )
        qid = cur.fetchone()[0]
        base = float(r.unit_cost)
        breaks = [(1, round(base, 2))]
        breaks.append((int(r.tier1_min_qty), round(base * (1 - float(r.tier1_discount)), 2)))
        breaks.append((int(r.tier2_min_qty), round(base * (1 - float(r.tier2_discount)), 2)))
        for min_qty, price in breaks:
            cur.execute(
                "INSERT INTO quote_price_breaks(quote_id, min_qty, unit_price) VALUES (%s,%s,%s)",
                (qid, min_qty, price),
            )
            n_breaks += 1
    return n_breaks


def load_evaluations(cur, perf, sup):
    for r in perf.itertuples(index=False):
        on_time = float(r.on_time_rate)
        defect = float(r.defect_rate)
        weights = {"on_time": 0.5, "defect": 0.5, "version": "baseline_v1"}
        score = round(100 * (0.5 * on_time + 0.5 * (1 - defect)), 2)
        cur.execute(
            """INSERT INTO supplier_evaluations(supplier_id, period_start, period_end,
                   on_time_rate, defect_rate, avg_lead_time, weights, total_score)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (sup[r.supplier_id], M5_START, M5_END, round(on_time, 4), round(defect, 4),
             round(float(r.avg_actual_lead_time), 2), Jsonb(weights), score),
        )


def load_purchase_history(cur, po, loc, prod, sup, sys_user):
    """Mỗi đơn mua PoC thành: phiếu đã duyệt + một dòng + nhật ký duyệt + đơn mua."""
    for i, r in enumerate(po.itertuples(index=False), start=1):
        order_date = pd.to_datetime(r.order_date).date()
        lead = int(r.expected_arrival_d) - int(r.order_d)
        expected = order_date + dt.timedelta(days=lead)
        total = round(float(r.order_value), 2)
        code = f"PR-SIM-{i:05d}"
        po_number = f"POS-{i:05d}"
        summary = f"Dữ liệu mô phỏng từ {r.po_id}"

        cur.execute(
            """INSERT INTO purchase_requests(code, status, total_value, currency, summary,
                   created_by_user, submitted_at, decided_at, decided_by, created_at)
               VALUES (%s, 'APPROVED', %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (code, total, CURRENCY, summary, sys_user, order_date, order_date, sys_user,
             order_date),
        )
        req_id = cur.fetchone()[0]

        cur.execute(
            """INSERT INTO purchase_request_lines(request_id, product_id, location_id,
                   supplier_id, option_type, qty, unit_price, needed_by_date,
                   is_recommended, is_selected, reason)
               VALUES (%s, %s, %s, %s, 'MANUAL', %s, %s, %s, FALSE, TRUE, %s)""",
            (req_id, prod[r.item_id], loc[r.store_id], sup[r.supplier_id],
             int(r.qty_ordered), round(float(r.unit_cost), 2), expected,
             f"Chính sách điểm đặt hàng lại (mô phỏng). {r.reason}"),
        )

        cur.execute(
            """INSERT INTO request_decisions(request_id, actor_id, action, snapshot, created_at)
               VALUES (%s, %s, 'APPROVE', %s, %s)""",
            (req_id, sys_user,
             Jsonb({"source": "SIMULATED_POLICY", "note": "Duyệt tự động theo chính sách mô phỏng"}),
             order_date),
        )

        cur.execute(
            """INSERT INTO purchase_orders(po_number, request_id, supplier_id, order_date,
                   expected_date, total_value, status, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'SIMULATED')""",
            (po_number, req_id, sup[r.supplier_id], order_date, expected, total,
             PO_STATUS_MAP.get(r.status, "CREATED")),
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--clean-dir", default="./output")
    ap.add_argument("--proc-dir", default="./procurement_data")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("Thiếu --dsn hoặc biến môi trường DATABASE_URL")

    sel = pd.read_csv(os.path.join(args.clean_dir, "selected_skus.csv"))
    long = pd.read_csv(os.path.join(args.clean_dir, "sales_clean_long.csv"))
    suppliers = pd.read_csv(os.path.join(args.proc_dir, "suppliers.csv"))
    suppliers = suppliers[[c for c in suppliers.columns if not c.endswith("_true")]]
    quotes = pd.read_csv(os.path.join(args.proc_dir, "supplier_quotes.csv"))
    perf = pd.read_csv(os.path.join(args.proc_dir, "supplier_performance.csv"))
    snap = pd.read_csv(os.path.join(args.proc_dir, "inventory_snapshot.csv"))
    po = pd.read_csv(os.path.join(args.proc_dir, "purchase_orders.csv"))

    counts = {}
    with psycopg.connect(args.dsn) as conn:
        with conn.cursor() as cur:
            print("Xóa dữ liệu PoC cũ (giữ nguyên bảng users)...")
            reset(cur)
            sys_user = ensure_system_user(cur)

            loc = load_locations(cur, sel)
            prod = load_products(cur, sel)
            sup = load_suppliers(cur, suppliers)
            load_policies_and_levels(cur, snap, loc, prod, sup)
            counts["locations"], counts["products"], counts["suppliers"] = len(loc), len(prod), len(sup)
            counts["inventory_policies"] = counts["inventory_levels"] = len(snap)

            counts["demand_history"] = load_demand(cur, long, loc, prod)
            counts["supplier_quotes"] = len(quotes)
            counts["quote_price_breaks"] = load_quotes(cur, quotes, loc, prod, sup)
            load_evaluations(cur, perf, sup)
            counts["supplier_evaluations"] = len(perf)

            load_purchase_history(cur, po, loc, prod, sup, sys_user)
            counts["purchase_requests"] = counts["purchase_orders"] = len(po)

        conn.commit()

    print("\nĐã nạp:")
    for k, v in counts.items():
        print(f"  {k:22s} {v:>8,d}")
    print("\nKhông nạp: inventory_daily (schema chưa có bảng lịch sử tồn theo ngày).")


if __name__ == "__main__":
    main()