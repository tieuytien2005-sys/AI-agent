"""
Sinh dữ liệu mô phỏng cho AI Procurement Agent (PoC).

Đầu vào (từ prepare_data.py):
  - sales_clean_long.csv
  - selected_skus.csv

Đầu ra (thư mục OUT_DIR):
  suppliers.csv               danh mục nhà cung cấp và thuộc tính
  supplier_quotes.csv         báo giá theo NCC, sản phẩm, cửa hàng, tháng (kèm chiết khấu)
  inventory_daily.csv         tồn kho hằng ngày mô phỏng (tồn, đang về, bán thiếu)
  purchase_orders.csv         lịch sử đơn mua theo chính sách tồn kho
  supplier_performance.csv    tỷ lệ giao đúng hạn, tỷ lệ lỗi, lead time thực tế theo NCC
  inventory_snapshot.csv      trạng thái tồn kho tại ngày cuối, kèm ROP, tồn an toàn

Giả định quan trọng:
  - Doanh số M5 được coi là nhu cầu. Khi tồn bằng 0, phần nhu cầu không đáp ứng được ghi là bán thiếu.
  - Lịch sử đơn mua do chính sách (ROP + đặt đủ cover) tạo ra, không phải do người quản lý thực hiện.
    Mọi bản ghi có source = SIMULATED_POLICY để tách khỏi dữ liệu thật sau này.
"""

import argparse
import math
import os
import numpy as np
import pandas as pd

SEED = 42

# Ba mẫu NCC cho mỗi nhóm hàng, có đánh đổi rõ ràng giữa giá, tốc độ và độ tin cậy
SUPPLIER_TEMPLATES = [
    dict(tag="FAST",  name="NCC giao nhanh",   lead_time=3,  price_factor=1.08, moq=10,
         on_time_rate=0.95, defect_rate=0.01),
    dict(tag="BAL",   name="NCC cân bằng",     lead_time=7,  price_factor=1.00, moq=50,
         on_time_rate=0.85, defect_rate=0.02),
    dict(tag="CHEAP", name="NCC giá rẻ",       lead_time=14, price_factor=0.92, moq=100,
         on_time_rate=0.70, defect_rate=0.04),
]
CATEGORIES = ["FOODS", "HOUSEHOLD", "HOBBIES"]

COST_RATIO = 0.65           # giá vốn ước tính = 65% giá bán lẻ trung bình
PRICE_DRIFT_SD = 0.03       # biến động giá nhà cung cấp mỗi tháng (độ lệch chuẩn)
DISCOUNT_TIERS = [(100, 0.03), (500, 0.06)]   # (số lượng tối thiểu, tỷ lệ chiết khấu)
SAFETY_DAYS = 7             # tồn an toàn tính bằng số ngày nhu cầu
TARGET_COVER_DAYS = 21      # khi đặt hàng, đặt đủ cover này
LATE_DELAY_DAYS = 7         # số ngày trễ khi NCC giao không đúng hạn
DEMAND_WINDOW = 28          # cửa sổ tính nhu cầu trung bình (ngày)


def build_suppliers(categories, rng):
    rows = []
    for cat in categories:
        for t in SUPPLIER_TEMPLATES:
            jitter = rng.uniform(0.97, 1.03)
            rows.append({
                "supplier_id": f"SUP_{cat[:4]}_{t['tag']}",
                "supplier_name": f"{t['name']} ({cat})",
                "category": cat,
                "lead_time_days": t["lead_time"],
                "price_factor": round(t["price_factor"] * jitter, 4),
                "moq": t["moq"],
                "on_time_rate_true": t["on_time_rate"],   # tham số mô phỏng, không dùng để chấm điểm
                "defect_rate_true": t["defect_rate"],
                "payment_terms_days": int(rng.choice([15, 30, 45])),
            })
    return pd.DataFrame(rows)


def build_quotes(sel, long, suppliers, rng):
    """Báo giá theo NCC, sản phẩm, cửa hàng và tháng. Giá dao động theo chuỗi ngẫu nhiên."""
    long = long.copy()
    long["month"] = pd.to_datetime(long["date"]).dt.to_period("M").astype(str)
    avg_price = (long.dropna(subset=["sell_price"])
                 .groupby(["item_id", "store_id", "month"])["sell_price"].mean()
                 .rename("avg_sell_price").reset_index())
    months = sorted(avg_price["month"].unique())

    rows = []
    for _, s in sel.iterrows():
        sup_cat = suppliers[suppliers["category"] == s["cat_id"]]
        item_prices = avg_price[(avg_price["item_id"] == s["item_id"]) &
                                (avg_price["store_id"] == s["store_id"])].set_index("month")["avg_sell_price"]
        for _, sup in sup_cat.iterrows():
            drift = 1.0
            for m in months:
                drift *= math.exp(rng.normal(0, PRICE_DRIFT_SD))
                base_sell = item_prices.get(m, item_prices.mean())
                if pd.isna(base_sell):
                    continue
                unit_cost = base_sell * COST_RATIO * sup["price_factor"] * drift
                rows.append({
                    "supplier_id": sup["supplier_id"],
                    "store_id": s["store_id"],
                    "item_id": s["item_id"],
                    "month": m,
                    "unit_cost": round(unit_cost, 2),
                    "moq": int(sup["moq"]),
                    "lead_time_days": int(sup["lead_time_days"]),
                    "tier1_min_qty": DISCOUNT_TIERS[0][0], "tier1_discount": DISCOUNT_TIERS[0][1],
                    "tier2_min_qty": DISCOUNT_TIERS[1][0], "tier2_discount": DISCOUNT_TIERS[1][1],
                })
    return pd.DataFrame(rows)


def unit_cost_for(quotes_idx, sup_id, store, item, month, qty):
    q = quotes_idx.get((sup_id, store, item, month))
    if q is None:
        return None
    disc = 0.0
    if qty >= q["tier2_min_qty"]:
        disc = q["tier2_discount"]
    elif qty >= q["tier1_min_qty"]:
        disc = q["tier1_discount"]
    return round(q["unit_cost"] * (1 - disc), 2)


def simulate_series(store, item, sales, dates, d_idx, sup, quotes_idx, rng):
    """Mô phỏng tồn kho một chuỗi theo chính sách ROP, trả về ba bảng: daily, PO, snapshot."""
    lead = int(sup["lead_time_days"])
    moq = int(sup["moq"])
    n = len(sales)

    base = d_idx[0]   # d_idx của ngày đầu tiên, dùng để quy đổi chỉ số vòng lặp sang d_idx

    init_demand = max(sales[:DEMAND_WINDOW].mean(), 0.5)
    on_hand = max(moq, int(round(init_demand * TARGET_COVER_DAYS)))
    pending = []   # (arrival_d, qty_ordered, po_id)
    po_records = []
    daily = []
    po_seq = 0

    for t in range(n):
        # 1) Nhận hàng về đúng ngày
        received = 0
        for arr in [p for p in pending if p[0] == t]:
            _, qty, po_id = arr
            defect = int(round(qty * sup["defect_rate_true"] * rng.uniform(0.5, 1.5)))
            good = max(qty - defect, 0)
            on_hand += good
            received += good
            pending.remove(arr)
            for rec in po_records:
                if rec["po_id"] == po_id:
                    rec["actual_arrival_d"] = base + t
                    rec["qty_received"] = good
                    rec["defect_qty"] = defect
                    rec["status"] = "RECEIVED"

        # 2) Nhu cầu trong ngày; phần vượt tồn là bán thiếu
        demand = int(sales[t])
        sold = min(demand, on_hand)
        lost = demand - sold
        on_hand -= sold

        # 3) Kiểm tra điểm đặt hàng lại
        window = sales[max(0, t - DEMAND_WINDOW + 1): t + 1]
        avg_d = max(window.mean(), 0.1)
        rop = avg_d * (lead + SAFETY_DAYS)
        on_order = sum(p[1] for p in pending)
        position = on_hand + on_order
        ordered = 0
        if position <= rop:
            target = avg_d * (lead + TARGET_COVER_DAYS)
            need = math.ceil(target - position)
            qty = max(moq, need)
            # làm tròn lên bội số của MOQ
            qty = int(math.ceil(qty / moq) * moq)
            unit_cost = unit_cost_for(quotes_idx, sup["supplier_id"], store, item,
                                      dates[t][:7], qty)
            if unit_cost is not None:
                po_seq += 1
                po_id = f"PO_{store}_{item}_{po_seq:04d}"
                on_time = rng.random() < sup["on_time_rate_true"]
                arrival = t + lead + (0 if on_time else LATE_DELAY_DAYS)
                pending.append((arrival, qty, po_id))
                po_records.append({
                    "po_id": po_id,
                    "store_id": store,
                    "item_id": item,
                    "supplier_id": sup["supplier_id"],
                    "order_date": dates[t],
                    "order_d": d_idx[t],
                    "expected_arrival_d": base + t + lead,
                    "actual_arrival_d": None,
                    "qty_ordered": qty,
                    "qty_received": None,
                    "defect_qty": None,
                    "unit_cost": unit_cost,
                    "order_value": round(unit_cost * qty, 2),
                    "reorder_point": round(rop, 1),
                    "on_hand_at_order": on_hand,
                    "reason": "Tồn dự kiến <= điểm đặt hàng lại",
                    "status": "IN_TRANSIT",
                    "source": "SIMULATED_POLICY",
                })
                ordered = qty
                on_order += qty

        daily.append({
            "store_id": store, "item_id": item, "d_idx": d_idx[t], "date": dates[t],
            "demand": demand, "sold": sold, "lost_sales": lost,
            "received_qty": received, "ordered_qty": ordered,
            "on_hand_end": on_hand, "on_order_end": sum(p[1] for p in pending),
            "stockout": int(on_hand == 0),
        })

    # Snapshot tại ngày cuối
    last_window = sales[-DEMAND_WINDOW:]
    avg_last = max(last_window.mean(), 0.1)
    snapshot = {
        "store_id": store, "item_id": item, "as_of_date": dates[-1],
        "on_hand": on_hand,
        "on_order": sum(p[1] for p in pending),
        "avg_daily_demand_28d": round(avg_last, 2),
        "lead_time_days": lead,
        "safety_stock": round(avg_last * SAFETY_DAYS, 1),
        "reorder_point": round(avg_last * (lead + SAFETY_DAYS), 1),
        "days_of_cover": round(on_hand / avg_last, 1),
        "supplier_default": sup["supplier_id"],
    }
    return pd.DataFrame(daily), pd.DataFrame(po_records), snapshot


def supplier_performance(po, suppliers):
    done = po[po["status"] == "RECEIVED"].copy()
    if done.empty:
        return pd.DataFrame()
    done["late"] = done["actual_arrival_d"] > done["expected_arrival_d"]
    done["actual_lead"] = done["actual_arrival_d"] - done["order_d"]
    done["defect_rate"] = done["defect_qty"] / done["qty_ordered"]
    perf = (done.groupby("supplier_id")
            .agg(orders_received=("po_id", "count"),
                 on_time_rate=("late", lambda x: 1 - x.mean()),
                 avg_actual_lead_time=("actual_lead", "mean"),
                 defect_rate=("defect_rate", "mean"))
            .reset_index())
    perf[["on_time_rate", "avg_actual_lead_time", "defect_rate"]] = \
        perf[["on_time_rate", "avg_actual_lead_time", "defect_rate"]].round(3)
    return perf.merge(suppliers[["supplier_id", "supplier_name", "lead_time_days"]], on="supplier_id")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", default="./output")
    ap.add_argument("--out-dir", default="./procurement_data")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    rng = np.random.default_rng(SEED)

    long = pd.read_csv(os.path.join(args.input_dir, "sales_clean_long.csv"))
    sel = pd.read_csv(os.path.join(args.input_dir, "selected_skus.csv"))

    suppliers = build_suppliers(CATEGORIES, rng)
    quotes = build_quotes(sel, long, suppliers, rng)
    quotes_idx = {(r.supplier_id, r.store_id, r.item_id, r.month): r._asdict()
                  for r in quotes.itertuples(index=False)}

    daily_all, po_all, snap_all = [], [], []
    tags = [t["tag"] for t in SUPPLIER_TEMPLATES]
    rank_in_cat = {}
    for _, s in sel.iterrows():
        # Luân phiên ba loại NCC trong mỗi nhóm hàng để có lịch sử giao hàng cho cả ba
        k = rank_in_cat.get(s["cat_id"], 0)
        rank_in_cat[s["cat_id"]] = k + 1
        series = long[(long["store_id"] == s["store_id"]) & (long["item_id"] == s["item_id"])]
        series = series.sort_values("d_idx")
        sales = series["sales"].to_numpy(dtype=float)
        dates = series["date"].tolist()
        d_idx = series["d_idx"].tolist()
        sup_id = f"SUP_{s['cat_id'][:4]}_{tags[k % len(tags)]}"
        sup = suppliers[suppliers["supplier_id"] == sup_id].iloc[0]
        daily, po, snap = simulate_series(s["store_id"], s["item_id"], sales, dates, d_idx,
                                          sup, quotes_idx, rng)
        daily_all.append(daily)
        po_all.append(po)
        snap_all.append(snap)

    daily_df = pd.concat(daily_all, ignore_index=True)
    po_df = pd.concat(po_all, ignore_index=True)
    snap_df = pd.DataFrame(snap_all)
    perf_df = supplier_performance(po_df, suppliers)

    suppliers.to_csv(os.path.join(args.out_dir, "suppliers.csv"), index=False)
    quotes.to_csv(os.path.join(args.out_dir, "supplier_quotes.csv"), index=False)
    daily_df.to_csv(os.path.join(args.out_dir, "inventory_daily.csv"), index=False)
    po_df.to_csv(os.path.join(args.out_dir, "purchase_orders.csv"), index=False)
    perf_df.to_csv(os.path.join(args.out_dir, "supplier_performance.csv"), index=False)
    snap_df.to_csv(os.path.join(args.out_dir, "inventory_snapshot.csv"), index=False)

    total_demand = daily_df["demand"].sum()
    total_lost = daily_df["lost_sales"].sum()
    print(f"Số NCC: {len(suppliers)}")
    print(f"Số dòng báo giá: {len(quotes)}")
    print(f"Số đơn mua: {len(po_df)}")
    print(f"Ngày có tồn bằng 0: {daily_df['stockout'].mean():.2%}")
    print(f"Tỷ lệ bán thiếu trên nhu cầu: {total_lost / total_demand:.2%}")
    print("\nHiệu suất NCC mô phỏng:")
    print(perf_df.to_string(index=False))
    print(f"\nĐã lưu vào {args.out_dir}")


if __name__ == "__main__":
    main()