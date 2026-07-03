"""상품 분류 기준.

대분류 : 캐리어 / 악세사리 / 프로모션 / 합구매 악세사리
  - 캐리어        : 캐리어 본품 (모델: 오딧 / 오딧 플랩 / 몬딱 / 쿼디)
  - 악세사리      : 일반 악세사리 (커버·파우치·월렛 등)
  - 프로모션      : 특정 이벤트 전용 페이지 (별도 트래킹용)
  - 합구매 악세사리 : "캐리어 구매 시" 특가로 파는 악세사리 (가격·판매량 별도 집계)

중분류 : 캐리어는 모델명, 그 외는 상품명
모델태그: 악세사리 중 특정 모델 전용품에 '오딧/몬딱/쿼디' 태그
임직원  : 상품명에 [임직원] 포함 여부 (분석엔 포함, 표시만 구분)

분류 우선순위 (위에서부터 먼저 적용)
 1) 프로모션 지정목록(PROMO_PRODUCTS)에 있으면        → 프로모션
 2) 이름/옵션에 '캐리어 구매' 표시가 있으면            → 합구매 악세사리
 3) 프로모션 키워드(PROMO_KW)가 있으면                → 프로모션
 4) 캐리어 모델명 또는 '캐리어'가 잡히면               → 캐리어
 5) 악세사리 키워드(ACC_KW)가 있으면                  → 악세사리
 6) 그 외                                          → 미분류
"""

# ── 프로모션으로 강제 지정할 상품 (이름의 일부만 적어도 됨) ───────────────
# 새 이벤트 페이지가 생기면 이 목록에 한 줄씩 추가하세요.
PROMO_PRODUCTS = [
    "ICY PINK 런칭 프로모션",
]

# 프로모션으로 자동 인식할 키워드 (대소문자 무시)
PROMO_KW = ["프로모션", "런칭", "icy pink", "아이시핑크"]

# 합구매(캐리어 구매 시 특가) 표시
COMBO_KW = ["캐리어 구매", "캐리어구매"]

# 일반 악세사리 키워드
ACC_KW = ["커버", "파우치", "패키지", "벨트", "월렛", "키링", "워시백",
          "오거나이저", "키퍼", "백", "케이스", "택", "태그", "시리즈", "모아보기"]


def detect_model(text):
    """텍스트에서 캐리어 모델명을 판별. 플랩은 오딧과 별도."""
    if not text:
        return None
    t = text.lower()
    if "플랩" in text or "flap" in t:
        return "오딧 플랩"
    if "몬딱" in text or "monddak" in t:
        return "몬딱"
    if "쿼디" in text or "quady" in t or "quody" in t:
        return "쿼디"
    if "오딧" in text or "odit" in t:
        return "오딧"
    return None


def classify(product, option):
    """(대분류, 중분류, 모델태그, 임직원여부) 반환."""
    product = product or ""
    option = option or ""
    text = product + " " + option
    low = text.lower()
    is_staff = "[임직원]" in product
    model = detect_model(option) or detect_model(product)

    # 1) 프로모션 지정목록 (가장 우선)
    if any(p and p in product for p in PROMO_PRODUCTS):
        return "프로모션", product, model, is_staff

    # 2) 합구매 악세사리 ('캐리어 구매 시' 특가)
    if any(k in text for k in COMBO_KW):
        return "합구매 악세사리", product, model, is_staff

    # 3) 프로모션 키워드
    if any(k in low for k in PROMO_KW):
        return "프로모션", product, model, is_staff

    # 4) 캐리어 (모델 감지 또는 '캐리어')
    if model or "캐리어" in product:
        return "캐리어", (model or "미상"), None, is_staff

    # 5) 일반 악세사리
    if any(k in text for k in ACC_KW):
        return "악세사리", product, model, is_staff

    return "미분류", product, model, is_staff
