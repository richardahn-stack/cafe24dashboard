"""카페24 자사몰 그로스 대시보드 (3개 탭: 매출 / 상품 / 재고)."""
import json
import os
import re
import requests
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
@st.cache_data(ttl=300)
def load_data_json(name):
    """data/ 폴더의 JSON을 읽음 (GitHub Actions가 매일 생성)."""
    path = os.path.join("data", name)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


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


# ====================== 사이드바 (탭 네비) ======================
with st.sidebar:
    st.title("📊 그로스 대시보드")
    page = st.radio("대시보드 이동",
                    ["매출 대시보드", "상품 대시보드", "재고 대시보드"])
    st.divider()
    st.caption("데이터는 매일 아침 자동으로 갱신됩니다.")

orders = None  # 모든 탭이 data/*.json을 읽음 (카페24 토큰 불필요)


# ======================================================================
# 매출 대시보드
# ======================================================================
def render_sales(orders):
    st.title("매출 대시보드")

    # 매일 자동 생성된 sales.json 읽기 (토큰 불필요)
    try:
        d = load_data_json("sales.json")
    except Exception:
        st.info("아직 매출 데이터가 없어요. (data/sales.json 생성 필요 — GitHub Actions 실행)")
        return

    p = d["period"]
    st.caption(f'{p["start"]} ~ {p["end"]} (최근 {p["days"]}일) · 갱신 '
               + d["generated_at"][:16].replace("T", " "))

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

    # 방문자 CSV (상단 KPI의 유입수/전환율 + 유입·전환 분석에 사용)
    csv_file = st.file_uploader("방문자 CSV 업로드 (처음방문vs재방문 구매)",
                                type=["csv"], key="visitor_csv")
    adf = None
    유입수 = 전환율 = None
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
        유입수 = int(adf["방문수"].sum())
        if adf["방문수"].sum():
            전환율 = adf["구매건수"].sum() / adf["방문수"].sum() * 100

    # 1) 상단 KPI 6개 (카드)
    k = d["kpi"]
    cards = [
        ("총 매출", won_short(k["total_sales"]), ""),
        ("전환수", f'{k["conv_count"]:,}건', ""),
        ("평균 객단가", won(k["aov"]), ""),
        ("유입수", f"{유입수:,}" if 유입수 is not None else "—", "· CSV"),
        ("전환율", f"{전환율:.1f}%" if 전환율 is not None else "—", "· CSV"),
        ("환불율", f'{k["refund_rate"]:.1f}%', ""),
    ]
    kpi_html = '<div class="kpi-grid">' + "".join(
        f'<div class="kpi-card"><div class="kpi-label">{l} <span class="kpi-tag">{tag}</span></div>'
        f'<div class="kpi-val">{v}</div></div>' for l, v, tag in cards) + '</div>'
    st.markdown(kpi_html, unsafe_allow_html=True)
    if adf is None:
        st.caption("유입수·전환율은 위에서 방문자 CSV를 올리면 표시됩니다.")

    # 2~3) 신규 vs 재구매 비율 막대 + 직전 기간 등락 (JSON에 포함)
    st.subheader("신규 구매 vs 재구매")
    nr = d["new_vs_repeat"]
    new_sales, repeat_sales = nr["new_sales"], nr["repeat_sales"]
    tot = new_sales + repeat_sales
    new_pct = round(new_sales / tot * 100) if tot else 0
    rep_pct = 100 - new_pct

    def delta_span(v):
        if v is None:
            return '<span style="color:#9AA0AC;font-weight:600;">— 비교불가</span>'
        color = "#1D9E75" if v > 0 else ("#E5484D" if v < 0 else "#9AA0AC")
        arrow = "▲" if v > 0 else ("▼" if v < 0 else "─")
        return f'<span style="color:{color};font-weight:600;">{arrow} {abs(v):.1f}%</span>'

    pp = nr.get("prev_period", {})
    prev_label = f'직전 {p["days"]}일'
    if pp:
        prev_label += f' ({pp["start"][5:]}~{pp["end"][5:]})'
    st.markdown(f"""<div class="wcard">
      <div class="ratio-bar"><div style="width:{new_pct}%;background:#378ADD;"></div><div style="width:{rep_pct}%;background:#B5D4F4;"></div></div>
      <div class="ratio-stats">
        <div><div class="rs-label"><span class="rs-dot" style="background:#378ADD;"></span>신규 구매</div>
          <div class="rs-val">{won_short(new_sales)} <span class="rs-sub">{new_pct}%</span></div>
          {delta_span(nr.get("new_delta_pct"))} <span class="rs-sub">{prev_label} 대비</span></div>
        <div><div class="rs-label"><span class="rs-dot" style="background:#B5D4F4;"></span>재구매</div>
          <div class="rs-val">{won_short(repeat_sales)} <span class="rs-sub">{rep_pct}%</span></div>
          {delta_span(nr.get("repeat_delta_pct"))} <span class="rs-sub">{prev_label} 대비</span></div>
      </div>
    </div>""", unsafe_allow_html=True)

    # 4) 일별 매출 추이 (신규/재구매 누적) + 메모
    st.subheader("일별 매출 추이")
    st.caption("신규·재구매를 쌓아 보여줍니다. 급변한 날은 메모로 기록해두세요.")
    daily = d["daily"]
    date_list = [r["date"] for r in daily]
    new_vals = [r["new"] for r in daily]
    rep_vals = [r["repeat"] for r in daily]
    total_by_date = {r["date"]: r["new"] + r["repeat"] for r in daily}
    memos = load_memos()

    fig = go.Figure()
    fig.add_bar(x=date_list, y=new_vals, name="신규구매", marker_color="#378ADD")
    fig.add_bar(x=date_list, y=rep_vals, name="재구매", marker_color="#B5D4F4")
    fig.update_layout(barmode="stack", height=360, margin=dict(t=30, b=10, l=10, r=10),
                      legend=dict(orientation="h", y=1.12, x=0),
                      plot_bgcolor="white", yaxis=dict(gridcolor="#EEF1F5"))
    for ds in date_list:
        if ds in memos and memos[ds].strip():
            fig.add_annotation(x=ds, y=float(total_by_date[ds]),
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

    active = {dd: t for dd, t in sorted(memos.items()) if t.strip()}
    if active:
        for dd, t in active.items():
            a1, a2 = st.columns([6, 1])
            a1.markdown(f"📌 **{dd}** — {t}")
            if a2.button("삭제", key=f"del_{dd}", use_container_width=True):
                memos.pop(dd, None)
                save_memos(memos)
                st.rerun()
    else:
        st.caption("아직 기록된 메모가 없어요. 위에서 날짜를 고르고 메모를 추가해보세요.")

    # 5) 유입 · 전환 분석 (CSV 업로드 시)
    if adf is not None:
        st.divider()
        st.subheader("유입 · 전환 분석")
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
    try:
        d = load_data_json("product.json")
    except Exception:
        st.info("아직 상품 데이터가 없어요. (data/product.json 생성 필요 — GitHub Actions 실행)")
        return
    p = d["period"]
    st.caption(f'{p["start"]} ~ {p["end"]} (최근 {p["days"]}일) · 갱신 '
               + d["generated_at"][:16].replace("T", " "))

    cat_colors = {"캐리어": "#378ADD", "악세사리": "#1D9E75", "프로모션": "#E0A800",
                  "합구매 악세사리": "#16A085", "미분류": "#888780"}

    # ============================================================
    # 1. 카테고리별 분석
    # ============================================================
    st.header("1. 카테고리별 분석")
    by_amt = d["category"]["by_amount"]
    by_qty = d["category"]["by_qty"]

    cats = list(by_amt.keys())
    st.markdown("#### 카테고리별 주요 지표")
    cols = st.columns(len(cats)) if cats else [st]
    for col, c in zip(cols, cats):
        amt = by_amt.get(c, 0)
        qty = by_qty.get(c, 0)
        aov = amt / qty if qty else 0
        color = cat_colors.get(c, "#888780")
        col.markdown(
            f'<div style="border-left:4px solid {color};padding:6px 12px;margin-bottom:8px;">'
            f'<div style="font-size:14px;color:#444;font-weight:600;">{c}</div>'
            f'<div style="font-size:20px;font-weight:700;">{won_short(amt)}</div>'
            f'<div style="font-size:12px;color:#777;">수량 {qty:,}개 · 객단가 {won_short(aov)}</div>'
            f'</div>', unsafe_allow_html=True)

    big = pd.DataFrame({"매출": by_amt, "수량": by_qty}).fillna(0)
    big["수량"] = big["수량"].astype(int)
    big["객단가"] = (big["매출"] / big["수량"].replace(0, pd.NA)).fillna(0).round(0)
    cc1, cc2 = st.columns([1, 1.2])
    with cc1:
        f = px.pie(values=big["매출"], names=big.index, hole=0.55,
                   color=big.index, color_discrete_map=cat_colors)
        f.update_traces(textinfo="percent+label")
        f.update_layout(height=260, margin=dict(t=10, b=10, l=10, r=10),
                        legend=dict(orientation="h", y=-0.1))
        st.plotly_chart(f, use_container_width=True, config={"displayModeBar": False})
    with cc2:
        st.dataframe(big.style.format({"매출": "₩{:,.0f}", "수량": "{:,}", "객단가": "₩{:,.0f}"}),
                     use_container_width=True)

    cat_daily = d.get("cat_daily", {})
    if cat_daily:
        st.markdown("#### 최근 7일 매출 흐름 (카테고리별)")
        dates = sorted(cat_daily.keys())[-7:]
        all_cats = sorted({c for dt in dates for c in cat_daily[dt].keys()})
        fig = go.Figure()
        for c in all_cats:
            fig.add_trace(go.Scatter(
                x=dates, y=[cat_daily[dt].get(c, 0) for dt in dates],
                mode="lines+markers", name=c,
                line=dict(color=cat_colors.get(c, "#888780"), width=2)))
        fig.update_layout(height=320, margin=dict(t=20, b=10, l=10, r=10),
                          plot_bgcolor="white", yaxis=dict(gridcolor="#EEF1F5"),
                          legend=dict(orientation="h", y=1.15))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # ============================================================
    # 2. 오딧 캐리어 매출 분석
    # ============================================================
    st.divider()
    st.header("2. 오딧 캐리어 매출 분석")
    odit_daily = d.get("odit_daily", {})
    if not odit_daily:
        st.info("오딧 옵션별 판매 데이터가 아직 없어요. (build_data.py 갱신 후 데이터 재생성 필요)")
    else:
        all_dates = sorted({dt for m in odit_daily.values() for dt in m})
        yday = all_dates[-1] if all_dates else None
        dbefore = all_dates[-2] if len(all_dates) >= 2 else None
        st.markdown(f"#### 어제({yday}) 판매량 · 전일 대비 등락")

        def parse_key(k):
            grp, _, color = k.partition("·")
            return grp, color

        rows = []
        for k, m in odit_daily.items():
            grp, color = parse_key(k)
            y = m.get(yday, 0) if yday else 0
            b = m.get(dbefore, 0) if dbefore else 0
            diff = y - b
            arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "─")
            rows.append({"인치": grp, "색상": color, "어제": y, "그저께": b,
                         "등락": f"{arrow} {diff:+d}"})
        rdf = pd.DataFrame(rows)
        order = {g: i for i, g in enumerate(ODIT_GROUPS)}
        rdf["_o"] = rdf["인치"].map(lambda g: order.get(g, 99))
        rdf = rdf.sort_values(["_o", "색상"]).drop(columns="_o")

        def hl_updown(val):
            if isinstance(val, str) and val.startswith("▲"):
                return "color: #1D9E75; font-weight:600;"
            if isinstance(val, str) and val.startswith("▼"):
                return "color: #E5484D; font-weight:600;"
            return ""
        st.dataframe(rdf.style.map(hl_updown, subset=["등락"]),
                     hide_index=True, use_container_width=True)

        st.markdown("#### 옵션별 일자별 판매량")
        labels = sorted(odit_daily.keys(),
                        key=lambda k: (order.get(parse_key(k)[0], 99), parse_key(k)[1]))
        sel = st.multiselect("옵션 선택 (인치·색상)", labels,
                             default=labels[:3] if len(labels) >= 3 else labels)
        if sel:
            recent = sorted({dt for k in sel for dt in odit_daily[k]})[-14:]
            piv = pd.DataFrame(
                {k: [odit_daily[k].get(dt, 0) for dt in recent] for k in sel},
                index=recent)
            piv.index.name = "날짜"
            st.line_chart(piv)
            st.dataframe(piv, use_container_width=True)

    # ============================================================
    # 참고: 상품 CSV 퍼널 · 랭킹
    # ============================================================
    st.divider()
    st.header("참고 분석")

    with st.expander("상품 퍼널 분석 (조회→전환 CSV 업로드)"):
        product_csv = st.file_uploader("상품별 매출분석 CSV", type=["csv"], key="product_csv")
        if product_csv is not None:
            pdf = pd.read_csv(product_csv).rename(columns={
                "exposure_count": "조회수", "cart_count": "장바구니", "order_count": "전환수",
                "conversion_rate": "전환율", "order_amount": "매출",
                "order_to_cart_rate": "장바구니→주문율", "product_name": "상품명"})
            for c in ["조회수", "장바구니", "전환수", "전환율", "매출", "장바구니→주문율"]:
                if c in pdf.columns:
                    pdf[c] = pd.to_numeric(pdf[c], errors="coerce").fillna(0)
            pdf["대분류"] = pdf["상품명"].apply(lambda n: classify(n, "")[0])
            p1, p2, p3 = st.columns(3)
            p1.metric("총 조회수", f"{int(pdf['조회수'].sum()):,}")
            p2.metric("총 전환수", f"{int(pdf['전환수'].sum()):,}")
            ov = pdf["전환수"].sum() / pdf["조회수"].sum() * 100 if pdf["조회수"].sum() else 0
            p3.metric("전체 전환율", f"{ov:.2f}%")
            fig = px.scatter(pdf, x="조회수", y="전환율", size="매출", color="대분류",
                             hover_name="상품명", size_max=45, color_discrete_map=cat_colors)
            fig.update_layout(height=380, margin=dict(t=10, b=10, l=10, r=10),
                              legend=dict(orientation="h", y=-0.18))
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        else:
            st.caption("CSV를 올리면 조회·전환 퍼널 분석이 표시됩니다.")

    with st.expander("상품별 매출 순위 TOP 20"):
        rk = pd.DataFrame(d.get("ranking", []))
        if not rk.empty:
            rk = rk.rename(columns={"name": "상품명", "qty": "판매수량", "amount": "매출"})
            st.dataframe(rk.style.format({"판매수량": "{:,}", "매출": "₩{:,.0f}"}),
                         use_container_width=True, hide_index=True)


# ======================================================================
# 재고 대시보드
# ======================================================================
# ====================== 재고 파싱 헬퍼 ======================
ODIT_COLORS = ["화이트", "실버", "다크그레이", "블랙", "솔티블루",
               "펄스레드", "아이시핑크", "웻그린"]
ODIT_GROUPS = ["20인치", "24인치", "26인치", "29인치", "20인치 플랩"]


def parse_odit_option(opt):
    """옵션값에서 (인치그룹, 색상) 추출. 예: '오딧 플랩 20인치 아이시 핑크' -> ('20인치 플랩','아이시핑크')"""
    m = re.search(r"(\d+)\s*인치", opt or "")
    inch = m.group(1) + "인치" if m else None
    flap = "플랩" in (opt or "")
    norm = re.sub(r"\(.*?\)", "", opt or "").replace("아이시 핑크", "아이시핑크")
    color = next((c for c in ODIT_COLORS if c in norm), None)
    group = "20인치 플랩" if flap else inch
    return group, color


def _sell(n):
    return f"{int(n)}일 후" if isinstance(n, (int, float)) else "—"


def render_inventory(orders):
    st.title("재고 대시보드")
    try:
        d = load_data_json("inventory.json")
    except Exception:
        st.info("아직 재고 데이터가 없어요. (data/inventory.json 생성 필요 — GitHub Actions 실행)")
        return
    items = d["items"]
    st.caption("갱신 " + d["generated_at"][:16].replace("T", " ")
               + f" · 전체 {d['summary'].get('total', len(items)):,}개 품목")

    # ===== 1. 오딧 재고 현황 (모든 오딧 페이지 합산, 인치별 묶음) =====
    st.header("1. 오딧 재고 현황")
    st.caption("오딧이 들어간 모든 페이지(248·270·184·세트·기획전 등)의 같은 옵션 재고를 합산. "
               "재고·판매량은 합계, 품절일은 합산 기준 (🔴 = 30일 기준 14일 내 소진). "
               "임직원·테스트·타모델·예약 상품은 제외.")

    _EXCLUDE = ["임직원", "테스트", "POP-UP", "PRE-ORDER", "몬딱", "쿼디"]

    def _is_odit(it):
        t = (it.get("product", "") or "") + " " + (it.get("option", "") or "")
        return ("오딧" in t) and not any(x in t for x in _EXCLUDE)

    # (인치그룹, 색상) -> 재고·판매량 합산
    agg = {}
    for it in items:
        if not _is_odit(it):
            continue
        g, c = parse_odit_option(it.get("option", ""))
        if not (g and c):
            continue
        a = agg.setdefault((g, c), {"stock": 0, "d1": 0, "d7": 0.0, "d30": 0.0, "d90": 0.0})
        a["stock"] += it.get("stock", 0)
        a["d1"] += it.get("daily_1", 0)
        a["d7"] += it.get("daily_7", 0)
        a["d30"] += it.get("daily_30", 0)
        a["d90"] += it.get("daily_90", 0)

    def _so(stock, rate):
        return round(stock / rate) if rate and rate > 0 else None

    for group in ODIT_GROUPS:
        st.markdown(f"#### {group}")
        rows = []
        for color in ODIT_COLORS:
            a = agg.get((group, color))
            if a and (a["stock"] or a["d30"]):
                s7 = _so(a["stock"], a["d7"])
                s30 = _so(a["stock"], a["d30"])
                s90 = _so(a["stock"], a["d90"])
                urgent = isinstance(s30, (int, float)) and s30 <= 14
                rows.append({
                    "색상": ("🔴 " if urgent else "") + color,
                    "재고": a["stock"],
                    "어제": round(a["d1"], 1),
                    "7일": round(a["d7"], 2),
                    "30일": round(a["d30"], 2),
                    "90일": round(a["d90"], 2),
                    "품절(7일속도)": _sell(s7),
                    "품절(30일속도)": _sell(s30),
                    "품절(90일속도)": _sell(s90),
                })
            else:
                rows.append({"색상": color, "재고": "", "어제": "", "7일": "", "30일": "",
                             "90일": "", "품절(7일속도)": "", "품절(30일속도)": "", "품절(90일속도)": ""})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    # ===== 2. 오딧 재고 관리 (248 / 270 / 184 페이지별) =====
    st.divider()
    st.header("2. 오딧 재고 관리")
    st.caption("248·270(플랩)·184 페이지별로 품절 임박 순 정렬. "
               "여유재고(다른 오딧 페이지의 같은 옵션) 반영 품절일과, 3번에서 등록한 입고 반영 품절일을 함께 봅니다.")

    EXCLUDE = ["임직원", "테스트", "POP-UP", "PRE-ORDER", "몬딱", "쿼디"]

    def is_odit_supply(it):
        text = (it.get("product", "") or "") + " " + (it.get("option", "") or "")
        if any(x in text for x in EXCLUDE):
            return False
        return "오딧" in text

    def keyof(it):
        g, c = parse_odit_option(it.get("option", ""))
        flap = "플랩" in (it.get("option", "") or "")
        inch = re.search(r"(\d+)", g or "").group(1) if g else None
        return (inch, flap, c)

    # 전체 오딧 재고 합 (여유재고 계산용)
    total_stock = {}
    src_map = {}
    for it in items:
        if not is_odit_supply(it) or it.get("stock", 0) <= 0:
            continue
        k = keyof(it)
        if not (k[0] and k[2]):
            continue
        total_stock[k] = total_stock.get(k, 0) + it["stock"]
        src_map.setdefault(k, {})
        pno_src = str(it.get("product_no"))
        src_map[k][pno_src] = src_map[k].get(pno_src, 0) + it["stock"]

    restocks = load_restocks()
    today = date.today()

    def sort_key(it):
        s = it.get("sellout_30")
        return s if isinstance(s, (int, float)) else 10**9

    def hl(row):
        s = row["현재 품절"]
        urgent = isinstance(s, str) and s.endswith("일 후") and int(s.replace("일 후", "")) <= 14
        return ["background-color: #FCE8E9" if urgent else "" for _ in row]

    PAGE_NAMES = {"248": "오딧 캐리어", "270": "오딧 플랩 캐리어", "184": "세트할인 오딧/플랩 캐리어"}
    for pno in ["248", "270", "184"]:
        sub = [it for it in items if str(it.get("product_no")) == pno and it.get("stock", 0) > 0]
        if not sub:
            continue
        sub.sort(key=sort_key)
        st.markdown(f"#### {PAGE_NAMES.get(pno, '')} (no.{pno})")
        rows = []
        for it in sub:
            k = keyof(it)
            vel = it.get("daily_30", 0)
            free = max(total_stock.get(k, 0) - it["stock"], 0)
            free_sell = round((it["stock"] + free) / vel) if vel > 0 else None
            # 입고: 옵션키로 찾아 이 페이지(pno)에 배분된 수량만 반영
            okey = f"{k[0]}|{k[1]}|{k[2]}" if (k[0] and k[2]) else None
            entries = restocks.get(okey, []) if okey else []
            page_list = [{"date": e["date"], "qty": e.get("alloc", {}).get(pno, 0)}
                         for e in entries]
            page_list = [e for e in page_list if e["qty"] > 0]
            in_sell = project_sellout(it["stock"], vel, page_list, today)
            sched = ", ".join(f'{e["date"][5:]}·{e["qty"]}개'
                              for e in sorted(page_list, key=lambda e: e["date"])) or "—"
            # 보충처: 같은 옵션을 가진 다른 페이지(현재 페이지 제외) 상위 3곳
            srcs = [(p, q) for p, q in src_map.get(k, {}).items() if p != pno and q > 0]
            srcs.sort(key=lambda x: -x[1])
            src_txt = ", ".join(f"no.{p}({q})" for p, q in srcs[:3]) if srcs else "—"
            rows.append({
                "옵션": it["option"], "현재고": it["stock"], "30일판매": vel,
                "현재 품절": _sell(it.get("sellout_30")),
                "여유재고": free, "여유 반영 품절": _sell(free_sell),
                "보충처(상위)": src_txt,
                "입고 배분": sched, "입고 반영 품절": _sell(in_sell),
            })
        st.dataframe(pd.DataFrame(rows).style.apply(hl, axis=1),
                     hide_index=True, use_container_width=True)

    st.caption("※ 여유재고는 다른 오딧 페이지(세트·기획전 등)의 같은 옵션 재고 합이라 실제 이동 가능량은 "
               "운영 상황에 따라 다를 수 있어요. 입고 예정·입고 반영 품절은 3번에서 등록한 일정 기준입니다.")

    # ===== 3. 입고 예정 일정 =====
    render_restock_section(items)


# ====================== 입고 예정 일정 (JSONBin 저장) ======================
# JSONBin에서 입고일정용 Bin을 새로 만들어 아래에 채우세요(매출 메모와 별도 Bin 권장).
JSONBIN_RESTOCK = {
    "bin_id": "6a38fb18f5f4af5e291be484",
    "api_key": "$2a$10$Ma9Mewe6lm2OO9cUDJ9hfOZ6N0R7KvD4XCc1.oyuWzTH0jsGsDUdy",
}


def load_restocks():
    cfg = JSONBIN_RESTOCK
    if cfg["bin_id"] and cfg["api_key"]:
        try:
            r = requests.get(f'https://api.jsonbin.io/v3/b/{cfg["bin_id"]}/latest',
                             headers={"X-Master-Key": cfg["api_key"]}, timeout=10)
            return r.json().get("record", {}).get("restocks", {}) or {}
        except Exception:
            return {}
    return st.session_state.get("_restocks", {})


def save_restocks(data):
    cfg = JSONBIN_RESTOCK
    if cfg["bin_id"] and cfg["api_key"]:
        try:
            requests.put(f'https://api.jsonbin.io/v3/b/{cfg["bin_id"]}',
                         headers={"X-Master-Key": cfg["api_key"],
                                  "Content-Type": "application/json"},
                         json={"restocks": data}, timeout=10)
        except Exception as e:
            st.warning("입고 일정 저장 실패: " + str(e)[:100])
    st.session_state["_restocks"] = data


def project_sellout(stock, rate, restock_list, today):
    """현재고를 판매속도로 소진하다가 입고일에 입고량을 더해 최종 품절일(오늘로부터 N일)을 계산."""
    if not rate or rate <= 0:
        return None
    events = []
    for e in restock_list:
        try:
            dd = datetime.strptime(e["date"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue
        q = float(e.get("qty", 0))
        if q > 0:
            events.append(((dd - today).days, q))
    events.sort()
    cur = float(stock)
    t = 0.0
    for td, q in events:
        if td < 0:
            continue
        if td == 0:
            cur += q
            continue
        if cur / rate < (td - t):       # 입고 전에 소진
            return round(t + cur / rate)
        cur -= rate * (td - t)          # 입고일까지 판매
        cur += q                        # 입고 반영
        t = td
    return round(t + cur / rate)


def render_restock_section(items):
    st.divider()
    st.header("3. 입고 예정 일정")
    st.caption("입고는 옵션 단위로 한 번에 들어오고, 248·270·184 페이지로 나눠 배분합니다. "
               "배분한 수량은 2번 각 페이지의 '입고 반영 품절'에 반영돼요.")
    cfg = JSONBIN_RESTOCK
    if not (cfg["bin_id"] and cfg["api_key"]):
        st.info("⚠ JSONBin 설정이 비어 있어 입고 일정이 지금 세션에만 임시 저장됩니다. "
                "코드 상단 JSONBIN_RESTOCK에 bin_id·api_key를 넣으면 저장되고 팀과 공유돼요.")

    restocks = load_restocks()
    today = date.today()
    targets = [it for it in items if str(it.get("product_no")) in ("248", "270", "184")]
    if not targets:
        st.caption("248·270·184 상품 데이터가 없습니다.")
        return

    # 옵션(인치그룹·색상) 목록 — 세 페이지에 존재하는 모든 옵션을 합쳐서 중복 제거
    opt_choices = {}   # 표시라벨 -> opt_key("그룹|플랩|색상")
    for it in targets:
        g, c = parse_odit_option(it.get("option", ""))
        flap = "플랩" in (it.get("option", "") or "")
        if not (g and c):
            continue
        okey = f"{g}|{flap}|{c}"
        label = f"{g} {c}"
        opt_choices[label] = okey
    opt_labels = sorted(opt_choices.keys())

    # 입고 일정 추가: 한 줄 (옵션·입고일·총수량·248·270·184·추가)
    st.markdown("**입고 일정 추가**")
    st.caption("옵션 · 입고일 · 총수량 · 248배분 · 270배분 · 184배분")
    c1, c2, c3, c4, c5, c6, c7 = st.columns([2.4, 1.6, 1, 0.9, 0.9, 0.9, 0.9])
    sel_label = c1.selectbox("옵션", opt_labels, key="rs_opt", label_visibility="collapsed")
    in_date = c2.date_input("입고일", today, key="rs_date", label_visibility="collapsed")
    in_qty = c3.number_input("총수량", min_value=0, value=0, step=1, key="rs_qty",
                             label_visibility="collapsed")
    a248 = c4.number_input("248", min_value=0, value=0, step=1, key="rs_a248",
                           label_visibility="collapsed")
    a270 = c5.number_input("270", min_value=0, value=0, step=1, key="rs_a270",
                           label_visibility="collapsed")
    a184 = c6.number_input("184", min_value=0, value=0, step=1, key="rs_a184",
                           label_visibility="collapsed")
    if c7.button("추가", use_container_width=True):
        okey = opt_choices[sel_label]
        entry = {"date": str(in_date), "qty": int(in_qty),
                 "alloc": {"248": int(a248), "270": int(a270), "184": int(a184)}}
        restocks.setdefault(okey, []).append(entry)
        save_restocks(restocks)
        st.rerun()
    alloc_sum_hint = "배분 합계가 총 입고수량과 다르면, 표시는 되지만 페이지 반영은 배분수량 기준입니다."
    st.caption(alloc_sum_hint)

    # 입고 일정 키 -> 사람이 읽는 옵션명
    vc_name = {it["variant_code"]: it.get("option", "") for it in targets}

    def okey_label(okey):
        parts = okey.split("|")
        if len(parts) == 3:           # 새 형식: 그룹|플랩|색상
            return f"{parts[0]} {parts[2]}"
        # 옛 형식: variant_code 로 저장된 경우 → 실제 옵션명으로 변환
        return vc_name.get(okey, okey)

    # 등록된 입고 일정 (옵션별 · 페이지 배분)
    rows = []
    for okey, lst in restocks.items():
        opt_name = okey_label(okey)
        for e in sorted(lst, key=lambda e: e.get("date", "")):
            al = e.get("alloc", {})
            rows.append({
                "옵션": opt_name, "입고일": e.get("date", ""),
                "총 입고": e.get("qty", 0),
                "248": al.get("248", 0), "270": al.get("270", 0), "184": al.get("184", 0),
            })
    if rows:
        rows.sort(key=lambda r: r["입고일"])
        st.markdown("**등록된 입고 일정**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

        with st.expander("입고 일정 삭제"):
            for okey, lst in list(restocks.items()):
                opt_name = okey_label(okey)
                for idx, e in enumerate(sorted(lst, key=lambda e: e.get("date", ""))):
                    al = e.get("alloc", {})
                    a1, a2 = st.columns([5, 1])
                    a1.write(f'{opt_name} — {e.get("date","")} · 총 {e.get("qty",0)}개 '
                             f'(248:{al.get("248",0)} / 270:{al.get("270",0)} / 184:{al.get("184",0)})')
                    if a2.button("삭제", key=f"rs_del_{okey}_{idx}"):
                        restocks[okey].remove(e)
                        if not restocks[okey]:
                            del restocks[okey]
                        save_restocks(restocks)
                        st.rerun()
    else:
        st.caption("아직 등록된 입고 일정이 없어요. 위에서 옵션·입고일·수량·배분을 넣고 추가하세요.")


# ====================== 라우팅 ======================
if page == "매출 대시보드":
    render_sales(orders)
elif page == "상품 대시보드":
    render_product(orders)
else:
    render_inventory(orders)
