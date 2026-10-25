# Use

**여러 AI 계정의 사용량과 남은 한도를 한 화면에서.**

ChatGPT · Codex LB · Claude LB · Gemini LB · xAI · OpenAI API

Use는 개인 운영자를 위한 AI 사용량 대시보드입니다. 여러 계정을 연결하고,
토큰·요청·비용·잔여 한도를 확인합니다. 추론 프록시가 아니며 모델을 호출하지 않습니다.

## 무엇을 보여주나요?

- 서비스별 탭과 다중 계정 관리
- 오늘 / 7일 / 30일 / 전체 기간 필터, 일별 추이와 모델별 집계
- 공급자가 제공하는 구독 한도와 초기화 시각
- 전체 또는 계정별 새로고침, 마지막 성공 데이터 보존
- 필터를 반영하는 CSV 내보내기
- 관리자 암호, 서버 측 세션, 암호화된 연결 정보
- 빌드 단계 없는 FastAPI + SQLite + 정적 웹 UI

**알 수 없는 값은 `—`로 표시합니다.** 누적 카운터를 일별 통계로 만들거나,
구독 한도를 토큰으로 환산하지 않습니다. 같은 트래픽을 여러 연결에서 수집하면
중복될 수 있으므로 연결 범위를 확인하세요.

## 연결 방식과 조회 범위

| 연결 | 필요한 인증 | 조회 범위 |
| --- | --- | --- |
| ChatGPT / Codex | OpenAI 브라우저 OAuth | Codex 구독 한도·크레딧. ChatGPT 전체 대화의 토큰 기록은 아님 |
| Codex LB | HTTPS 서버 주소 + gateway key | 해당 키의 누적 토큰·요청·비용 환산액·잔여 한도 |
| Claude LB | HTTPS 서버 주소 + 조회 키 | 해당 키의 누적 사용량과 세션·일·주 한도 |
| Gemini LB | HTTPS 서버 주소 + gateway key | gateway의 누적 사용량과 독립된 한도 창 |
| OpenAI API | 조직 Admin API key | API 조직의 일별·모델별 사용량 및 별도 조직 비용 |
| Grok / xAI API | Management key + Team ID | 일별 API 지출. 현재 연동은 토큰·요청 수를 제공하지 않음 |
| Trusted usage JSON | HTTPS origin + 선택적 Bearer key | 아래 공통 수집 형식으로 제공된 데이터 |

Google·Grok 소비자 구독 로그인을 지원한다고 표시하지 않습니다. LB 비용은 설정된
환산액일 수 있으며 공급자의 실제 청구서와 다릅니다. 누적 사용량은 기간별 합계와
분리해서 표시합니다. OpenAI API 집계의 일자 경계는 공급자의 UTC 기준입니다.

### ChatGPT 연결

1. 관리자 로그인 후 **연결 추가 → ChatGPT**를 선택합니다.
2. **OpenAI에서 인증**을 눌러 `https://auth.openai.com`에서 로그인합니다.
3. 인증 후 이동한 `http://localhost:1455/auth/callback?...` 주소 전체를 복사합니다.
   로컬 수신기를 실행하지 않으므로 브라우저에 연결 오류가 표시될 수 있습니다.
4. Use의 콜백 입력란에 붙여 넣어 연결을 완료합니다.

Use는 OpenAI 암호를 받지 않으며 기기 인증 코드를 사용하지 않습니다. PKCE와
일회용 state를 검증하며, 인증 흐름은 15분 후 만료됩니다. 콜백 URL에는 일회용
인증 코드가 있으므로 공유하지 마세요. 연결 삭제는 로컬 인증 정보를 지우며
공급자 측 권한 철회를 대신하지 않습니다.

이 기능은 [codex-lb](https://github.com/Soju06/codex-lb)의 브라우저 인증 방식과
비공개 ChatGPT quota endpoint를 참고한 연동입니다. OpenAI가 보장하는 공개 소비자
분석 API가 아니며, 공급자 정책과 API 변경에 따라 동작이 달라질 수 있습니다.
본인의 계정과 신뢰하는 Use 서버에서만 사용하세요.

## 빠른 시작

Python 3.11 이상과 Linux를 권장합니다.

```bash
git clone https://github.com/deploy103/use.git
cd use
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
umask 077
ATLAS_DATA_DIR=./data .venv/bin/uvicorn app:app \
  --host 127.0.0.1 --port 8080 --no-proxy-headers
```

처음 실행하면 `data/setup.token`이 생성됩니다. 서버에서 이 파일을 확인한 뒤
`http://127.0.0.1:8080/#setup=<일회용 토큰>`으로 접속하여 12자 이상의 관리자
암호를 설정하세요. 설정 후 토큰은 삭제됩니다. 토큰·암호·데이터 디렉터리는
저장소나 이슈에 올리지 마세요.

### HTTPS 배포

공개 서비스는 TLS reverse proxy 뒤에 배치하세요. `deploy/use.service`는
전용 사용자 `use`, 코드 `/opt/use`, 상태 `/var/lib/use`를 사용하는 일반 예제입니다.
사용자·디렉터리·가상환경을 먼저 준비하고 예제 도메인을 실제 도메인으로 바꾸세요.
단일 Uvicorn worker만 사용합니다. 인증 흐름과 수집 잠금은 프로세스 메모리에 있습니다.

| 환경 변수 | 기본값 / 의미 |
| --- | --- |
| `ATLAS_DATA_DIR` | `data` — DB·키·암호·인증 정보의 비공개 디렉터리 |
| `ATLAS_PUBLIC_ORIGIN` | 선택 사항. 공개 환경에서는 `https://use.example.com`처럼 정확한 HTTPS origin 지정 |
| `ATLAS_COOKIE_SECURE` | `true`이면 Secure cookie. 공개 origin을 지정하면 자동 강제 |
| `ATLAS_PASSWORD_FILE` | `<data>/owner.password` — scrypt 관리자 암호 해시 |
| `ATLAS_KEY_FILE` | `<data>/encryption.key` — Fernet 암호화 키 |

환경 변수 접두사 `ATLAS_`는 기존 설치와의 호환성을 위해 유지합니다.
프록시는 원래 `Host`를 보존해야 합니다. 서버는 전달 헤더로 신뢰 대상을 바꾸지 않고
실제 Host와 브라우저 Origin을 검사합니다. `/api/*`는 캐시하지 마세요.
관리자 세션은 HttpOnly·SameSite=Strict이며 인증되지 않은 API 조회는 거부됩니다.
관리자 계정 하나를 위한 앱으로, 공개 회원 가입이나 다중 사용자 격리는 제공하지 않습니다.

암호화 키와 DB는 함께 비공개 백업하세요. 같은 서버의 키를 가진 관리자는 연결된
인증 정보를 복호화할 수 있습니다. 재설치 중 키를 새로 만들면 기존 인증 정보를
복구할 수 없습니다. 인증 실패 시 재연결하고, 계정 삭제 시 해당 로컬 통계도 삭제됩니다.

## 수집 형식

Trusted usage JSON은 검증된 HTTPS origin의 `GET /usage.json`만 읽습니다.
선택적 인증은 `Authorization: Bearer <credential>`입니다. 리다이렉트와 사설망
대상은 허용하지 않습니다. 임의 URL 경로나 로컬 파일을 읽지 않습니다.

```json
{
  "records": [{
    "date": "2026-01-02",
    "model": "example-model",
    "requests": 2,
    "input_tokens": 100,
    "output_tokens": 20,
    "cache_read_tokens": 30,
    "cache_write_tokens": 5,
    "cost_usd": 0.01
  }],
  "quota": [],
  "capabilities": ["tokens", "requests", "cost"]
}
```

`input_tokens`는 캐시를 제외한 입력입니다. 총 토큰은 입력 + 출력 + 캐시 읽기 +
캐시 쓰기이며 알 수 없는 항목은 `null`로 둡니다. 기록은 계정·일자·모델별로
교체하므로 새로고침해도 중복 합산하지 않습니다. 누적 데이터만 있다면 빈 `records`와
`summary: {"period":"lifetime", ...}`를 반환합니다. `quota` 항목은
`label`, `used_percent`, `remaining`, `limit`, `resets_at`, `unit`을 사용합니다.
한도 창과 누적 값은 기간별 기록에 더하지 않습니다.

## 개발과 검증

```bash
.venv/bin/python -m pytest -q
node --check static/app.js
ruff check app.py collectors.py chatgpt.py
```

테스트는 가짜 공급자 응답과 임시 데이터를 사용합니다. 실제 공급자 로그인 성공이나
현재의 외부 API 호환성을 단위 테스트가 보장하지는 않습니다.

## 출처

ChatGPT 인증 프로토콜의 일부는 MIT 라이선스의
[Soju06/codex-lb](https://github.com/Soju06/codex-lb)를 참고·수정했습니다.
해당 저작권과 MIT 고지는 `chatgpt.py`에 보존되어 있습니다.
Use는 OpenAI, Anthropic, Google, xAI의 공식 제품이 아닙니다.
