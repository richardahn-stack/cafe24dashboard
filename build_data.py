"""대시보드용 JSON 생성 스크립트.

GitHub Actions가 매일 08시에 이 스크립트를 실행해
data/ 폴더에 sales.json / product.json / inventory.json 을 만들고,
HTML 대시보드(GitHub Pages)가 그 JSON을 읽어 화면을 그립니다.

로컬 테스트:  python3 build_data.py
"""
import json
import os
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from cafe24_client import Cafe24Client
from classify import classify

KST = timezone(timedelta(hours=9))


def now_kst():
    """한국 시간(KST) 기준 현재 시각 문자열."""
    return datetime.now(KST).isoformat(timespec="seconds")

OUT_DIR = "data"
PERIOD_DAYS = 90  # 분석 기간 (일). 직전 동일 기간과 비교. 날짜 필터 비교용으로 넉넉히 확보.


# ---------- 공통 헬퍼 ----------
def to_amount(val):
    if isinstance(val, bool):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val.replace(",", ""))
        except ValueError:
            return 0.0
    if isinstance(val, dict):
        for k in ("payment_amount", "order_amount", "order_price_amount",
                  "actual_payment_amount", "amount"):
            if k in val:
                return to_amount(val[k])
    return 0.0


def order_amount(o):
    return to_amount(o.get("payment_amount")) or to_amount(o.get("actual_order_amount"))


def order_day(o):
    return (o.get("order_date") or "")[:10]


def option_label(it):
    opts = it.get("options")
    if isinstance(opts, list) and opts:
        texts = []
        for op in opts:
            ov = op.get("option_value") if isinstance(op, dict) else None
            if isinstance(ov, dict) and ov.get("option_text"):
                texts.append(ov["option_text"])
            elif isinstance(op, dict) and isinstance(op.get("value"), str):
                texts.append(op["value"])
        if texts:
            return " / ".join(texts)
    raw = it.get("option_value") or ""
    if "=" in raw:
        return raw.split("=")[-1].strip()
    return raw.strip() or "(옵션없음)"


def _order_date_obj(o):
    ds = order_day(o)
    try:
        return datetime.strptime(ds, "%Y-%m-%d").date()
    except ValueError:
        return None


# ---------- 매출 집계 ----------
def summarize_orders(orders):
    """결제완료·미취소 기준 매출/전환수/환불 요약 + 신규/재구매 분리."""
    total_paid = 0.0
    refund = 0.0
    conv_count = 0
    conv_sales = 0.0
    new_sales = 0.0
    repeat_sales = 0.0
    daily = {}  # date -> {"new":.., "repeat":..}
    for o in orders:
        amt = order_amount(o)
        paid = o.get("paid") == "T"
        canceled = o.get("canceled") == "T"
        if paid:
            total_paid += amt
        if canceled:
            refund += amt
            continue
        if not paid:
            continue
        conv_count += 1
        conv_sales += amt
        is_new = o.get("first_order") == "T"
        if is_new:
            new_sales += amt
        else:
            repeat_sales += amt
        d = order_day(o)
        if d:
            slot = daily.setdefault(d, {"new": 0.0, "repeat": 0.0})
            slot["new" if is_new else "repeat"] += amt
    return {
        "total_paid": total_paid, "refund": refund, "conv_count": conv_count,
        "conv_sales": conv_sales, "new_sales": new_sales,
        "repeat_sales": repeat_sales, "daily": daily,
    }


def pct_delta(cur, prev):
    return round((cur / prev - 1) * 100, 1) if prev else None


def build_sales(client):
    today = date.today()
    cur_start = today - timedelta(days=PERIOD_DAYS - 1)
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=PERIOD_DAYS - 1)

    cur = summarize_orders(client.get_orders(str(cur_start), str(today)))
    prev = summarize_orders(client.get_orders(str(prev_start), str(prev_end)))

    aov = cur["conv_sales"] / cur["conv_count"] if cur["conv_count"] else 0
    refund_rate = (cur["refund"] / cur["total_paid"] * 100) if cur["total_paid"] else 0

    daily_list = [{"date": d, "new": round(v["new"]), "repeat": round(v["repeat"])}
                  for d, v in sorted(cur["daily"].items())]

    return {
        "generated_at": now_kst(),
        "period": {"start": str(cur_start), "end": str(today), "days": PERIOD_DAYS},
        "kpi": {
            "total_sales": round(cur["conv_sales"]),
            "conv_count": cur["conv_count"],
            "aov": round(aov),
            "refund_rate": round(refund_rate, 1),
        },
        "new_vs_repeat": {
            "new_sales": round(cur["new_sales"]),
            "repeat_sales": round(cur["repeat_sales"]),
            "new_delta_pct": pct_delta(cur["new_sales"], prev["new_sales"]),
            "repeat_delta_pct": pct_delta(cur["repeat_sales"], prev["repeat_sales"]),
            "prev_period": {"start": str(prev_start), "end": str(prev_end)},
        },
        "daily": daily_list,
    }


def write_json(name, data):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {path}")


# ---------- 상품 집계 ----------
def build_product(client):
    """주문 기준 상품/옵션 판매 + 카테고리 분류 (조회·전환은 CSV라 제외)."""
    today = date.today()
    start = today - timedelta(days=PERIOD_DAYS - 1)
    orders = client.get_orders(str(start), str(today))

    cat_amount = Counter()      # 대분류 -> 매출
    cat_qty = Counter()         # 대분류 -> 수량
    model_amount = Counter()    # 캐리어 모델 -> 매출
    product_sales = {}          # 상품명 -> {qty, amount, orders}
    option_daily = {}           # "상품·옵션" -> {date: qty}
    cat_daily = {}              # 날짜 -> {대분류: 매출}
    odit_daily = {}             # "인치그룹·색상" -> {date: qty}  (오딧 전 페이지 합산)

    import re as _re
    _ODIT_COLORS = ["화이트", "실버", "다크그레이", "블랙", "솔티블루",
                    "펄스레드", "아이시핑크", "웻그린"]
    _EXCLUDE = ["임직원", "테스트", "POP-UP", "PRE-ORDER", "몬딱", "쿼디"]

    def _odit_key(pname, opt):
        text = (pname or "") + " " + (opt or "")
        if "오딧" not in text or any(x in text for x in _EXCLUDE):
            return None
        m = _re.search(r"(\d+)\s*인치", opt or "")
        if not m:
            return None
        flap = "플랩" in (opt or "")
        group = f"{m.group(1)}인치 플랩" if flap else f"{m.group(1)}인치"
        norm = _re.sub(r"\(.*?\)", "", opt or "").replace("아이시 핑크", "아이시핑크")
        color = next((c for c in _ODIT_COLORS if c in norm), None)
        return f"{group}·{color}" if color else None

    for o in orders:
        if o.get("canceled") == "T":
            continue
        d = order_day(o)
        for it in (o.get("items") or []):
            net = int(to_amount(it.get("quantity"))) - int(to_amount(it.get("claim_quantity")))
            if net <= 0:
                continue
            amt = (to_amount(it.get("product_price")) + to_amount(it.get("option_price"))) * net
            opt = option_label(it)
            대, 중, 태그, _ = classify(it.get("product_name", ""), opt)
            cat_amount[대] += amt
            cat_qty[대] += net
            if 대 == "캐리어":
                model_amount[중] += amt
            pname = it.get("product_name", "(이름없음)")
            ps = product_sales.setdefault(pname, {"qty": 0, "amount": 0.0})
            ps["qty"] += net
            ps["amount"] += amt
            label = f"{pname} · {opt}"
            if d:
                option_daily.setdefault(label, {}).setdefault(d, 0)
                option_daily[label][d] += net
                cat_daily.setdefault(d, {}).setdefault(대, 0.0)
                cat_daily[d][대] += amt
                ok = _odit_key(pname, opt)
                if ok:
                    e = odit_daily.setdefault(ok, {}).setdefault(d, {"q": 0, "a": 0.0})
                    e["q"] += net
                    e["a"] += amt

    ranking = sorted(
        ({"name": k, "qty": v["qty"], "amount": round(v["amount"])}
         for k, v in product_sales.items()),
        key=lambda x: x["amount"], reverse=True)[:20]

    return {
        "generated_at": now_kst(),
        "period": {"start": str(start), "end": str(today), "days": PERIOD_DAYS},
        "category": {
            "by_amount": {k: round(v) for k, v in cat_amount.items()},
            "by_qty": dict(cat_qty),
        },
        "carrier_models": {k: round(v) for k, v in model_amount.most_common()},
        "ranking": ranking,
        "option_daily": option_daily,
        "cat_daily": {d: {k: round(v) for k, v in cats.items()}
                      for d, cats in sorted(cat_daily.items())},
        "odit_daily": {k: {d: {"q": e["q"], "a": round(e["a"])} for d, e in m.items()}
                       for k, m in odit_daily.items()},
    }


# ---------- 재고 집계 ----------
def build_inventory(client):
    """전체 품목 재고 + 판매속도(7/30/90일) + 품절예측/부진."""
    today = date.today()
    orders90 = client.get_orders(str(today - timedelta(days=89)), str(today))

    def window_qty(days):
        c = Counter()
        d_from = today - timedelta(days=days - 1)
        for o in orders90:
            if o.get("canceled") == "T":
                continue
            od = _order_date_obj(o)
            if od is None or od < d_from:
                continue
            for it in (o.get("items") or []):
                vc = it.get("variant_code")
                if not vc:
                    continue
                net = int(to_amount(it.get("quantity"))) - int(to_amount(it.get("claim_quantity")))
                if net > 0:
                    c[vc] += net
        return c
    s7, s30, s90 = window_qty(7), window_qty(30), window_qty(90)

    def day_qty(target):
        c = Counter()
        for o in orders90:
            if o.get("canceled") == "T":
                continue
            if _order_date_obj(o) != target:
                continue
            for it in (o.get("items") or []):
                vc = it.get("variant_code")
                if not vc:
                    continue
                net = int(to_amount(it.get("quantity"))) - int(to_amount(it.get("claim_quantity")))
                if net > 0:
                    c[vc] += net
        return c
    s1 = day_qty(today - timedelta(days=1))  # 어제 하루 판매량

    rows = []
    skipped = []
    products = client.get_all_products()
    for p in products:
        pno = p.get("product_no")
        pname = p.get("product_name", "")
        try:
            variants = client.get_variants(pno)
        except Exception as e:
            skipped.append((pno, str(e)[:120]))
            continue
        time.sleep(0.4)  # 카페24 호출 속도 제한 회피
        for v in variants:
            vc = v.get("variant_code")
            opts = v.get("options")
            optval = opts[0].get("value") if isinstance(opts, list) and opts else ""
            stock = int(to_amount(v.get("quantity")))
            d1 = s1.get(vc, 0)
            v7 = round(s7.get(vc, 0) / 7, 2)
            v30 = round(s30.get(vc, 0) / 30, 2)
            v90 = round(s90.get(vc, 0) / 90, 2)

            def sellout(rate):
                return round(stock / rate) if rate > 0 else None

            rows.append({
                "variant_code": vc, "product_no": pno,
                "product": pname, "option": optval,
                "stock": stock, "safety": int(to_amount(v.get("safety_inventory"))),
                "daily_1": d1, "daily_7": v7, "daily_30": v30, "daily_90": v90,
                "sellout_1": sellout(d1), "sellout_7": sellout(v7),
                "sellout_30": sellout(v30), "sellout_90": sellout(v90),
                "sellout_days": sellout(v30),  # 기존 호환
                "selling": v.get("selling") == "T",
            })

    if skipped:
        print(f"  ⚠ 재고 조회 실패로 건너뛴 상품 {len(skipped)}개: "
              + ", ".join(str(s[0]) for s in skipped[:20]))

    return {
        "generated_at": now_kst(),
        "summary": {
            "total": len(rows),
            "products_ok": len(products) - len(skipped),
            "products_skipped": len(skipped),
            "out_of_stock": sum(1 for r in rows if r["stock"] == 0),
            "soon": sum(1 for r in rows if r["stock"] > 0 and r["sellout_days"] is not None
                        and r["sellout_days"] <= 90),
            "stagnant": sum(1 for r in rows if r["stock"] > 0 and r["daily_30"] == 0),
        },
        "items": rows,
    }


def build_current_month(client):
    """이번 달 월별 파일(data/monthly/YYYY-MM.json)을 다시 받아 덮어쓴다."""
    import os
    from datetime import date
    # backfill_monthly의 집계·저장 로직 재사용
    from backfill_monthly import backfill_one
    today = date.today()
    ym = f"{today.year:04d}-{today.month:02d}"
    print(f"이번 달 월별 데이터 갱신 중... ({ym})")
    try:
        backfill_one(client, ym)
    except Exception as e:
        print(f"  이번 달 월별 갱신 실패(무시하고 계속): {e}")


def main():
    client = Cafe24Client()
    print("매출 데이터 생성 중...")
    write_json("sales.json", build_sales(client))
    print("상품 데이터 생성 중...")
    write_json("product.json", build_product(client))
    print("재고 데이터 생성 중...")
    write_json("inventory.json", build_inventory(client))
    # 이번 달 월별 누적 파일도 최신화 (상품/매출 대시보드가 data/monthly 를 읽음)
    build_current_month(client)
    print("완료.")


if __name__ == "__main__":
    main()
