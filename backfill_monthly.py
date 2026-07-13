"""
[로컬 실행용] 월별 누적 데이터 생성 스크립트.

카페24 주문을 '월 단위'로 가져와 data/monthly/YYYY-MM.json 으로 저장한다.
각 월 파일에는 그 달의 일자별 × 전체 옵션·카테고리·오딧 인치색상 데이터가 모두 들어간다.

사용법 (맥북 터미널, 프로젝트 폴더에서):
    python3 backfill_monthly.py 2026-01            # 특정 한 달
    python3 backfill_monthly.py 2026-01 2026-06    # 여러 달 (범위)
    python3 backfill_monthly.py                     # 인자 없으면 올해 1월~이번 달 전체

이 스크립트는 카페24가 과거 데이터를 어디까지 내어주는지 확인하는 용도로도 쓴다.
한 달씩 돌려보며 성공/실패를 확인하면 된다.
실행 후 생성된 data/monthly/*.json 을 git 으로 커밋·push 하면 끝.
"""
import sys
import json
import re
import os
from calendar import monthrange
from collections import Counter
from datetime import date, datetime

from cafe24_client import Cafe24Client
from classify import classify
from build_data import to_amount, order_day, option_label, now_kst

OUT_DIR = "data/monthly"
_ODIT_COLORS = ["화이트", "실버", "다크그레이", "블랙", "솔티블루",
                "펄스레드", "아이시핑크", "웻그린"]
_EXCLUDE = ["테스트", "POP-UP", "PRE-ORDER", "몬딱", "쿼디"]  # 임직원은 색상 집계에 포함


def _odit_key(pname, opt):
    text = (pname or "") + " " + (opt or "")
    if "오딧" not in text or any(x in text for x in _EXCLUDE):
        return None
    # 캐리어 본품만 색상 집계에 포함 (커버·파우치 등 악세사리 제외)
    if classify(pname or "", opt or "")[0] != "캐리어":
        return None
    # '커버'가 들어간 옵션/상품은 캐리어 색상 아님 (풀커버·패커블커버 사은품 등)
    if "커버" in (opt or "") or "커버" in (pname or ""):
        return None
    # 대괄호(배송/날짜)·소괄호 제거
    clean = re.sub(r"\[.*?\]", "", opt or "")
    clean = re.sub(r"\(.*?\)", "", clean)
    clean = clean.replace("아이시 핑크", "아이시핑크")
    # '/'로 여러 상품이 붙은 세트: 첫 조각에 인치+색상이 둘 다 있으면(캐리어 완결)
    # 그 조각만 사용해 뒤 상품(파우치 등) 색상 오염을 막는다. 아니면 전체 사용.
    segs = [s.strip() for s in clean.split("/") if s.strip()]
    first = segs[0] if segs else clean
    _has_inch = re.search(r"(\d+)\s*인치", first)
    _has_color = any(c in first for c in _ODIT_COLORS)
    o = first if (_has_inch and _has_color) else clean
    p = pname or ""
    opt_inch = re.search(r"(\d+)\s*인치", o)
    if "플랩" in o:
        flap = True
    elif "플랩" in p and not opt_inch and "오딧" not in o:
        flap = True
    else:
        flap = False
    if flap:
        group = "20인치 플랩"
    else:
        m = opt_inch or re.search(r"(\d+)\s*인치", p)
        if not m:
            return None
        group = f"{m.group(1)}인치"
    # 색상: '/' 또는 공백 구분 모두 대응 (o 전체에서 색상 문자열 탐색)
    color = next((c for c in _ODIT_COLORS if c in o), None)
    return f"{group}·{color}" if color else None


def aggregate_month(orders):
    """한 달치 주문 -> 일자별/카테고리/옵션/오딧 집계 (build_product 와 동일 기준)."""
    cat_amount = Counter()
    cat_qty = Counter()
    model_amount = Counter()
    product_sales = {}
    option_daily = {}          # "상품 · 옵션" -> {date: qty}
    cat_daily = {}             # date -> {대분류: 매출}
    odit_daily = {}            # "인치그룹·색상" -> {date: {q, a}}
    total_amount = 0.0
    total_qty = 0
    order_ids = set()

    # 번들(함께구매) 일자별 집계
    from itertools import combinations
    bundle_daily = {}          # date -> {stat, pairs, carrier_with}
    bundle_cat = {}            # date -> {single, set, combo, etc} (캐리어 수량 기준)

    for o in orders:
        if o.get("canceled") == "T":
            continue
        d = order_day(o)
        order_ids.add(o.get("order_id"))

        # --- 주문 단위 번들 집계 ---
        if d:
            prod_names, order_amt, has_carrier = [], 0.0, False
            for it in (o.get("items") or []):
                net0 = int(to_amount(it.get("quantity"))) - int(to_amount(it.get("claim_quantity")))
                if net0 <= 0:
                    continue
                pn = it.get("product_name", "(이름없음)")
                opt0 = option_label(it)
                # 오딧 캐리어면 옵션(인치·색상)으로 세분, 나머지는 상품명 그대로
                ok0 = _odit_key(pn, opt0)
                disp = f"오딧 {ok0.replace('·', ' ')}" if ok0 else pn
                if disp not in prod_names:
                    prod_names.append(disp)
                order_amt += (to_amount(it.get("product_price"))
                              + to_amount(it.get("option_price"))) * net0
                if classify(pn, opt0)[0] == "캐리어":
                    has_carrier = True
            if prod_names:
                bd = bundle_daily.setdefault(d, {
                    "total_orders": 0, "bundle_orders": 0,
                    "single_amount": 0.0, "bundle_amount": 0.0,
                    "pairs": {}, "carrier_with": {},
                    "set_inch": {}, "set_color": {}})
                bd["total_orders"] += 1
                if len(prod_names) >= 2:
                    bd["bundle_orders"] += 1
                    bd["bundle_amount"] += order_amt
                    for a, b in combinations(sorted(prod_names), 2):
                        pk = f"{a}\t{b}"
                        cell = bd["pairs"].setdefault(pk, {"c": 0, "a": 0.0})
                        cell["c"] += 1
                        cell["a"] += order_amt
                    if has_carrier:
                        for disp in prod_names:
                            cw = bd["carrier_with"].setdefault(disp, {"c": 0, "a": 0.0})
                            cw["c"] += 1
                            cw["a"] += order_amt
                    # 캐리어 옵션이 2개 이상이면 인치/컬러 조합 집계 (순수 캐리어 세트)
                    carrier_opts = [n for n in prod_names if n.startswith("오딧 ")
                                    and any(g in n for g in ["20인치", "24인치", "26인치", "29인치"])]
                    if len(carrier_opts) >= 2:
                        inches, colors = [], []
                        for n in carrier_opts:
                            body = n[len("오딧 "):]  # "20인치 플랩 실버" or "24인치 블랙"
                            parts = body.rsplit(" ", 1)
                            if len(parts) == 2:
                                inches.append(parts[0]); colors.append(parts[1])
                        if len(inches) >= 2:
                            ik = " + ".join(sorted(inches))
                            ck = " + ".join(sorted(colors))
                            si = bd["set_inch"].setdefault(ik, {"c": 0, "a": 0.0})
                            si["c"] += 1; si["a"] += order_amt
                            sc = bd["set_color"].setdefault(ck, {"c": 0, "a": 0.0})
                            sc["c"] += 1; sc["a"] += order_amt
                else:
                    bd["single_amount"] += order_amt

            # --- 신규: 캐리어 수량 기준 주문 분류 (단품/세트/기타 + 합구매 악세) ---
            carrier_qty = 0
            carrier_amt = 0.0
            acc_amt = 0.0
            for it in (o.get("items") or []):
                net_i = int(to_amount(it.get("quantity"))) - int(to_amount(it.get("claim_quantity")))
                if net_i <= 0:
                    continue
                pn_i = it.get("product_name", "")
                opt_i = option_label(it)
                amt_i = (to_amount(it.get("product_price"))
                         + to_amount(it.get("option_price"))) * net_i
                if classify(pn_i, opt_i)[0] == "캐리어":
                    carrier_qty += net_i
                    carrier_amt += amt_i
                else:
                    acc_amt += amt_i
            full_amt = carrier_amt + acc_amt
            if prod_names:
                bc = bundle_cat.setdefault(d, {
                    "single": {"orders": 0, "full": 0.0, "carrier": 0.0},
                    "set": {"orders": 0, "full": 0.0, "carrier": 0.0},
                    "combo": {"orders": 0, "acc": 0.0},
                    "etc": {"orders": 0, "amt": 0.0}})
                if carrier_qty == 0:
                    bc["etc"]["orders"] += 1
                    bc["etc"]["amt"] += full_amt
                elif carrier_qty == 1:
                    bc["single"]["orders"] += 1
                    bc["single"]["full"] += full_amt
                    bc["single"]["carrier"] += carrier_amt
                    if acc_amt > 0:
                        bc["combo"]["orders"] += 1
                        bc["combo"]["acc"] += acc_amt
                else:  # 캐리어 2개 이상 = 세트
                    bc["set"]["orders"] += 1
                    bc["set"]["full"] += full_amt
                    bc["set"]["carrier"] += carrier_amt
                    if acc_amt > 0:
                        bc["combo"]["orders"] += 1
                        bc["combo"]["acc"] += acc_amt

        for it in (o.get("items") or []):
            net = int(to_amount(it.get("quantity"))) - int(to_amount(it.get("claim_quantity")))
            if net <= 0:
                continue
            amt = (to_amount(it.get("product_price")) + to_amount(it.get("option_price"))) * net
            opt = option_label(it)
            대, 중, 태그, _ = classify(it.get("product_name", ""), opt)
            cat_amount[대] += amt
            cat_qty[대] += net
            total_amount += amt
            total_qty += net
            if 대 == "캐리어":
                model_amount[중] += amt
            pname = it.get("product_name", "(이름없음)")
            ps = product_sales.setdefault(pname, {"qty": 0, "amount": 0.0})
            ps["qty"] += net
            ps["amount"] += amt
            if d:
                label = f"{pname} · {opt}"
                option_daily.setdefault(label, {}).setdefault(d, 0)
                option_daily[label][d] += net
                cat_daily.setdefault(d, {}).setdefault(대, {"a": 0.0, "q": 0})
                cat_daily[d][대]["a"] += amt
                cat_daily[d][대]["q"] += net
                ok = _odit_key(pname, opt)
                if ok:
                    e = odit_daily.setdefault(ok, {}).setdefault(d, {"q": 0, "a": 0.0})
                    e["q"] += net
                    e["a"] += amt

    ranking = sorted(
        ({"name": k, "qty": v["qty"], "amount": round(v["amount"])}
         for k, v in product_sales.items()),
        key=lambda x: x["amount"], reverse=True)

    return {
        "summary": {
            "order_count": len(order_ids),
            "total_amount": round(total_amount),
            "total_qty": total_qty,
        },
        "category": {
            "by_amount": {k: round(v) for k, v in cat_amount.items()},
            "by_qty": dict(cat_qty),
        },
        "carrier_models": {k: round(v) for k, v in model_amount.most_common()},
        "ranking": ranking,
        "option_daily": option_daily,
        "cat_daily": {d: {k: {"a": round(v["a"]), "q": v["q"]} for k, v in cats.items()}
                      for d, cats in sorted(cat_daily.items())},
        "odit_daily": {k: {d: {"q": e["q"], "a": round(e["a"])} for d, e in m.items()}
                       for k, m in odit_daily.items()},
        "bundle_daily": {
            d: {
                "total_orders": v["total_orders"],
                "bundle_orders": v["bundle_orders"],
                "single_amount": round(v["single_amount"]),
                "bundle_amount": round(v["bundle_amount"]),
                "pairs": {pk: {"c": c["c"], "a": round(c["a"])} for pk, c in v["pairs"].items()},
                "carrier_with": {n: {"c": c["c"], "a": round(c["a"])}
                                 for n, c in v["carrier_with"].items()},
                "set_inch": {k: {"c": c["c"], "a": round(c["a"])}
                             for k, c in v.get("set_inch", {}).items()},
                "set_color": {k: {"c": c["c"], "a": round(c["a"])}
                              for k, c in v.get("set_color", {}).items()},
            } for d, v in sorted(bundle_daily.items())
        },
        "bundle_cat_daily": {
            d: {
                "single": {"orders": v["single"]["orders"],
                           "full": round(v["single"]["full"]),
                           "carrier": round(v["single"]["carrier"])},
                "set": {"orders": v["set"]["orders"],
                        "full": round(v["set"]["full"]),
                        "carrier": round(v["set"]["carrier"])},
                "combo": {"orders": v["combo"]["orders"],
                          "acc": round(v["combo"]["acc"])},
                "etc": {"orders": v["etc"]["orders"],
                        "amt": round(v["etc"]["amt"])},
            } for d, v in sorted(bundle_cat.items())
        },
    }


def month_range(ym):
    """'2026-01' -> (date(2026,1,1), date(2026,1,31))"""
    y, m = map(int, ym.split("-"))
    last = monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last)


def iter_months(start_ym, end_ym):
    sy, sm = map(int, start_ym.split("-"))
    ey, em = map(int, end_ym.split("-"))
    y, m = sy, sm
    while (y, m) <= (ey, em):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m > 12:
            m = 1
            y += 1


def build_page_daily(orders):
    """248/270/184 페이지별 일자 집계 (주문 API · order_status 기준).
    {날짜: {페이지: {orders:주문수, q:순구매상품수, a:매출, cancel:취소수량}}}
    order_status 앞글자: N=정상, C=취소, R=반품, E=교환.
    - 정상(N)·교환(E): 판매로 집계 (매출·수량·주문수)
    - 취소(C)·반품(R): 취소 수량으로 집계 (매출·주문수엔 미포함)
    객단가는 화면에서 a / orders 로 계산.
    """
    PAGES = {"248", "270", "184"}
    tmp = {}  # (date, pno) -> {order_ids:set, q, a, cancel}
    for o in orders:
        d = order_day(o)
        oid = o.get("order_id")
        for it in (o.get("items") or []):
            pno = str(it.get("product_no") or "")
            if pno not in PAGES:
                continue
            qty = int(to_amount(it.get("quantity")))
            status = str(it.get("order_status") or "")
            head = status[:1].upper()
            price = to_amount(it.get("product_price")) + to_amount(it.get("option_price"))
            cell = tmp.setdefault((d, pno), {"order_ids": set(), "q": 0, "a": 0.0, "cancel": 0})
            if head in ("C", "R"):          # 취소·반품
                cell["cancel"] += qty
            else:                           # 정상(N)·교환(E) = 판매
                if qty > 0:
                    cell["order_ids"].add(oid)
                    cell["q"] += qty
                    cell["a"] += price * qty
    page_daily = {}
    for (d, pno), v in tmp.items():
        page_daily.setdefault(d, {})[pno] = {
            "orders": len(v["order_ids"]),
            "q": v["q"],
            "a": round(v["a"]),
            "cancel": v["cancel"],
        }
    return page_daily


def backfill_one(client, ym):
    first, last = month_range(ym)
    today = date.today()
    # 미래 달의 끝날은 오늘까지만
    if last > today:
        last = today
    if first > today:
        print(f"  [{ym}] 미래 달이라 건너뜀")
        return
    print(f"  [{ym}] {first} ~ {last} 주문 수집 중...", flush=True)
    try:
        orders = client.get_orders(str(first), str(last))
    except Exception as e:
        print(f"  [{ym}] ❌ 수집 실패: {e}")
        print(f"        → 카페24가 이 기간을 안 내어줄 수 있어요. 여기까지가 한계일 수 있습니다.")
        return False
    data = aggregate_month(orders)
    data["month"] = ym
    data["period"] = {"start": str(first), "end": str(last)}
    data["generated_at"] = now_kst()
    data["page_daily"] = build_page_daily(orders)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{ym}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    s = data["summary"]
    days = len(data["cat_daily"])
    print(f"  [{ym}] ✅ 저장 완료 → {path}  "
          f"(주문 {s['order_count']}건 · 매출 {s['total_amount']:,} · 수량 {s['total_qty']} · {days}일치)")
    return True


def main():
    args = sys.argv[1:]
    today = date.today()
    if len(args) == 0:
        start_ym = f"{today.year}-01"
        end_ym = f"{today.year}-{today.month:02d}"
    elif len(args) == 1:
        start_ym = end_ym = args[0]
    else:
        start_ym, end_ym = args[0], args[1]

    print(f"월별 backfill: {start_ym} ~ {end_ym}")
    client = Cafe24Client()
    ok_count = 0
    for ym in iter_months(start_ym, end_ym):
        r = backfill_one(client, ym)
        if r is False:   # 명시적 실패(수집 실패)면 중단
            print("수집 실패로 중단합니다. 받아진 달까지는 저장됐어요.")
            break
        if r:
            ok_count += 1
    print(f"\n완료: {ok_count}개 월 저장됨. data/monthly/ 를 git 커밋·push 하세요.")


if __name__ == "__main__":
    main()
