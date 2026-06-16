# 카페24 자사몰 매출 대시보드

일/월 매출 추이와 상품별 매출 순위를 보여주는 Streamlit 대시보드입니다.

## 구성

| 파일 | 역할 |
|------|------|
| `config.py` | 환경변수·API 설정 로드 |
| `get_token.py` | 최초 1회 OAuth 토큰 발급 |
| `cafe24_client.py` | API 클라이언트 (토큰 자동 갱신 + 주문 수집) |
| `app.py` | Streamlit 대시보드 |

## 설치

```bash
pip install -r requirements.txt
```

## 1. 환경변수 설정

`.env.example`을 `.env`로 복사하고 값을 채웁니다.

```bash
cp .env.example .env
```

```
CAFE24_MALL_ID=myshop          # 쇼핑몰 주소가 myshop.cafe24.com 이면 myshop
CAFE24_CLIENT_ID=...           # 발급받은 client id
CAFE24_CLIENT_SECRET=...       # 발급받은 secret key
CAFE24_REDIRECT_URI=...        # 카페24 앱 설정에 등록한 redirect uri
```

> ⚠️ `redirect_uri`는 카페24 개발자센터 앱 설정의 'Redirect URI'와 **정확히 일치**해야 합니다.

## 2. 토큰 발급 (최초 1회)

```bash
python get_token.py
```

출력된 URL을 브라우저에서 열고 승인 → 이동된 주소의 `?code=...` 값을
터미널에 붙여넣으면 `token.json`이 생성됩니다.
(access token은 2시간, refresh token은 2주 유효 — 이후 대시보드가 자동 갱신합니다.)

## 3. 대시보드 실행

```bash
streamlit run app.py
```

브라우저에서 사이드바의 기간을 선택하고 **데이터 불러오기**를 누르세요.

## 참고 / 커스터마이징

- **매출 필드**: 카페24 응답의 금액 필드명(`actual_order_amount`,
  `payment_amount` 등)은 쇼핑몰 설정·API 버전에 따라 다를 수 있습니다.
  대시보드 하단 **🔍 원본 주문 데이터** 펼치기에서 실제 필드명을 확인하고
  `app.py`의 해당 부분을 조정하세요.
- **API 버전**: `config.py`의 `API_VERSION`을 개발자센터 문서의 최신
  버전으로 맞추는 것을 권장합니다.
- **취소/환불 제외**: 순매출만 보려면 `app.py`에서 주문 상태
  (`order_status`/`canceled` 등)로 필터링을 추가하세요.
- 카페24 API 명세는 자주 갱신되므로, 엔드포인트·파라미터는
  developers.cafe24.com 의 최신 문서로 확인하는 것이 안전합니다.
