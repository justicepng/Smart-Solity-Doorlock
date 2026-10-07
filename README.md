# Smart Solity Doorlock — Home Assistant 커스텀 통합

솔리티(SOLITY) 스마트 도어락을 **SmartThings 없이** 솔리티 클라우드 API 및 ESPHome 블루투스 프록시(Bluetooth Proxy)로 직접 연동하는 Home Assistant 커스텀 통합구성요소입니다.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/default)
[![version](https://img.shields.io/badge/version-v1.5.4-blue.svg)](https://github.com/justicepng/Smart-Solity-Doorlock/releases)

---

## ✨ 주요 기능

- 🔓 **잠금 / 해제(열기)** — `lock` 엔티티 지원 (도어락 물리 특성에 맞춘 안정적인 잠김 상태 유지)
- 🚪 **문 덜 닫힘(Sub-Latch) & 데드볼트 미잠김 감지** — 모티스 내 물리 서브 랫치 핀 센서와 연동하여 문이 덜 닫혔거나 데드볼트가 걸렸을 때 미잠김 상태 감지 및 `is_unclosed` 속성 제공
- 📡 **블루투스 프록시(Bluetooth Proxy) 로컬 직결 제어** — ESPHome 블루투스 프록시를 통해 클라우드 경유 없이 0초대 초고속 로컬 제어 지원
- 🔀 **3가지 제어 모드 (하이브리드 / 블루투스 / 클라우드)** — 블루투스를 우선 시도한 후 실패 시 클라우드로 자동 폴백하는 안심 하이브리드 모드 지원
- 👤 **얼굴키(1~7번) 별명 실시간 자동 동기화** — 솔리티 공식 앱에서 수정한 얼굴인식 키 별명을 클라우드 API(`/api_v2/addrNickname`)를 통해 수동 입력 없이 100% 자동 동기화 (`solity.sync_face_nicknames` 서비스 제공)
- 🕒 **출입 센서 발생 시각 표기** — 마지막 출입 센서(`last_access`) 상태에 `(HH:MM)` 형태의 출입 시각 자동 표기
- 🚨 **장시간 문열림 및 안전 경보** — 도어락 부저가 울리며 발생하는 장시간 문열림(코드 7) 수신 시 가상 닫힘 방지 및 상태 유지
- 🔋 **배터리 잔량 모니터링** — 도어락 배터리 상태 표시 및 HA 재시작 시에도 직전 상태 복원 유지
- 🚪 **실내 개폐(안에서 열고 나감) 자동 판별** — 내부 레버/열림 버튼 동작 시 실내 개폐로 자동 감지 (`direction: inside`)
- 🕑 **초고속 이벤트 감지** — 도어락을 깨우지 않는 클라우드 로그 폴링으로 배터리 소모 없이 수초 내 출입 감지
- ⚙️ **간결한 옵션 UI** — 복잡한 수동 입력 없이 제어 모드, BLE MAC 주소, 폴링 주기만 깔끔하게 설정

---

## 💡 권장 사항: Home Assistant 전용 계정 사용

스마트폰에서 솔리티 공식 앱을 주로 사용하시는 경우, **Home Assistant용 계정을 별도로 추가 발급하여 연동하는 것을 강력히 권장**합니다.

> [!TIP]
> **왜 전용 계정이 필요한가요?**
> - 솔리티 공식 스마트폰 앱과 Home Assistant가 동일한 관리자 계정으로 동시 로그인할 경우, 세션 충돌로 인해 스마트폰 앱에서 예기치 않게 로그아웃되거나 공식 앱의 푸시 알림 수신이 불안정해질 수 있습니다.
> - 별도 계정을 만들어 기기를 공유하면 스마트폰 앱의 로그인 및 푸시 알림에 전혀 간섭 없이 24시간 안정적으로 Home Assistant와 연동됩니다.

### 📌 전용 계정 등록 절차 (약 2분 소요)
1. **서브 계정 회원가입**: 솔리티 스마트 도어락 앱 또는 웹에서 별도의 이메일 주소(예: HA 전용 구글 계정 등)로 새 계정을 생성합니다.
2. **도어락 멤버 초대 (공유)**:
   - 스마트폰 솔리티 앱(기존 메인 계정) 실행 ➔ 도어락 선택 ➔ **[설정]** ➔ **[멤버 관리]** ➔ **[멤버 초대]**
   - 1번에서 생성한 **서브 계정의 이메일 주소**를 입력하고 초대를 보냅니다.
3. **초대 수락**:
   - 서브 계정으로 로그인하여 초대를 수락합니다 (도어락 제어 및 상태 확인 권한 획득).
4. **Home Assistant 연동**:
   - Home Assistant의 통합구성요소 추가 시 이 **서브 계정(전용 이메일 및 비밀번호)**으로 로그인합니다.

---

## 📦 설치 방법

### HACS (커스텀 저장소)
1. Home Assistant 접속 ➔ **HACS** ➔ 우측 상단 점 3개(⋮) ➔ **Custom repositories(커스텀 저장소)** 선택
2. 저장소 URL에 아래 주소 입력, 카테고리 **Integration(통합구성요소)** 선택 후 추가:
   ```
   https://github.com/justicepng/Smart-Solity-Doorlock
   ```
3. HACS 목록에서 **"Smart Solity Doorlock"** 검색 ➔ **다운로드** ➔ **Home Assistant 다시 시작**

### 수동 설치
저장소의 `custom_components/solity/` 폴더를 Home Assistant의 `/config/custom_components/` 디렉토리에 복사한 후 Home Assistant를 다시 시작합니다.

---

## ⚙️ 설정 및 옵션

### 1. 기본 등록
Home Assistant **설정** ➔ **기기 및 서비스** ➔ **통합구성요소 추가** ➔ **"Smart Solity"** 검색 ➔ **솔리티 계정(이메일/비밀번호)** 입력.

### 2. 구성 옵션 ([구성 / 옵션])
| 설정 항목 | 설명 | 기본값 |
| :--- | :--- | :---: |
| **제어 모드 (`control_mode`)** | `hybrid`(블루투스 우선 + 클라우드 폴백) / `bluetooth`(로컬 직결) / `cloud`(클라우드 전용) | `hybrid` |
| **블루투스 MAC 주소 (`ble_mac`)** | 도어락 BLE MAC 주소 (클라우드에서 자동 수신되나 필요 시 직접 지정 가능) | 자동 수신값 |
| **이벤트 로그 조회 주기 (`log_seconds`)** | 출입 감지 주기 (클라우드 캐시 조회, 배터리 소모 없음) | 10초 (5~600초) |
| **상태/배터리 조회 주기 (`status_minutes`)** | 도어락 배터리 및 하드웨어 상태 동기화 주기 (도어락을 깨움) | 30분 (1~1440분) |
| **자동 잠김 반영 지연 (`auto_close_seconds`)** | 문열림 감지 후 잠김 상태 복귀 지연 시간 | 5초 (1~300초) |

---

## 📊 제공 엔티티 및 속성

| 엔티티 | 설명 | 주요 속성 |
| :--- | :--- | :--- |
| `lock.<도어락이름>` | 도어락 잠금/해제 및 실시간 개폐 상태 | `sub_latch`, `system_mode`, `is_unclosed`, `control_mode`, `ble_mac`, `ble_available`, `last_access_who`, `last_access_method`, `last_access_time` |
| `sensor.<도어락이름>_battery` | 배터리 잔량 (%) | `battery`, `device_class: battery` |
| `sensor.<도어락이름>_last_access` | 최근 출입 기록 (누가 / 어떻게 / 시각) | `who`, `method`, `method_code`, `direction` (`inside`/`outside`), `datetime` |
| `event.<도어락이름>_door_event` | 실시간 출입 이벤트 발생기 (`open`/`close`) | `event_type`, `who`, `method`, `direction`, `log_code`, `synthetic` |

### 🔍 주요 진단 속성 설명
- `sub_latch`: 모티스 서브 랫치 핀 센서 상태 (`1`: 문 닫힘, `0`: 문 덜 닫힘/열림)
- `system_mode`: 데드볼트 모터 상태 (`1`: 정상 잠김, `0`: 미잠김)
- `is_unclosed`: 문이 덜 닫혔거나 데드볼트가 걸렸는지 여부 (`true` / `false`)
- `live_state`: 실시간 개폐 이벤트 활성 여부

---

## 🛠️ 추천 자동화 예시

### 1. 문 덜 닫힘 / 미잠김 경보 (텔레그램)
문이 열린 뒤 25초 이내에 정상적으로 닫히거나 잠기지 않았을 때 경고를 발송합니다.
```yaml
alias: "현관 도어락 미잠김 경보 (텔레그램)"
description: "현관문이 열린 후 25초 내에 정상 잠기지 않거나 장시간 문열림 발생 시 텔레그램 경고 알림 전송"
mode: restart
trigger:
  - trigger: state
    entity_id: event.hyeongwan_urijib_doeorag_door_event
    id: door_event
action:
  - choose:
      # Case 1: 도어락 자체 장시간 문열림 경보 (log_code == 7)
      - conditions:
          - condition: template
            value_template: "{{ trigger.to_state.attributes.log_code in ['7', 7] }}"
        sequence:
          - action: telegram_bot.send_message
            data:
              message: |
                ⚠️ [현관 도어락 경고]
                도어락에서 장시간 문열림 경보가 발생했습니다!
                현관문이 완전히 닫혔는지 확인해 주세요.
      # Case 2: 문열림 후 25초 경과 시 잠김 검증
      - conditions:
          - condition: template
            value_template: "{{ trigger.to_state.attributes.event_type == 'open' and not trigger.to_state.attributes.synthetic | default(false) and trigger.to_state.attributes.log_code not in ['7', 7] }}"
        sequence:
          - delay: "00:00:25"
          - action: homeassistant.update_entity
            target:
              entity_id: lock.hyeongwan_urijib_doeorag
          - delay: "00:00:03"
          - if:
              - condition: template
                value_template: "{{ state_attr('lock.hyeongwan_urijib_doeorag', 'sub_latch') in [0, '0'] or state_attr('lock.hyeongwan_urijib_doeorag', 'system_mode') in [0, '0'] or is_state('lock.hyeongwan_urijib_doeorag', 'unlocked') }}"
            then:
              - action: telegram_bot.send_message
                data:
                  message: |
                    ⚠️ [현관 도어락 경고]
                    현관문이 아직 정상적으로 잠기지 않았습니다!
                    문이 덜 닫혔거나 걸려있는지 확인해 주세요.
```

---

## ⚠️ 유의사항

- **원격 열기/제어 (클라우드)**: 클라우드를 통한 원격 제어는 도어락과 Wi-Fi 사이에 **스마트솔리티 Wi-Fi 게이트웨이(GW-100 등)**가 연동되어 있어야 동작합니다.
- **로컬 블루투스 직결 제어**: 집안에 ESPHome 블루투스 프록시가 설치되어 있고 도어락과 BLE 통신 범위(수 미터 이내)에 위치해야 합니다.
- **비밀번호 보안**: 계정 비밀번호는 원문 대신 솔리티 앱과 동일한 SHA-256 해시 형태로만 안전하게 보관됩니다.

---

## 📄 라이선스 및 면책

본 통합구성요소는 비공식 커스텀 프로젝트이며 솔리티(SOLITY) 공식 제조사와는 무관합니다. 개인 스마트홈 환경에서 본인 소유의 계정 및 도어락에 한해 사용하시기 바랍니다.
