"""
네이버 스마트스토어 판매량 섹션 (엑셀 업로드 기반, 판매량 전용).
- 스마트스토어 '주문조회' 엑셀을 업로드 → 파싱 → 집계 → JSONBin 저장(계속 유지).
- 매출/객단가는 이 엑셀에 금액이 없어 제공하지 않음 (판매량 기준).
- 캐리어 커버·벨트 등은 네이버 섹션에서 '악세사리'로 분리 처리.
"""
import io
import re
import json
import datetime

import pandas as pd
import requests
import streamlit as st

from classify import classify

# ---- JSONBin 저장 설정 ----
# ※ 재입고와 다른 별도 bin 을 만들어 bin_id 를 넣으세요 (같은 api_key 사용 가능).
JSONBIN_NAVER = {
    "bin_id": "",   # ← jsonbin.io 에서 새 bin 생성 후 id 입력
    "api_key": "$2a$10$Ma9Mewe6lm2OO9cUDJ9hfOZ6N0R7KvD4XCc1.oyuWzTH0jsGsDUdy",
}

ODIT_COLORS = ["화이트", "실버", "다크그레이", "블랙", "솔티블루",
               "펄스레드", "아이시핑크", "웻그린"]
ODIT_GROUPS = ["20인치 플랩", "29인치", "26인치", "24인치", "20인치"]

# 캐리어로 오분류되기 쉬운 악세사리 키워드 (네이버 섹션 전용 재분류)
ACC_OVERRIDE = ["커버", "벨트", "이너프백", "보스턴백", "파우치", "스트랩",
                "오거나이저", "월렛", "키링", "워시백", "네임택", "택", "케이스"]

VALID_EXCLUDE_STATUS = ("취소", "미결제취소", "반품", "결제대기")
VALID_EXCLUDE_CLAIM = ("취소완료", "반품요청", "수거완료", "수거중", "반품완료")


# ---------- 파싱 ----------
def parse_naver_excel(file_bytes):
    """스마트스토어 주문조회 엑셀(bytes) → 집계 dict."""
    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, header=0)
    needed = {"주문상태", "클레임상태", "상품명", "판매옵션정보", "수량", "주문일시"}
    if not needed.issubset(set(df.columns)):
        raise ValueError("스마트스토어 '주문조회' 형식이 아니에요. "
                         "(필요 컬럼: 주문상태·상품명·판매옵션정보·수량 등)")

    def is_valid(row):
        stt = str(row.get("주문상태"))
        claim = str(row.get("클레임상태"))
        if stt in VALID_EXCLUDE_STATUS:
            return False
        if claim in VALID_EXCLUDE_CLAIM:
            return False
        return True

    df["_valid"] = df.apply(is_valid, axis=1)
    valid = df[df["_valid"]].copy()

    def qty(row):
        try:
            return int(float(row.get("수량") or 0))
        except Exception:
            return 0

    def cat_model(row):
        name = str(row.get("상품명") or "")
        opt = str(row.get("판매옵션정보") or "")
        c, m = classify(name, opt)[0], classify(name, opt)[1]
        # 네이버 전용: 캐리어인데 커버·벨트 등이면 악세사리로 재분류
        text = name + " " + opt
        if c == "캐리어" and any(k in text for k in ACC_OVERRIDE):
            c = "악세사리"
        return c, m

    def odit_sku(row):
        name = str(row.get("상품명") or "")
        opt = str(row.get("판매옵션정보") or "")
        text = name + " " + opt
        if "오딧" not in text:
            return None
        c = classify(name, opt)[0]
        if c != "캐리어":
            return None
        if any(k in text for k in ACC_OVERRIDE):
            return None
        if "커버" in text:
            return None
        m = re.search(r"(\d+)\s*인치", text)
        inch = f"{m.group(1)}인치" if m else None
        color = next((col for col in ODIT_COLORS if col in text), None)
        if "플랩" in text:
            inch = "20인치 플랩"
        if inch and color:
            return f"{inch}·{color}"
        return None

    cat_qty = {}
    model_qty = {}
    sku_qty = {}
    prod_qty = {}
    for _, row in valid.iterrows():
        q = qty(row)
        if q <= 0:
            continue
        c, m = cat_model(row)
        cat_qty[c] = cat_qty.get(c, 0) + q
        if c == "캐리어":
            model_qty[m] = model_qty.get(m, 0) + q
        sku = odit_sku(row)
        if sku:
            sku_qty[sku] = sku_qty.get(sku, 0) + q
        pname = str(row.get("상품명") or "").replace("[리드볼트] ", "").strip()
        prod_qty[pname] = prod_qty.get(pname, 0) + q

    # 기간
    dts = pd.to_datetime(valid["주문일시"], errors="coerce").dropna()
    period = {"start": str(dts.min().date()) if len(dts) else "",
              "end": str(dts.max().date()) if len(dts) else ""}

    return {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "period": period,
        "total_valid_qty": int(valid.apply(qty, axis=1).sum()),
        "excluded_rows": int((~df["_valid"]).sum()),
        "cat_qty": cat_qty,
        "model_qty": model_qty,
        "sku_qty": sku_qty,
        "prod_qty": dict(sorted(prod_qty.items(), key=lambda x: -x[1])[:50]),
    }


# ---------- JSONBin 저장/불러오기 ----------
def load_naver():
    cfg = JSONBIN_NAVER
    if cfg["bin_id"] and cfg["api_key"]:
        try:
            r = requests.get(f'https://api.jsonbin.io/v3/b/{cfg["bin_id"]}/latest',
                             headers={"X-Master-Key": cfg["api_key"]}, timeout=10)
            return r.json().get("record", {}).get("naver", None)
        except Exception:
            return None
    return st.session_state.get("_naver_data")


def save_naver(data):
    cfg = JSONBIN_NAVER
    if cfg["bin_id"] and cfg["api_key"]:
        try:
            requests.put(f'https://api.jsonbin.io/v3/b/{cfg["bin_id"]}',
                         headers={"X-Master-Key": cfg["api_key"],
                                  "Content-Type": "application/json"},
                         json={"naver": data}, timeout=10)
        except Exception as e:
            st.warning("네이버 데이터 저장 실패: " + str(e)[:100])
    st.session_state["_naver_data"] = data


# ---------- 화면 ----------
def render_naver_section():
    st.header("🟢 네이버 스마트스토어 판매 (판매량)")

    with st.expander("📤 주문조회 엑셀 업로드 (최신 데이터로 갱신)"):
        st.caption("스마트스토어 → 주문조회에서 받은 엑셀(.xlsx)을 올리면 판매량이 갱신돼요. "
                   "이 파일엔 금액이 없어 판매량만 집계해요.")
        up = st.file_uploader("주문조회 엑셀", type=["xlsx"], key="naver_upload")
        if up is not None:
            try:
                data = parse_naver_excel(up.read())
                save_naver(data)
                st.success(f"업로드 완료 · 기간 {data['period']['start']}~{data['period']['end']} · "
                           f"실판매 {data['total_valid_qty']:,}개 (제외 {data['excluded_rows']}행)")
            except Exception as e:
                st.error("파싱 실패: " + str(e)[:200])

    data = load_naver()
    if not data:
        st.info("아직 업로드된 네이버 데이터가 없어요. 위에서 주문조회 엑셀을 올려주세요.")
        st.divider()
        return

    st.caption(f"기간 {data['period']['start']} ~ {data['period']['end']} · "
               f"실판매 {data['total_valid_qty']:,}개 · "
               f"갱신 {data.get('generated_at','')[:16]}")

    # 카테고리 · 모델
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**카테고리별 판매량**")
        cat = data.get("cat_qty", {})
        cdf = pd.DataFrame([{"카테고리": k, "판매량": v}
                            for k, v in sorted(cat.items(), key=lambda x: -x[1])])
        if not cdf.empty:
            st.dataframe(cdf.style.format({"판매량": "{:,}개"}),
                         hide_index=True, use_container_width=True)
    with c2:
        st.markdown("**캐리어 모델별 판매량**")
        mdl = data.get("model_qty", {})
        mdf = pd.DataFrame([{"모델": k, "판매량": v}
                           for k, v in sorted(mdl.items(), key=lambda x: -x[1])])
        if not mdf.empty:
            st.dataframe(mdf.style.format({"판매량": "{:,}개"}),
                         hide_index=True, use_container_width=True)

    # 오딧 SKU 표 (인치 × 색상)
    st.markdown("**오딧 캐리어 SKU 판매량 (인치 × 색상)**")
    sku = data.get("sku_qty", {})
    grid = []
    for inch in ODIT_GROUPS:
        row = {"인치": inch.replace("20인치 플랩", "플랩")}
        for color in ODIT_COLORS:
            row[color] = sku.get(f"{inch}·{color}", 0)
        grid.append(row)
    sdf = pd.DataFrame(grid)
    total_sku = sum(sku.values())
    if total_sku:
        st.dataframe(sdf.style.format({c: "{:,}" for c in ODIT_COLORS}),
                     hide_index=True, use_container_width=True)
        st.caption(f"오딧 SKU 합계 {total_sku:,}개")
    else:
        st.caption("오딧 SKU 데이터가 없어요.")

    # 상품 순위
    st.markdown("**상품별 판매량 순위 (Top)**")
    prod = data.get("prod_qty", {})
    pdf = pd.DataFrame([{"상품명": k, "판매량": v} for k, v in prod.items()])
    if not pdf.empty:
        st.dataframe(pdf.head(20).style.format({"판매량": "{:,}개"}),
                     hide_index=True, use_container_width=True)
    st.caption("※ 커버·벨트 등 캐리어 악세사리는 '악세사리'로 분류했어요. 매출·객단가는 이 엑셀에 없어 제외했어요.")
    st.divider()
