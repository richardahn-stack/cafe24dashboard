"""카페24 자사몰 그로스 대시보드.

레이아웃
  [최상단] 매출 분석 (주문 API 자동 · 최근 30일)
     - KPI: 총매출 / 전환수 / 객단가 / 환불율
     - 신규 vs 재구매 매출 파이차트 (first_order 기준)  |  일별 매출 추이
  유입 / 전환율 / 매출 / 객단가 / 그로스 진단 (애널리틱스 CSV)
  상품별 매출 순위 (주문 API)
"""
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from cafe24_client import Cafe24Client

st.set_page_config(page_title="자사몰 그로스 대시보드", layout="wide")
st.title("📊 카페24 자사몰 그로스 대시보드")


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


def option_label(it):
    """주문 항목에서 사람이 읽기 좋은 옵션 라벨을 추출."""
    opts = it.get("options")
    if isinstance(opts, list) and opts:
        texts = []
        for op in opts:
            ov = op.get("option_value") if isinstance(op, dict) else None
            if isinstance(ov, dict) and ov.get("option_text"):
                texts.append(ov["option_text"])
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
    rate = (last / first - 1) * 100 if first else 0.0
    return first, last, rate


# ====================== 사이드바 ======================
with st.sidebar:
    st.header("주문 데이터 (카페24 API)")
    today = date.today()
    start_date = st.date_input("시작일", today - timedelta(days=30))
    end_date = st.date_input("종료일", today)
    run = st.button("주문 데이터 새로고침", type="primary")

    st.divider()
    st.header("방문자 데이터 (애널리틱스 CSV)")
    st.caption("고객분석 > 구매패턴 > 처음방문vs재방문 구매 에서 내려받은 CSV를 올리세요.")
    csv_file = st.file_uploader("CSV 업로드", type=["csv"])


@st.cache_data(ttl=600, show_spinner="카페24에서 주문 데이터를 가져오는 중...")
def load_orders(start, end):
    return Cafe24Client().get_orders(start, end)


# 페이지 진입 시 최근 30일 주문을 자동 로드 (버튼으로 갱신 가능)
if run:
    st.session_state["orders"] = load_orders(str(start_date), str(end_date))
elif "orders" not in st.session_state:
    try:
        st.session_state["orders"] = load_orders(str(start_date), str(end_date))
    except Exception as e:
        st.session_state["orders"] = None
        st.warning("주문 데이터 자동 로드에 실패했어요. 사이드바에서 기간 확인 후 "
                   "'주문 데이터 새로고침'을 눌러주세요.\n\n" + str(e)[:300])

orders = st.session_state.get("orders")


# ====================== 주문 데이터 정리 ======================
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


# ======================================================================
# [최상단] 매출 분석 — 주문 API 자동
# ======================================================================
st.header("매출 분석")
st.caption("주문 API 기준 · 사이드바에서 설정한 기간(기본 최근 30일)")

if orders:
    odf = build_order_df(orders)
    valid = odf[odf["결제완료"] & ~odf["취소"]]
    canceled_orders = odf[odf["취소"]]

    conv_count = len(valid)
    conv_sales = valid["결제금액"].sum()
    refund_amount = canceled_orders["결제금액"].sum()
    gross_paid = odf[odf["결제완료"]]["결제금액"].sum()
    refund_rate = refund_amount / gross_paid * 100 if gross_paid else 0
    aov = conv_sales / conv_count if conv_count else 0

    # KPI
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("총 매출", won(conv_sales))
    k2.metric("전환수", f"{conv_count:,}건")
    k3.metric("평균 객단가", won(aov))
    k4.metric("환불율", f"{refund_rate:.1f}%", help=f"환불 금액 {won(refund_amount)} (전체취소 기준)")

    # 파이차트 + 일별 추이
    col_pie, col_trend = st.columns([1, 2])
    with col_pie:
        st.subheader("신규 vs 재구매 매출")
        new_sales = valid[valid["신규고객"]]["결제금액"].sum()
        repeat_sales = valid[~valid["신규고객"]]["결제금액"].sum()
        fig = px.pie(
            values=[new_sales, repeat_sales],
            names=["신규 구매", "재구매"],
            hole=0.55,
            color_discrete_sequence=["#378ADD", "#1D9E75"],
        )
        fig.update_traces(textinfo="percent+label", textfont_size=13)
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300,
                          legend=dict(orientation="h", y=-0.1))
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"신규 {won(new_sales)} · 재구매 {won(repeat_sales)} "
                   "(first_order 기준 · 고객 첫 주문)")

    with col_trend:
        st.subheader("일별 매출 추이")
        daily = valid.groupby(valid["날짜"].dt.date)["결제금액"].sum()
        st.bar_chart(daily)
else:
    st.info("주문 데이터를 불러오는 중이거나, 사이드바에서 '주문 데이터 새로고침'이 필요합니다.")


# ======================================================================
# 유입 / 전환율 / 객단가 / 그로스 진단 — 애널리틱스 CSV
# ======================================================================
st.divider()
st.header("유입 · 전환 분석 (방문자 데이터)")

if csv_file is not None:
    adf = pd.read_csv(csv_file)
    adf["date"] = pd.to_datetime(adf["date"])
    adf = adf.sort_values("date").set_index("date")
    for c in ["first_visit_count", "revisit_count", "first_visit_purchase",
              "first_visit_amount", "revisit_purchase", "revisit_amount",
              "first_visit_rate", "revisit_rate", "total_order_amount"]:
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

    st.caption(f"방문자 데이터 기간: {adf.index.min():%Y-%m-%d} ~ {adf.index.max():%Y-%m-%d} ({len(adf)}일)")
    tot_visit = int(adf["방문수"].sum())
    a1, a2, a3 = st.columns(3)
    a1.metric("총 방문수", f"{tot_visit:,}")
    a2.metric("신규 유입 비중", f"{adf['first_visit_count'].sum()/tot_visit*100:.1f}%")
    a3.metric("전체 전환율", f"{adf['구매건수'].sum()/tot_visit*100:.2f}%")

    st.subheader("1. 유입 — 신규 vs 재방문")
    c1, c2 = st.columns([2, 1])
    with c1:
        st.bar_chart(adf[["first_visit_count", "revisit_count"]]
                     .rename(columns={"first_visit_count": "신규방문", "revisit_count": "재방문"}))
    with c2:
        st.caption("신규 유입 비중(%)")
        st.line_chart(adf[["신규유입비중"]])

    st.subheader("2. 전환율 — 유입 대비 구매")
    st.line_chart(adf[["신규전환율", "재방문전환율", "전체전환율"]])

    st.subheader("3. 객단가 — 신규 vs 재방문")
    st.line_chart(adf[["신규객단가", "재방문객단가"]])

    st.subheader("4. 신규 그로스 진단")
    st.caption("신규 매출 = 신규 유입수 × 전환율 × 객단가. 기간 전반 대비 후반 평균 변화율.")
    levers = {
        "신규 유입수": adf["first_visit_count"],
        "신규 전환율(%)": adf["신규전환율"],
        "신규 객단가": adf["신규객단가"],
        "신규 매출": adf["first_visit_amount"],
    }
    cols = st.columns(len(levers))
    for col, (name, series) in zip(cols, levers.items()):
        _, last, rate = half_change(series)
        arrow = "▲" if rate > 0 else ("▼" if rate < 0 else "─")
        col.metric(name, f"{last:,.1f}" if "율" in name else f"{last:,.0f}", f"{arrow} {rate:+.1f}%")

    with st.expander("📋 일자별 상세 표"):
        show = adf[["first_visit_count", "revisit_count", "신규유입비중", "구매건수",
                    "신규전환율", "재방문전환율", "전체전환율",
                    "first_visit_amount", "revisit_amount", "신규객단가", "재방문객단가"]].copy()
        show.columns = ["신규방문", "재방문", "신규비중%", "구매건수", "신규전환율%",
                        "재방문전환율%", "전체전환율%", "신규매출", "재방문매출",
                        "신규객단가", "재방문객단가"]
        st.dataframe(show, use_container_width=True)
else:
    st.info("← 사이드바에서 애널리틱스 CSV를 올리면 유입·전환율·객단가 분석이 표시됩니다.")


# ======================================================================
# 상품별 매출 순위 — 주문 API
# ======================================================================
st.divider()
st.header("상품별 매출 순위 TOP 20")
if orders:
    item_rows = []
    for o in orders:
        if o.get("canceled") == "T":
            continue
        for it in (o.get("items") or []):
            qty = int(to_amount(it.get("quantity")))
            price = to_amount(it.get("product_price"))
            item_rows.append({"상품명": it.get("product_name", "(이름없음)"),
                              "수량": qty, "매출": qty * price})
    if item_rows:
        idf = pd.DataFrame(item_rows)
        ranking = (idf.groupby("상품명")
                   .agg(판매수량=("수량", "sum"), 매출=("매출", "sum"))
                   .sort_values("매출", ascending=False).head(20))
        st.bar_chart(ranking["매출"])
        st.dataframe(ranking.style.format({"매출": "₩{:,.0f}", "판매수량": "{:,}"}),
                     use_container_width=True)
    with st.expander("🔍 원본 주문 데이터 (필드명 확인용)"):
        st.json(orders[0])
else:
    st.info("주문 데이터가 로드되면 표시됩니다.")


# ======================================================================
# 일자별 제품 옵션 판매 수량 — 주문 API (items)
# ======================================================================
st.divider()
st.header("일자별 제품 옵션 판매 수량")
st.caption("주문 항목(items) 기준 · 순수량 = 판매수량 − 취소/반품수량")

if orders:
    irows = []
    for o in orders:
        odate = (o.get("order_date") or "")[:10]
        for it in (o.get("items") or []):
            qty = int(to_amount(it.get("quantity")))
            claim = int(to_amount(it.get("claim_quantity")))
            irows.append({
                "날짜": odate,
                "상품명": it.get("product_name", "(이름없음)"),
                "옵션": option_label(it),
                "판매수량": qty,
                "취소수량": claim,
                "순수량": qty - claim,
            })
    idf = pd.DataFrame(irows)
    idf = idf[idf["날짜"] != ""]

    if idf.empty:
        st.info("판매된 상품 항목이 없습니다.")
    else:
        idf["날짜"] = pd.to_datetime(idf["날짜"]).dt.date
        # 판매량 많은 순으로 상품 정렬
        order_by_qty = (idf.groupby("상품명")["순수량"].sum()
                        .sort_values(ascending=False).index.tolist())
        sel = st.multiselect("상품 선택 (여러 개 가능)", order_by_qty,
                             default=order_by_qty[:1])
        view = idf[idf["상품명"].isin(sel)] if sel else idf
        view = view.assign(옵션라벨=view["상품명"] + " · " + view["옵션"])

        pivot = (view.pivot_table(index="날짜", columns="옵션라벨",
                                  values="순수량", aggfunc="sum", fill_value=0)
                 .sort_index())

        st.subheader("일자별 옵션 판매 수량 (표)")
        # 합계 행 추가
        table = pivot.copy()
        table.loc["합계"] = table.sum()
        st.dataframe(table, use_container_width=True)

        st.subheader("일자별 옵션 판매 수량 (추이)")
        st.line_chart(pivot)

        with st.expander("⬇️ 전체 옵션 × 일자 데이터 (필터 없이)"):
            full = (idf.assign(옵션라벨=idf["상품명"] + " · " + idf["옵션"])
                    .pivot_table(index="날짜", columns="옵션라벨",
                                 values="순수량", aggfunc="sum", fill_value=0)
                    .sort_index())
            st.dataframe(full, use_container_width=True)
else:
    st.info("주문 데이터가 로드되면 표시됩니다.")
