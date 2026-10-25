# AI 대시보드

**여러 AI 계정의 사용량과 남은 한도를 한 화면에서.**

ChatGPT · Claude · Gemini · Custom API

AI 대시보드는 개인 운영자를 위한 AI 사용량 대시보드입니다. 여러 계정을 연결하고,
토큰·요청·비용·잔여 한도를 확인합니다. 추론 프록시가 아니며 모델을 호출하지 않습니다.

## 무엇을 보여주나요?

- 대시보드 · 계정 · 사용량 · 설정, 다중 계정 관리
- codex-lb 기반 레이아웃과 Dither Kit 컴포넌트, 라이트·다크 테마
- 연결된 계정의 잔여 한도·초기화 시각·마지막 성공을 먼저 표시
- 오늘 / 7일 / 30일 / 전체 기간 필터, 일별 추이와 모델별 집계
- 공급자가 제공하는 구독 한도와 초기화 시각
- 전체 또는 계정별 새로고침, 마지막 성공 데이터 보존
- 필터를 반영하는 CSV 내보내기
- 연결 전 사용량 API 테스트, 전체 또는 계정별 연결 관리
- 관리자 암호 변경, 서버 측 세션, 암호화된 연결 정보
- 별도 읽기 전용 키로 MCP 클라이언트에서 사용량·모델별 통계·한도 조회
- React · TypeScript · Vite 프런트엔드, FastAPI · SQLite 백엔드

**알 수 없는 값은 `—`로 표시합니다.** 누적 카운터를 일별 통계로 만들거나,
구독 한도를 토큰으로 환산하지 않습니다. 같은 트래픽을 여러 연결에서 수집하면
중복될 수 있으므로 연결 범위를 확인하세요.

## 연결 방식과 조회 범위

| 연결 | 필요한 인증 | 조회 범위 |
| --- | --- | --- |
| ChatGPT / Codex | OpenAI 브라우저 OAuth | Codex 구독 한도·크레딧. ChatGPT 전체 대화의 토큰 기록은 아님 |
| Claude | Anthropic 조직 Admin API key | UTC 일별·모델별 토큰, 별도 조직 비용. Claude.ai Pro/Max 구독과 별개 |
| Gemini 계정 | Google 브라우저 OAuth + 인증 코드 | Gemini CLI / Code Assist의 모델별 잔여 한도. Gemini 웹 대화 기록은 아님 |
| Codex LB | HTTPS 서버 주소 + gateway key | 해당 키의 누적 토큰·요청·비용 환산액·잔여 한도 |
| Claude LB | HTTPS 서버 주소 + 조회 키 | 해당 키의 누적 사용량과 세션·일·주 한도 |
| Gemini LB | HTTPS 서버 주소 + gateway key | gateway의 누적 사용량과 독립된 한도 창 |
| OpenAI API | 조직 Admin API key | API 조직의 일별·모델별 사용량 및 별도 조직 비용 |
| Grok / xAI API | Management key + Team ID | 일별 API 지출. 현재 연동은 토큰·요청 수를 제공하지 않음 |
| Trusted usage JSON | HTTPS origin + 선택적 Bearer key | 아래 공통 수집 형식으로 제공된 데이터 |
| 기타 → 커스텀 API | HTTPS URL + 선택적 조회 키 | 지원되는 사용량 endpoint 자동 탐지 또는 경로·인증·응답 형식 지정 |

Grok 소비자 구독 로그인을 지원한다고 표시하지 않습니다. LB 비용은 설정된
환산액일 수 있으며 공급자의 실제 청구서와 다릅니다. 누적 사용량은 기간별 합계와
분리해서 표시합니다. OpenAI API 집계의 일자 경계는 공급자의 UTC 기준입니다.

### ChatGPT 연결

1. 관리자 로그인 후 **연결 추가 → ChatGPT**를 선택합니다.
2. **OpenAI에서 인증**을 눌러 `https://auth.openai.com`에서 로그인합니다.
3. 인증 후 이동한 `http://localhost:1455/auth/callback?...` 주소 전체를 복사합니다.
   로컬 수신기를 실행하지 않으므로 브라우저에 연결 오류가 표시될 수 있습니다.
4. AI 대시보드의 콜백 입력란에 붙여 넣어 연결을 완료합니다.

AI 대시보드는 OpenAI 암호를 받지 않으며 기기 인증 코드를 사용하지 않습니다. PKCE와
일회용 state를 검증하며, 인증 흐름은 15분 후 만료됩니다. 콜백 URL에는 일회용
인증 코드가 있으므로 공유하지 마세요. 연결 삭제는 로컬 인증 정보를 지우며
공급자 측 권한 철회를 대신하지 않습니다.

이 기능은 [codex-lb](https://github.com/Soju06/codex-lb)의 브라우저 인증 방식과
비공개 ChatGPT quota endpoint를 참고한 연동입니다. OpenAI가 보장하는 공개 소비자
분석 API가 아니며, 공급자 정책과 API 변경에 따라 동작이 달라질 수 있습니다.
본인의 계정과 신뢰하는 AI 대시보드 서버에서만 사용하세요.

### Claude · Gemini · 기타 연결

- **Claude:** 조직 Admin 조회 키 또는 Claude LB 주소·조회 키를 선택합니다.
  일반 추론 API key와 Admin key는 다릅니다. [Anthropic 인증 정책](https://code.claude.com/docs/en/legal-and-compliance#authentication-and-credential-use)에
  따라 Claude.ai 소비자 계정의 OAuth 토큰을 수집하는 제3자 로그인은 제공하지 않습니다.
- **Gemini:** Google 인증 페이지에서 승인한 뒤 표시되는 **인증 코드만** 붙여 넣습니다.
  이메일 식별과 Code Assist 조회에 필요한 범위를 요청하며, 암호를 받지 않습니다.
  흐름은 5분 후 만료됩니다. 계정의 기존 Code Assist 설정이 필요하며 프로젝트 생성,
  결제 활성화, 약관 동의 자동 처리나 모델 호출은 하지 않습니다.
  운영자는 아래 환경 변수로 이 인증 방식과 Code Assist에 호환되는 OAuth 클라이언트를
  별도 설정해야 합니다. 저장소에는 클라이언트 ID·secret을 포함하지 않으며,
  설정이 없으면 인증을 시작하지 않습니다. 임의의 Google OAuth 앱이 Code Assist에
  접근할 수 있다고 보장하지 않습니다.
- **기타 → 커스텀 API:** 공급자 URL과 조회 키를 넣고 연결을 테스트합니다.
  기본 자동 탐지는 `/v1/usage`, `/v1/usage/self`, `/usage.json`을 순서대로 읽습니다.
  `Bearer` 또는 `x-api-key` 인증과 경로·응답 형식을 직접 지정할 수도 있습니다.
  추론 API 호환만으로 사용량 조회가 가능한 것은 아닙니다. 서버가 지원되는
  사용량 JSON을 제공해야 하며, 실패를 사용량 0으로 표시하지 않습니다.

브라우저 인증 요청은 시작한 관리자 세션에 묶입니다. OAuth·조회 키는 서버에서
암호화하여 저장하며, 연결 삭제는 로컬 자격 증명 삭제이지 공급자 측 권한 철회가 아닙니다.

## MCP에서 사용하기

1. 관리자 로그인 후 **Settings → MCP · 읽기 전용 공유**에서 클라이언트별 이름으로 조회 키를 발급합니다.
2. 발급 직후 한 번만 표시되는 키를 안전하게 보관합니다. 목록에서는 다시 볼 수 없습니다.
3. MCP 클라이언트에 서버의 `/mcp` 주소와 `Authorization: Bearer <발급한 키>`를 설정합니다.

Streamable HTTP와 사용자 지정 인증 헤더를 지원하는 클라이언트용 예시입니다.
클라이언트마다 설정 형식은 다를 수 있습니다. 실제 키는 공개 설정·이슈·프롬프트에 넣지 마세요.

```json
{
  "mcpServers": {
    "ai-dashboard": {
      "type": "http",
      "url": "https://use.example.com/mcp",
      "headers": {"Authorization": "Bearer <MCP_READ_ONLY_KEY>"}
    }
  }
}
```

| 도구 | 조회 내용 |
| --- | --- |
| `list_accounts` | 계정 ID·이름·공급자·수집 상태·마지막 성공 시각 |
| `usage_summary` | 날짜별 사용량·기간 합계·모델별 통계. `range`: `today`, `7d`(기본), `30d`, `all`; `account`, `provider`: ID 또는 `all` |
| `account_quota` | `account_id`에 해당하는 계정의 저장된 한도·초기화 시각·별도 누적 사용량 |

MCP는 **저장된 스냅샷만 조회**합니다. 계정 변경·삭제, 인증 정보 조회, 모델 호출,
수집 새로고침은 제공하지 않습니다. 최신 여부는 마지막 성공 시각과 수집 상태를 확인하세요.
브라우저 관리자 쿠키와 MCP 키는 서로 대신 사용할 수 없습니다. 자체 OAuth 로그인이나
자동 클라이언트 등록은 제공하지 않으므로 인증 헤더를 설정할 수 없는 클라이언트는 지원하지 않습니다.

**모든 MCP 키는 이 설치의 전체 계정 이름·통계·한도를 읽을 수 있습니다.**
키별 사용자 데이터 격리나 공개 회원 가입 기능이 아닙니다. 신뢰하는 사람에게만 발급하고,
각자의 비공개 데이터를 관리하려면 별도 설치를 사용하세요. 최대 20개까지 발급할 수 있으며
서버에는 SHA-256 해시만 저장합니다. 자동 만료는 없고, 설정에서 삭제하면 다음 요청부터
거부됩니다. 관리자 암호 변경은 MCP 키를 폐기하지 않습니다. 유출된 키는 직접 삭제하세요.

## 빠른 시작

Python 3.11 이상, Node.js 22.12 이상, Linux를 권장합니다. 프런트엔드는 배포 전에 빌드합니다.

```bash
git clone https://github.com/deploy103/use.git
cd use
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
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
| `ATLAS_GEMINI_CLIENT_ID` | Gemini 연결용 호환 OAuth 클라이언트 ID. 기본값 없음 |
| `ATLAS_GEMINI_CLIENT_SECRET` | 해당 OAuth 클라이언트 secret. 기본값 없음 |

Gemini 클라이언트 설정은 Git 밖의 권한 `0600` 파일에 보관하고 systemd의
`EnvironmentFile=`로 주입하세요. 실제 값은 서비스 예제·README·커밋에 넣지 마세요.
저장소·배포 경로의 `use`와 환경 변수 접두사 `ATLAS_`는 기존 설치와의 호환성을 위해 유지합니다.
프록시는 원래 `Host`를 보존해야 합니다. 서버는 전달 헤더로 신뢰 대상을 바꾸지 않고
실제 Host와 브라우저 Origin을 검사합니다. `/api/*`는 캐시하지 마세요.
관리자 세션은 HttpOnly·SameSite=Strict이며 인증되지 않은 API 조회는 거부됩니다.
관리자 계정 하나를 위한 앱으로, 공개 회원 가입이나 다중 사용자 격리는 제공하지 않습니다.
빌드된 `web-dist/`를 Python 소스와 함께 배포합니다. 운영 서버에 Node.js는 필요하지 않습니다.

암호화 키와 DB는 함께 비공개 백업하세요. 같은 서버의 키를 가진 관리자는 연결된
인증 정보를 복호화할 수 있습니다. 재설치 중 키를 새로 만들면 기존 인증 정보를
복구할 수 없습니다. 인증 실패 시 재연결하고, 계정 삭제 시 해당 로컬 통계도 삭제됩니다.

## 수집 형식

Trusted usage JSON은 검증된 HTTPS origin의 `GET /usage.json`만 읽습니다.
선택적 인증은 `Authorization: Bearer <credential>`입니다. 리다이렉트와 사설망
대상은 허용하지 않습니다. 임의 URL 경로나 로컬 파일을 읽지 않습니다.
커스텀 API 모드에서는 운영자가 HTTPS base path·port와 상대 사용량 경로를 지정할 수 있지만,
동일한 공개망 제한과 DNS 고정 검사를 적용합니다.

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
npm --prefix frontend run build
npm --prefix frontend exec -- playwright install chromium
npm --prefix frontend test
ruff check app.py collectors.py chatgpt.py subscriptions.py
```

테스트는 가짜 공급자 응답과 임시 데이터를 사용합니다. 실제 공급자 로그인 성공이나
현재의 외부 API 호환성을 단위 테스트가 보장하지는 않습니다.

## 출처

레이아웃·UI 일부와 ChatGPT 인증 프로토콜은 MIT 라이선스의
[Soju06/codex-lb](https://github.com/Soju06/codex-lb)를 참고·수정했습니다.
[Dither Kit](https://www.tripwire.sh/dither-kit) 컴포넌트와 Google Gemini CLI의
설치형 클라이언트 인증 방식도 사용합니다. 원본·리비전·라이선스와 수정 범위는
[`licenses/`](licenses/)와 `chatgpt.py`의 저작권 고지에 보존합니다.
AI 대시보드는 OpenAI, Anthropic, Google, xAI의 공식 제품이 아닙니다.
