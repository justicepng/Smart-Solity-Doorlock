# Smart Solity Doorlock — Home Assistant 커스텀 통합

솔리티(SOLITY) 스마트 도어락을 **SmartThings 없이** 솔리티 클라우드 API로 직접 제어하는 HA 커스텀 통합.

- 🔓 잠금/해제(열기) — `lock` 엔티티
- 🔋 배터리 + 상태(deadBolt, 카드/비번 개수 등) — `sensor`
- 🕑 **이벤트 로그 빠른 감지** — 외부 열림/잠김/지문을 수초 내 감지 (`event` + "마지막 출입" 센서)
- ⚙️ UI 설정흐름 (이메일/비번 입력 → 도어락 자동 탐색)
- 🔁 토큰 자동 갱신 (30일 JWT, 401/403 시 재로그인)
- 📟 폰 앱과 **독립 세션**(phoneToken `ha-solity`)이라 충돌 없음

> SmartThings API 유료화(2026.10~)를 피해 솔리티 자체 클라우드를 직접 쓰는 방식입니다. (여전히 클라우드 경유 — 완전 로컬은 아님)

## 설치

### HACS (커스텀 저장소)
1. HACS → 우상단 ⋮ → **Custom repositories**
2. 이 저장소 URL 추가, 카테고리 **Integration**
3. "Smart Solity Doorlock" 검색 → 설치 → HA 재시작

### 수동
`custom_components/solity/` 폴더를 HA의 `/config/custom_components/` 아래로 복사 → HA 재시작

## 설정
설정 → **기기 및 서비스** → **통합 추가** → "Smart Solity" 검색
→ **이메일 + 비밀번호** 입력 → 도어락 자동 등록.

- 도어락이 여러 개면 선택 단계가 나옵니다.
- 설정 후 통합의 **옵션**에서 두 가지 조회 주기를 바꿀 수 있습니다.

## 엔티티
| 엔티티 | 설명 |
|---|---|
| `lock.<도어락이름>` | 잠금/해제/열기 |
| `sensor.<도어락이름>_battery` | 배터리 % |
| `sensor.<도어락이름>_last_access` | 마지막 출입 (누가/어떻게/언제) |
| `event.<도어락이름>_door_event` | 새 출입 이벤트 (`open`/`close`/`other`) — 자동화 트리거용 |

## 폴링 구조 (2가지)
| 종류 | 대상 | 도어락 깨움 | 기본 | 범위 |
|---|---|---|---|---|
| **이벤트 로그** | 클라우드 로그(`retrieveLog`) | ❌ 안 함 | 10초 | 5~600초 |
| **상태/배터리** | `get_status` | ✅ 깨움 | 30분 | 1~1440분 |

이벤트 로그는 도어락을 안 깨우니 **자주(수초)** 돌려도 배터리 부담이 없고, 상태 조회만 느리게 둡니다.

## 유의점
- **자동잠김** 도어락은 해제(열기)만 실질 의미가 있고, 잠금은 보통 이미 잠긴 상태입니다. `lock` 상태(잠김/열림)는 상태조회(느림) 기준이고, "누가 언제 열었나"는 `event`/`last_access`(빠름)로 봅니다.
- HA에서 보낸 열기/잠그기는 즉시 반영됩니다.
- 이벤트는 도어락이 클라우드에 보고한 뒤 뜨므로, 그 보고 지연(수초)이 실제 하한입니다.
- 계정 자격증명은 HA config entry에 저장됩니다(비밀번호는 SHA-256 해시로만 보관).

### 자동화 예시
```yaml
automation:
  - alias: 아이 하교 알림
    triggers:
      - trigger: state
        entity_id: event.우리집_door_event
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.attributes.event_type == 'open' }}"
    actions:
      - action: notify.mobile_app_phone
        data:
          message: >
            현관 열림 — {{ trigger.to_state.attributes.who }}
            ({{ trigger.to_state.attributes.method }})
```

## 동작 원리
- `POST /api_v2/login` → JWT `token` + `tokenPwd` (헤더 `Authorization`, `AuthorizationPwd`)
- `PUT /api_v2/controlDevice/{id}` body `{"controlType":"open|close|get_status","optionValue":"1"}`
- `lang`은 반드시 숫자 `"0"` (문자 `"ko"`는 반쪽 토큰 발급됨)

## 면책
비공식 프로젝트입니다. 솔리티/제조사와 무관하며, 앱 API 변경 시 동작하지 않을 수 있습니다. 본인 계정·본인 도어락에 대해서만 사용하세요.
