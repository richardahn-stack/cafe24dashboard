"""카페24 자사몰 그로스 대시보드 (3개 탭: 매출 / 상품 / 재고)."""
import json
import os
from collections import Counter
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from cafe24_client import Cafe24Client
from classify import classify

st.set_page_config(page_title="자사몰 그로스 대시보드", layout="wide")


# ====================== 공통 헬퍼 ======================
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


def won(x):
    return f"₩{x:,.0f}"


def won_short(n):
    if n >= 1e8:
        return f"₩{n/1e8:.1f}억"
    if n >= 1e4:
        return f"₩{round(n/1e4):,}만"
    return f"₩{round(n):,}"


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


def half_change(series):
    n = len(series)
    if n < 2:
        return 0.0, 0.0, 0.0
    half = n // 2
    first = series.iloc[:half].mean()
    last = series.iloc[half:].mean()
    return first, last, ((last / first - 1) * 100 if first else 0.0)


def _order_date(o):
    ds = (o.get("order_date") or "")[:10]
    try:
        return datetime.strptime(ds, "%Y-%m-%d").date()
    except ValueError:
        return None


def variant_sales_in_range(orders, d_from, d_to):
    c = Counter()
    for o in orders:
        if o.get("canceled") == "T":
            continue
        d = _order_date(o)
        if d is None or d < d_from or d > d_to:
            continue
        for it in (o.get("items") or []):
            vc = it.get("variant_code")
            if not vc:
                continue
            net = int(to_amount(it.get("quantity"))) - int(to_amount(it.get("claim_quantity")))
            if net > 0:
                c[vc] += net
    return c


def model_sales_in_range(orders, d_from, d_to):
    c = Counter()
    for o in orders:
        if o.get("canceled") == "T":
            continue
        d = _order_date(o)
        if d is None or d < d_from or d > d_to:
            continue
        for it in (o.get("items") or []):
            net = int(to_amount(it.get("quantity"))) - int(to_amount(it.get("claim_quantity")))
            if net <= 0:
                continue
            대, 중, _, _ = classify(it.get("product_name", ""), option_label(it))
            c[중 if 대 == "캐리어" else f"악세사리:{중}"] += net
    return c


def build_order_df(orders):
    rows = []
    for o in orders:
        amount = to_amount(o.get("payment_amount")) or to_amount(o.get("actual_order_amount"))
        rows.append({
            "날짜": (o.get("order_date") or "")[:10],
            "결제금액": amount,
            "신규고객": o.get("first_order") == "T",
            "결제완료": o.get("paid") == "T",
            "취소": o.get("canceled") == "T",
        })
    df = pd.DataFrame(rows)
    df = df[df["날짜"] != ""]
    df["날짜"] = pd.to_datetime(df["날짜"])
    return df


# ---- 메모 영속화 ----
def load_memos():
    if os.path.exists("memos.json"):
        try:
            with open("memos.json", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_memos(m):
    try:
        with open("memos.json", "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False)
    except Exception:
        pass


# ====================== 데이터 로더 (캐시) ======================
@st.cache_data(ttl=600, show_spinner="카페24에서 주문 데이터를 가져오는 중...")
def load_orders(start, end):
    return Cafe24Client().get_orders(start, end)


@st.cache_data(ttl=1800, show_spinner="전체 상품 재고를 불러오는 중...")
def load_inventory():
    client = Cafe24Client()
    products = client.get_all_products()
    rows = []
    for p in products:
        pno = p.get("product_no")
        pname = p.get("product_name", "")
        try:
            variants = client.get_variants(pno)
        except Exception:
            continue
        for v in variants:
            opts = v.get("options")
            optval = opts[0].get("value") if isinstance(opts, list) and opts else ""
            rows.append({
                "variant_code": v.get("variant_code"), "product_no": pno,
                "상품명": pname, "옵션": optval,
                "재고": int(to_amount(v.get("quantity"))),
                "안전재고": int(to_amount(v.get("safety_inventory"))),
                "판매중": v.get("selling") == "T",
            })
    return rows


def save_inventory_snapshot(inventory):
    path = "inventory_history.json"
    hist = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            hist = {}
    hist[str(date.today())] = {r["variant_code"]: r["재고"]
                               for r in inventory if r.get("variant_code")}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False)
    except Exception:
        pass
    return len(hist)


# ====================== 사이드바 (탭 네비 + 주문기간) ======================
with st.sidebar:
    st.title("📊 그로스 대시보드")
    page = st.radio("대시보드 이동",
                    ["매출 대시보드", "상품 대시보드", "재고 대시보드"])
    st.divider()
    st.header("주문 데이터 기간")
    today = date.today()
    start_date = st.date_input("시작일", today - timedelta(days=30))
    end_date = st.date_input("종료일", today)
    run = st.button("주문 데이터 새로고침", type="primary")

if run:
    st.session_state["orders"] = load_orders(str(start_date), str(end_date))
elif "orders" not in st.session_state:
    try:
        st.session_state["orders"] = load_orders(str(start_date), str(end_date))
    except Exception as e:
        st.session_state["orders"] = None
        st.warning("주문 데이터 자동 로드 실패. 사이드바에서 새로고침을 눌러주세요.\n\n" + str(e)[:300])

orders = st.session_state.get("orders")


# ======================================================================
# 매출 대시보드
# ======================================================================
def render_sales(orders):
    st.title("매출 대시보드")
    if not orders:
        st.info("주문 데이터를 불러오는 중입니다. 사이드바에서 새로고침을 눌러주세요.")
        return

    # 방문자 CSV (상단 KPI의 유입수/전환율에 사용)
    csv_file = st.file_uploader("방문자 CSV 업로드 (처음방문vs재방문 구매)",
                                type=["csv"], key="visitor_csv")
    adf = None
    if csv_file is not None:
        adf = pd.read_csv(csv_file)
        adf["date"] = pd.to_datetime(adf["date"])
        adf = adf.sort_values("date").set_index("date")
        for c in ["first_visit_count", "revisit_count", "first_visit_purchase",
                  "first_visit_amount", "revisit_purchase", "revisit_amount"]:
            if c in adf.columns:
                adf[c] = pd.to_numeric(adf[c], errors="coerce").fillna(0)
        adf["방문수"] = adf["first_visit_count"] + adf["revisit_count"]
        adf["구매건수"] = adf["first_visit_purchase"] + adf["revisit_purchase"]
        adf["신규유입비중"] = (adf["first_visit_count"] / adf["방문수"] * 100).round(1)
        adf["신규전환율"] = (adf["first_visit_purchase"] / adf["first_visit_count"] * 100).round(3)
        adf["재방문전환율"] = (adf["revisit_purchase"] / adf["revisit_count"] * 100).round(3)
        adf["전체전환율"] = (adf["구매건수"] / adf["방문수"] * 100).round(3)
        adf["신규객단가"] = (adf["first_visit_amount"] / adf["first_visit_purchase"]).fillna(0).round(0)
        adf["재방문객단가"] = (adf["revisit_amount"] / adf["revisit_purchase"]).fillna(0).round(0)

    odf = build_order_df(orders)
    valid = odf[odf["결제완료"] & ~odf["취소"]]
    canceled = odf[odf["취소"]]
    conv_sales = valid["결제금액"].sum()
    conv_count = len(valid)
    refund_amount = canceled["결제금액"].sum()
    gross = odf[odf["결제완료"]]["결제금액"].sum()
    aov = conv_sales / conv_count if conv_count else 0
    유입수 = int(adf["방문수"].sum()) if adf is not None else None
    전환율 = (adf["구매건수"].sum() / adf["방문수"].sum() * 100) if (adf is not None and adf["방문수"].sum()) else None

    # ----- 디자인 스타일 (화이트 카드 + 블루 포인트) -----
    st.markdown("""<style>
    .kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:6px 0 18px;}
    .kpi-card{background:#F4F6F9;border-radius:12px;padding:16px 18px;}
    .kpi-label{font-size:13px;color:#6B7280;}
    .kpi-tag{font-size:11px;color:#AAB0BC;}
    .kpi-val{font-size:24px;font-weight:700;color:#1A2233;margin-top:4px;letter-spacing:-0.5px;}
    .wcard{background:#fff;border:0.5px solid #E6E9EF;border-radius:12px;padding:20px;margin-bottom:18px;}
    .ratio-bar{display:flex;height:16px;border-radius:4px;overflow:hidden;margin-bottom:18px;}
    .ratio-stats{display:grid;grid-template-columns:1fr 1fr;gap:16px;}
    .rs-label{font-size:13px;color:#6B7280;}
    .rs-dot{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px;vertical-align:middle;}
    .rs-val{font-size:22px;font-weight:700;color:#1A2233;margin:4px 0;}
    .rs-sub{font-size:13px;color:#6B7280;font-weight:400;}
    </style>""", unsafe_allow_html=True)

    refund_rate = (refund_amount / gross * 100) if gross else 0

    # 1) 상단 KPI 6개 (카드)
    cards = [
        ("총 매출", won_short(conv_sales), ""),
        ("전환수", f"{conv_count:,}건", ""),
        ("평균 객단가", won(aov), ""),
        ("유입수", f"{유입수:,}" if 유입수 is not None else "—", "· CSV"),
        ("전환율", f"{전환율:.1f}%" if 전환율 is not None else "—", "· CSV"),
        ("환불율", f"{refund_rate:.1f}%", ""),
    ]
    kpi_html = '<div class="kpi-grid">' + "".join(
        f'<div class="kpi-card"><div class="kpi-label">{l} <span class="kpi-tag">{tag}</span></div>'
        f'<div class="kpi-val">{v}</div></div>' for l, v, tag in cards) + '</div>'
    st.markdown(kpi_html, unsafe_allow_html=True)
    if adf is None:
        st.caption("유입수·전환율은 위에서 방문자 CSV를 올리면 표시됩니다.")

    # 2~3) 신규 vs 재구매 비율 막대 + 직전 기간 등락
    st.subheader("신규 구매 vs 재구매")
    new_sales = valid[valid["신규고객"]]["결제금액"].sum()
    repeat_sales = valid[~valid["신규고객"]]["결제금액"].sum()
    tot = new_sales + repeat_sales
    new_pct = round(new_sales / tot * 100) if tot else 0
    rep_pct = 100 - new_pct

    L = (end_date - start_date).days + 1
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=L - 1)
    try:
        prev_orders = load_orders(str(prev_start), str(prev_end))
    except Exception:
        prev_orders = []
    pv = build_order_df(prev_orders) if prev_orders else pd.DataFrame()
    if not pv.empty:
        pv = pv[pv["결제완료"] & ~pv["취소"]]
        prev_new = pv[pv["신규고객"]]["결제금액"].sum()
        prev_repeat = pv[~pv["신규고객"]]["결제금액"].sum()
    else:
        prev_new = prev_repeat = 0

    def delta_num(cur, prev):
        return (cur / prev - 1) * 100 if prev else None

    def delta_span(v):
        if v is None:
            return '<span style="color:#9AA0AC;font-weight:600;">— 비교불가</span>'
        color = "#1D9E75" if v > 0 else ("#E5484D" if v < 0 else "#9AA0AC")
        arrow = "▲" if v > 0 else ("▼" if v < 0 else "─")
        return f'<span style="color:{color};font-weight:600;">{arrow} {abs(v):.1f}%</span>'

    dn_new = delta_num(new_sales, prev_new)
    dn_rep = delta_num(repeat_sales, prev_repeat)
    st.markdown(f"""<div class="wcard">
      <div class="ratio-bar"><div style="width:{new_pct}%;background:#378ADD;"></div><div style="width:{rep_pct}%;background:#B5D4F4;"></div></div>
      <div class="ratio-stats">
        <div><div class="rs-label"><span class="rs-dot" style="background:#378ADD;"></span>신규 구매</div>
          <div class="rs-val">{won_short(new_sales)} <span class="rs-sub">{new_pct}%</span></div>
          {delta_span(dn_new)} <span class="rs-sub">직전 {L}일 대비</span></div>
        <div><div class="rs-label"><span class="rs-dot" style="background:#B5D4F4;"></span>재구매</div>
          <div class="rs-val">{won_short(repeat_sales)} <span class="rs-sub">{rep_pct}%</span></div>
          {delta_span(dn_rep)} <span class="rs-sub">직전 {L}일 대비</span></div>
      </div>
    </div>""", unsafe_allow_html=True)

    # 4) 일별 매출 추이 (신규/재구매 누적) + 메모
    st.subheader("일별 매출 추이")
    st.caption("신규·재구매를 쌓아 보여줍니다. 급변한 날은 메모로 기록해두세요.")
    daily = (valid.groupby([valid["날짜"].dt.date, "신규고객"])["결제금액"]
             .sum().unstack(fill_value=0))
    daily = daily.rename(columns={True: "신규구매", False: "재구매"}).sort_index()
    for col in ["신규구매", "재구매"]:
        if col not in daily.columns:
            daily[col] = 0
    total_series = daily["신규구매"] + daily["재구매"]
    date_list = [str(d) for d in daily.index]
    memos = load_memos()

    fig = go.Figure()
    fig.add_bar(x=date_list, y=daily["신규구매"], name="신규구매", marker_color="#378ADD")
    fig.add_bar(x=date_list, y=daily["재구매"], name="재구매", marker_color="#B5D4F4")
    fig.update_layout(barmode="stack", height=360, margin=dict(t=30, b=10, l=10, r=10),
                      legend=dict(orientation="h", y=1.12, x=0),
                      plot_bgcolor="white", yaxis=dict(gridcolor="#EEF1F5"))
    for ds in date_list:
        if ds in memos and memos[ds].strip():
            fig.add_annotation(x=ds, y=float(total_series.loc[pd.to_datetime(ds).date()]),
                               text="📌 " + memos[ds], showarrow=True, arrowhead=2,
                               ax=0, ay=-40, bgcolor="#FFF7E6", bordercolor="#E0A800",
                               font=dict(size=11))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # 그래프 바로 아래에서 메모 추가
    mc1, mc2, mc3 = st.columns([1.2, 3, 0.8])
    md = mc1.selectbox("날짜", date_list, index=len(date_list) - 1,
                       key="memo_date", label_visibility="collapsed")
    mt = mc2.text_input("메모", value="", key="memo_text",
                        placeholder="이 날 무슨 일이 있었나요? (예: 라이브방송, 광고집행)",
                        label_visibility="collapsed")
    if mc3.button("메모 추가", use_container_width=True):
        if mt.strip():
            memos[md] = mt.strip()
            save_memos(memos)
            st.rerun()

    active = {d: t for d, t in sorted(memos.items()) if t.strip()}
    if active:
        for d, t in active.items():
            a1, a2 = st.columns([6, 1])
            a1.markdown(f"📌 **{d}** — {t}")
            if a2.button("삭제", key=f"del_{d}", use_container_width=True):
                memos.pop(d, None)
                save_memos(memos)
                st.rerun()
    else:
        st.caption("아직 기록된 메모가 없어요. 위에서 날짜를 고르고 메모를 추가해보세요.")

    # 유입 · 전환 상세 (상단에서 올린 CSV 재사용)
    st.divider()
    st.subheader("유입 · 전환 분석")
    if adf is None:
        st.info("위에서 방문자 CSV를 올리면 유입·전환율·객단가 분석이 표시됩니다.")
        return
    st.caption("유입 — 신규 vs 재방문")
    st.bar_chart(adf[["first_visit_count", "revisit_count"]]
                 .rename(columns={"first_visit_count": "신규방문", "revisit_count": "재방문"}))
    st.caption("전환율 — 신규 / 재방문 / 전체")
    st.line_chart(adf[["신규전환율", "재방문전환율", "전체전환율"]])
    st.caption("객단가 — 신규 / 재방문")
    st.line_chart(adf[["신규객단가", "재방문객단가"]])
    st.caption("신규 그로스 진단 (전반 대비 후반 평균 변화)")
    levers = {"신규 유입수": adf["first_visit_count"], "신규 전환율(%)": adf["신규전환율"],
              "신규 객단가": adf["신규객단가"], "신규 매출": adf["first_visit_amount"]}
    cols = st.columns(len(levers))
    for col, (name, s) in zip(cols, levers.items()):
        _, last, rate = half_change(s)
        arrow = "▲" if rate > 0 else ("▼" if rate < 0 else "─")
        col.metric(name, f"{last:,.1f}" if "율" in name else f"{last:,.0f}", f"{arrow} {rate:+.1f}%")


# ======================================================================
# 상품 대시보드
# ======================================================================
def render_product(orders):
    st.title("상품 대시보드")
    product_csv = st.file_uploader("상품별 매출분석 CSV 업로드", type=["csv"], key="product_csv")

    if product_csv is not None:
        pdf = pd.read_csv(product_csv).rename(columns={
            "exposure_count": "조회수", "cart_count": "장바구니", "order_count": "전환수",
            "conversion_rate": "전환율", "order_amount": "매출",
            "order_to_cart_rate": "장바구니→주문율", "product_name": "상품명"})
        for c in ["조회수", "장바구니", "전환수", "전환율", "매출", "장바구니→주문율"]:
            if c in pdf.columns:
                pdf[c] = pd.to_numeric(pdf[c], errors="coerce").fillna(0)
        prod_cat = {}
        if orders:
            tmp = {}
            for o in orders:
                for it in (o.get("items") or []):
                    pno = it.get("product_no")
                    if pno is None:
                        continue
                    대 = classify(it.get("product_name", ""), option_label(it))[0]
                    tmp.setdefault(str(pno), Counter())[대] += 1
            prod_cat = {k: c.most_common(1)[0][0] for k, c in tmp.items()}
        pdf["대분류"] = pdf.apply(
            lambda r: prod_cat.get(str(r.get("product_no", "")),
                                   classify(r.get("상품명", ""), "")[0]), axis=1)

        st.subheader("상품 퍼널 (조회 → 장바구니 → 전환)")
        p1, p2, p3 = st.columns(3)
        p1.metric("총 조회수", f"{int(pdf['조회수'].sum()):,}")
        p2.metric("총 전환수", f"{int(pdf['전환수'].sum()):,}")
        ov = pdf["전환수"].sum() / pdf["조회수"].sum() * 100 if pdf["조회수"].sum() else 0
        p3.metric("전체 전환율", f"{ov:.2f}%")

        st.caption("조회수 대비 전환율 (색=카테고리, 버블=매출). 오른쪽 아래 = 개선 후보")
        fig = px.scatter(pdf, x="조회수", y="전환율", size="매출", color="대분류",
                         hover_name="상품명", size_max=45,
                         color_discrete_map={"캐리어": "#378ADD", "악세사리": "#1D9E75",
                                             "미분류": "#888780"})
        fig.update_layout(height=400, margin=dict(t=10, b=10, l=10, r=10),
                          legend=dict(orientation="h", y=-0.18))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        med_v, med_c = pdf["조회수"].median(), pdf["전환율"].median()
        st.caption(f"전환 개선 후보 (조회>{med_v:,.0f} & 전환율<{med_c:.2f}%)")
        cand = (pdf[(pdf["조회수"] > med_v) & (pdf["전환율"] < med_c)]
                .sort_values("조회수", ascending=False)
                [["상품명", "대분류", "조회수", "장바구니", "전환수", "전환율", "매출"]])
        st.dataframe(cand.style.format({"조회수": "{:,}", "장바구니": "{:,}", "전환수": "{:,}",
                                        "전환율": "{:.2f}%", "매출": "₩{:,.0f}"}),
                     use_container_width=True, hide_index=True)

        if "장바구니→주문율" in pdf.columns:
            st.caption("장바구니 이탈 후보 (장바구니 많고 장바구니→주문율 낮음)")
            mc, mo = pdf["장바구니"].median(), pdf["장바구니→주문율"].median()
            drop = (pdf[(pdf["장바구니"] > mc) & (pdf["장바구니→주문율"] < mo)]
                    .sort_values("장바구니", ascending=False)
                    [["상품명", "대분류", "장바구니", "전환수", "장바구니→주문율", "매출"]])
            st.dataframe(drop.style.format({"장바구니": "{:,}", "전환수": "{:,}",
                                            "장바구니→주문율": "{:.2f}%", "매출": "₩{:,.0f}"}),
                         use_container_width=True, hide_index=True)
    else:
        st.info("상품별 매출분석 CSV를 올리면 조회·전환 퍼널 분석이 표시됩니다.")

    if not orders:
        return

    # 카테고리별 분석
    st.divider()
    st.subheader("카테고리별 분석")
    exclude_staff = st.checkbox("임직원 상품 제외", value=False)
    crows = []
    for o in orders:
        if o.get("canceled") == "T":
            continue
        odate = (o.get("order_date") or "")[:10]
        for it in (o.get("items") or []):
            net = int(to_amount(it.get("quantity"))) - int(to_amount(it.get("claim_quantity")))
            if net <= 0:
                continue
            amt = (to_amount(it.get("product_price")) + to_amount(it.get("option_price"))) * net
            대, 중, 태그, staff = classify(it.get("product_name", ""), option_label(it))
            crows.append({"날짜": odate, "대분류": 대, "중분류": 중, "모델태그": 태그,
                          "임직원": staff, "수량": net, "매출": amt})
    cdf = pd.DataFrame(crows)
    if exclude_staff and not cdf.empty:
        cdf = cdf[~cdf["임직원"]]
    if not cdf.empty:
        big = cdf.groupby("대분류").agg(매출=("매출", "sum"), 수량=("수량", "sum"))
        cc1, cc2 = st.columns(2)
        with cc1:
            f = px.pie(values=big["매출"], names=big.index, hole=0.55,
                       color_discrete_sequence=["#378ADD", "#1D9E75", "#888780"])
            f.update_traces(textinfo="percent+label")
            f.update_layout(height=260, margin=dict(t=10, b=10, l=10, r=10),
                            legend=dict(orientation="h", y=-0.1))
            st.plotly_chart(f, use_container_width=True, config={"displayModeBar": False})
        with cc2:
            st.dataframe(big.style.format({"매출": "₩{:,.0f}", "수량": "{:,}"}),
                         use_container_width=True)
        carrier = cdf[cdf["대분류"] == "캐리어"]
        if not carrier.empty:
            st.caption("캐리어 모델별 매출")
            ms = (carrier.groupby("중분류").agg(매출=("매출", "sum"), 수량=("수량", "sum"))
                  .sort_values("매출", ascending=False))
            st.bar_chart(ms["매출"])

    # 상품별 매출 순위
    st.divider()
    st.subheader("상품별 매출 순위 TOP 20")
    irows = []
    for o in orders:
        if o.get("canceled") == "T":
            continue
        for it in (o.get("items") or []):
            qty = int(to_amount(it.get("quantity")))
            irows.append({"상품명": it.get("product_name", "(이름없음)"), "수량": qty,
                          "매출": qty * to_amount(it.get("product_price"))})
    if irows:
        rk = (pd.DataFrame(irows).groupby("상품명")
              .agg(판매수량=("수량", "sum"), 매출=("매출", "sum"))
              .sort_values("매출", ascending=False).head(20))
        st.bar_chart(rk["매출"])

    # 일자별 옵션 판매 수량
    st.divider()
    st.subheader("일자별 제품 옵션 판매 수량")
    orows = []
    for o in orders:
        odate = (o.get("order_date") or "")[:10]
        for it in (o.get("items") or []):
            net = int(to_amount(it.get("quantity"))) - int(to_amount(it.get("claim_quantity")))
            orows.append({"날짜": odate, "상품명": it.get("product_name", "(이름없음)"),
                          "옵션": option_label(it), "순수량": net})
    oodf = pd.DataFrame(orows)
    oodf = oodf[oodf["날짜"] != ""]
    if not oodf.empty:
        oodf["날짜"] = pd.to_datetime(oodf["날짜"]).dt.date
        order_by = (oodf.groupby("상품명")["순수량"].sum()
                    .sort_values(ascending=False).index.tolist())
        sel = st.multiselect("상품 선택", order_by, default=order_by[:1])
        view = oodf[oodf["상품명"].isin(sel)] if sel else oodf
        view = view.assign(옵션라벨=view["상품명"] + " · " + view["옵션"])
        piv = (view.pivot_table(index="날짜", columns="옵션라벨", values="순수량",
                                aggfunc="sum", fill_value=0).sort_index())
        st.dataframe(piv, use_container_width=True)

    # 옵션 분류 매핑 참조표
    st.divider()
    with st.expander("[참조] 옵션 분류 매핑 표"):
        seen = {}
        for o in orders:
            for it in (o.get("items") or []):
                pname = it.get("product_name", "(이름없음)")
                opt = option_label(it)
                if (pname, opt) not in seen:
                    대, 중, 태그, staff = classify(pname, opt)
                    seen[(pname, opt)] = {"상품명": pname, "옵션": opt, "대분류": 대,
                                          "중분류": 중, "모델태그": (태그 + "용") if 태그 else "",
                                          "임직원": "Y" if staff else ""}
        if seen:
            st.dataframe(pd.DataFrame(list(seen.values()))
                         .sort_values(["대분류", "중분류", "상품명", "옵션"]),
                         use_container_width=True, hide_index=True)


# ======================================================================
# 재고 대시보드
# ======================================================================
def render_inventory(orders):
    st.title("재고 대시보드")
    st.caption("전체 상품 품목별 재고 · 판매속도(7/30/90일) 기반 품절 예측 · 리드타임 90일")
    reorder_csv = st.file_uploader("발주 일정 CSV (선택: variant_code, 입고예정일)",
                                   type=["csv"], key="reorder_csv")

    if st.button("재고 불러오기 (전체 상품)"):
        st.session_state["inventory"] = load_inventory()
        cnt = save_inventory_snapshot(st.session_state["inventory"])
        st.success(f"재고를 불러왔어요. (재고 이력 {cnt}일치 누적)")
    inventory = st.session_state.get("inventory")

    if not inventory:
        st.info("'재고 불러오기'를 누르면 전체 상품의 품목별 재고를 조회합니다.")
        return
    if not orders:
        st.info("판매속도 계산을 위해 주문 데이터가 필요합니다.")
        return

    today = date.today()
    o90 = load_orders(str(today - timedelta(days=90)), str(today))
    s7 = variant_sales_in_range(o90, today - timedelta(days=6), today)
    s30 = variant_sales_in_range(o90, today - timedelta(days=29), today)
    s90 = variant_sales_in_range(o90, today - timedelta(days=89), today)

    inv = pd.DataFrame(inventory)
    inv["일판매(7일)"] = inv["variant_code"].map(lambda v: round(s7.get(v, 0) / 7, 2))
    inv["일판매(30일)"] = inv["variant_code"].map(lambda v: round(s30.get(v, 0) / 30, 2))
    inv["일판매(90일)"] = inv["variant_code"].map(lambda v: round(s90.get(v, 0) / 90, 2))
    inv["소진일(30일속도)"] = inv.apply(
        lambda r: round(r["재고"] / r["일판매(30일)"]) if r["일판매(30일)"] > 0 else None, axis=1)

    incoming = {}
    if reorder_csv is not None:
        try:
            rc = pd.read_csv(reorder_csv)
            for _, r in rc.iterrows():
                incoming[str(r["variant_code"]).strip()] = str(r["입고예정일"]).strip()
        except Exception as e:
            st.warning("발주 일정 CSV 읽기 실패: " + str(e)[:150])
    inv["입고예정"] = inv["variant_code"].map(lambda v: incoming.get(str(v), ""))

    def has_incoming_soon(d):
        if not d:
            return False
        try:
            return (datetime.strptime(d, "%Y-%m-%d").date() - today).days <= 90
        except ValueError:
            return False

    soon = inv[(inv["재고"] > 0) & inv["소진일(30일속도)"].notna() & (inv["소진일(30일속도)"] <= 90)]
    soon = soon[~soon["입고예정"].apply(has_incoming_soon)]
    stagnant = inv[(inv["재고"] > 0) & (inv["일판매(30일)"] == 0)]

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("총 품목 수", f"{len(inv):,}")
    k2.metric("품절(재고 0)", f"{int((inv['재고']==0).sum())}개")
    k3.metric("품절 임박(90일 내)", f"{len(soon)}개")
    k4.metric("부진 재고(30일 무판매)", f"{len(stagnant)}개")

    st.subheader("품절 임박 경보 (90일 내 소진 · 입고예정 없음)")
    if not soon.empty:
        st.dataframe(soon.sort_values("소진일(30일속도)")
                     [["상품명", "옵션", "재고", "일판매(7일)", "일판매(30일)",
                       "일판매(90일)", "소진일(30일속도)", "입고예정"]],
                     use_container_width=True, hide_index=True)
    else:
        st.success("90일 내 품절 위험 품목이 없습니다. 👍")

    st.subheader("판매량 추이 변화 (모델별 WoW / MoM)")
    wr = model_sales_in_range(o90, today - timedelta(days=6), today)
    wp = model_sales_in_range(o90, today - timedelta(days=13), today - timedelta(days=7))
    mr = model_sales_in_range(o90, today - timedelta(days=29), today)
    mp = model_sales_in_range(o90, today - timedelta(days=59), today - timedelta(days=30))

    def pct(r, p):
        return round((r / p - 1) * 100, 1) if p else None
    rows = [{"모델/카테고리": k, "최근7일": wr.get(k, 0), "이전7일": wp.get(k, 0),
             "WoW%": pct(wr.get(k, 0), wp.get(k, 0)), "최근30일": mr.get(k, 0),
             "이전30일": mp.get(k, 0), "MoM%": pct(mr.get(k, 0), mp.get(k, 0))}
            for k in sorted(set(wr) | set(wp) | set(mr) | set(mp))]
    st.dataframe(pd.DataFrame(rows).sort_values("최근30일", ascending=False),
                 use_container_width=True, hide_index=True)
    st.caption("YoY는 1년치 데이터가 쌓이면 추가됩니다.")

    st.subheader("부진 재고 (재고 있으나 최근 30일 판매 0)")
    if not stagnant.empty:
        st.dataframe(stagnant.sort_values("재고", ascending=False)
                     [["상품명", "옵션", "재고", "일판매(90일)", "판매중"]],
                     use_container_width=True, hide_index=True)
    else:
        st.success("해당 품목이 없습니다.")

    with st.expander("📋 전체 품목 재고 표"):
        st.dataframe(inv[["상품명", "옵션", "재고", "안전재고", "일판매(7일)", "일판매(30일)",
                          "일판매(90일)", "소진일(30일속도)", "입고예정", "판매중"]]
                     .sort_values(["상품명", "옵션"]),
                     use_container_width=True, hide_index=True)


# ====================== 라우팅 ======================
if page == "매출 대시보드":
    render_sales(orders)
elif page == "상품 대시보드":
    render_product(orders)
else:
    render_inventory(orders)
