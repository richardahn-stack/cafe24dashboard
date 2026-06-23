"""
최초 1회 실행하여 access_token / refresh_token을 발급받습니다.

    python get_token.py

발급된 토큰은 token.json에 저장되고, 이후 대시보드가 자동으로 갱신합니다.
"""
import base64
import json

import requests

from config import (API_BASE, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, SCOPES,
                    TOKEN_FILE)


def main():
    # 1) 관리자 승인 URL 생성
    authorize_url = (
        f"{API_BASE}/api/v2/oauth/authorize"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&state=random_state_value"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={SCOPES}"
    )
    print("=" * 70)
    print("1) 아래 URL을 브라우저에서 열고 '승인'을 누르세요:\n")
    print(authorize_url)
    print("\n2) 승인 후 이동된 주소(redirect_uri)의 ?code=... 값을 복사하세요.")
    print("=" * 70)
    code = input("\ncode 값 입력: ").strip()

    # 2) code -> access_token / refresh_token 교환
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        f"{API_BASE}/api/v2/oauth/token",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json()

    # access token 만료시각이 없으면 직접 계산 (2시간) — 자동 갱신 판단에 필요
    if not token.get("expires_at"):
        from datetime import datetime, timedelta, timezone
        token["expires_at"] = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token, f, indent=2, ensure_ascii=False)

    print(f"\n✅ 토큰 저장 완료 → {TOKEN_FILE}")
    print("이제 `streamlit run app.py` 로 대시보드를 실행하세요.")


if __name__ == "__main__":
    main()
