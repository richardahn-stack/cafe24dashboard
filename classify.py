"""상품 분류 기준.

대분류 : 캐리어 / 악세사리
중분류 : 캐리어는 모델(오딧 / 오딧 플랩 / 몬딱 / 쿼디), 악세사리는 상품명
모델태그: 악세사리 중 특정 모델 전용품에 '오딧/몬딱/쿼디' 태그
임직원  : 상품명에 [임직원] 포함 여부 (분석엔 포함, 표시만 구분)

규칙
 1) 상품명 또는 옵션에 악세사리 키워드가 있으면 → 악세사리
 2) 아니고 모델명/'캐리어'가 잡히면 → 캐리어
 3) 모델은 옵션 우선, 없으면 상품명에서 판별 (영문 별칭 포함)
"""

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
    is_staff = "[임직원]" in product
    is_acc = any(k in (product + " " + option) for k in ACC_KW)
    model = detect_model(option) or detect_model(product)

    if is_acc:
        return "악세사리", product, model, is_staff
    if model or "캐리어" in product:
        return "캐리어", (model or "미상"), None, is_staff
    return "미분류", product, model, is_staff
