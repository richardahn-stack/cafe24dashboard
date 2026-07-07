"""
MKT 그로스 분석 탭.
공개 구글 스프레드시트(링크 공개)를 CSV로 불러와 분석한다.
- 시트를 '링크가 있는 모든 사용자에게 공개(보기)'로 설정하면 인증 없이 읽을 수 있다.
- 시트 URL을 넣으면 자동으로 CSV 내보내기 주소로 변환해 읽는다.
"""
import re
import pandas as pd
import streamlit as st


def _to_csv_url(url: str) -> str | None:
    """구글 시트 공유 URL → CSV 내보내기 URL 로 변환.
    예: https://docs.google.com/spreadsheets/d/<ID>/edit#gid=0
      → https://docs.google.com/spreadsheets/d/<ID>/export?format=csv&gid=0
    """
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not m:
        return None
    sheet_id = m.group(1)
    gid = "0"
    g = re.search(r"[#&?]gid=([0-9]+)", url)
    if g:
        gid = g.group(1)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


@st.cache_data(ttl=300, show_spinner="구글 시트를 불러오는 중...")
def load_sheet(csv_url: str) -> pd.DataFrame:
    return pd.read_csv(csv_url)


def render_mkt_tab():
    st.title("MKT 그로스 분석")
    st.caption("공개 구글 스프레드시트를 연동해 마케팅 데이터를 분석합니다.")

    # 시트 URL 입력 (기본값은 secrets 에 저장해두면 자동 사용)
    default_url = ""
    try:
        default_url = st.secrets.get("MKT_SHEET_URL", "")
    except Exception:
        pass

    url = st.text_input(
        "구글 시트 URL",
        value=default_url,
        placeholder="https://docs.google.com/spreadsheets/d/.../edit#gid=0",
        help="시트를 '링크가 있는 모든 사용자 - 보기'로 공유한 뒤 주소를 붙여넣으세요.")

    if not url:
        st.info("구글 시트 URL을 입력하면 데이터를 불러옵니다.\n\n"
                "① 구글 시트 우측 상단 '공유' → '링크가 있는 모든 사용자'(보기 권한)로 설정\n"
                "② 주소창의 URL을 복사해 위에 붙여넣기")
        return

    csv_url = _to_csv_url(url)
    if not csv_url:
        st.error("구글 시트 URL 형식이 아니에요. /spreadsheets/d/... 형태의 주소를 넣어주세요.")
        return

    try:
        df = load_sheet(csv_url)
    except Exception as e:
        st.error(f"시트를 불러오지 못했어요: {e}\n\n"
                 "시트가 '링크가 있는 모든 사용자에게 공개(보기)'로 설정됐는지 확인하세요.")
        return

    if df.empty:
        st.warning("시트에 데이터가 없어요.")
        return

    # ── 우선: 불러온 데이터 확인 (컬럼 구조 파악용) ──
    st.success(f"불러오기 완료 · {len(df):,}행 × {len(df.columns)}열")
    st.markdown("**컬럼 목록**")
    st.write(list(df.columns))

    st.markdown("**데이터 미리보기**")
    st.dataframe(df.head(50), use_container_width=True)

    with st.expander("각 컬럼 요약 정보"):
        info = pd.DataFrame({
            "컬럼": df.columns,
            "타입": [str(t) for t in df.dtypes],
            "비어있지 않은 값": [int(df[c].notna().sum()) for c in df.columns],
            "예시 값": [str(df[c].dropna().iloc[0]) if df[c].notna().any() else "" for c in df.columns],
        })
        st.dataframe(info, hide_index=True, use_container_width=True)

    st.caption("이 컬럼 구조를 확인한 뒤, 원하는 분석(추이·전환·광고비 대비 매출 등)을 추가할 수 있어요.")
