from __future__ import annotations

import re
from typing import Any

DOMAIN = "solity"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_HASHED_PWD = "hashed_pwd"
CONF_DEVICE_ID = "device_id"
CONF_NICKNAME = "nickname"

# Event-log poll (fast, seconds) — detects external open/close/fingerprint/face.
CONF_LOG_SECONDS = "log_seconds"
DEFAULT_LOG_SECONDS = 10
MIN_LOG_SECONDS = 5
MAX_LOG_SECONDS = 600

# Status poll (slow, minutes) — battery % and deadbolt resync (wakes the lock).
CONF_STATUS_MINUTES = "status_minutes"
DEFAULT_STATUS_MINUTES = 30
MIN_STATUS_MINUTES = 1
MAX_STATUS_MINUTES = 1440

# How many consecutive status-poll misses to tolerate before the lock/battery
# go 'unavailable'. A battery lock is asleep most of the time and often misses
# a single poll, so one miss must NOT flap the entities to unavailable.
TOLERATED_STATUS_FAILURES = 3
TOLERATED_LOG_FAILURES = 3

# The device logs ONLY open events (never a lock/close). Since this lock
# auto-locks, we synthesize a 'close' event — and revert the lock tile to
# 'locked' — this many seconds after each open.
CONF_AUTO_CLOSE_SECONDS = "auto_close_seconds"
DEFAULT_AUTO_CLOSE_SECONDS = 5
MIN_AUTO_CLOSE_SECONDS = 1
MAX_AUTO_CLOSE_SECONDS = 300

# Attribute values tagging the synthesized auto-lock close event.
AUTO_CLOSE_METHOD = "auto"
AUTO_CLOSE_LOG_TYPE = "AUTO_LOCK"
AUTO_CLOSE_MESSAGE = "자동으로 잠겼습니다. (W)"

MANUFACTURER = "SOLITY"
MODEL = "Smart Doorlock"

# Door-lock timezone for the log API (Korea).
LOG_TIMEZONE = "+9:00"

# Event-log codes (logCode) → event category.
LOG_CODE_CLOSE = "0"
LOG_CODE_OPEN = "1"
LOG_CODE_OPEN_LONG = "7"

EVENT_OPEN = "open"
EVENT_CLOSE = "close"
EVENT_OTHER = "other"
EVENT_TYPES = [EVENT_OPEN, EVENT_CLOSE, EVENT_OTHER]

# Solity mediaType (access method) mapping to Korean names
METHOD_MAP: dict[str, str] = {
    "0": "실내 개폐",
    "1": "비밀번호",
    "2": "카드키",
    "3": "지문",
    "4": "스마트폰 앱",
    "5": "실내 수동 개폐",
    "6": "원격 제어",
    "7": "원격 열기",
    "15": "얼굴인식",
    "32": "실내 개폐",
    "auto": "자동잠김",
}

# Face recognition key user name mapping options (up to 7 keys)
CONF_FACE_NAME_1 = "face_name_1"
CONF_FACE_NAME_2 = "face_name_2"
CONF_FACE_NAME_3 = "face_name_3"
CONF_FACE_NAME_4 = "face_name_4"
CONF_FACE_NAME_5 = "face_name_5"
CONF_FACE_NAME_6 = "face_name_6"
CONF_FACE_NAME_7 = "face_name_7"

CONF_FACE_FIELDS: list[str] = [
    CONF_FACE_NAME_1,
    CONF_FACE_NAME_2,
    CONF_FACE_NAME_3,
    CONF_FACE_NAME_4,
    CONF_FACE_NAME_5,
    CONF_FACE_NAME_6,
    CONF_FACE_NAME_7,
]


CONF_CONTROL_MODE = "control_mode"
CONTROL_MODE_CLOUD = "cloud"
CONTROL_MODE_BLUETOOTH = "bluetooth"
CONTROL_MODE_HYBRID = "hybrid"
DEFAULT_CONTROL_MODE = CONTROL_MODE_HYBRID

CONF_BLE_MAC = "ble_mac"
CONF_BLE_APP_KEY = "ble_app_key"
CONF_MEMBER_ID = "member_id"

# Solity BLE GATT UUIDs
SOLITY_BLE_SERVICE_UUID = "48400001-B5A3-F393-E0A9-E50E24DCCA9E"
SOLITY_BLE_WRITE_UUID = "48400002-B5A3-F393-E0A9-E50E24DCCA9E"
SOLITY_BLE_NOTIFY_UUID = "48400003-B5A3-F393-E0A9-E50E24DCCA9E"


def get_face_map(
    options: dict[str, Any] | None,
    server_map: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return combined mapping of face key numbers ('1'..'7') to names.

    Server map provides base nicknames; options overrides take precedence.
    """
    face_map: dict[str, str] = dict(server_map or {})
    if options:
        for i, field in enumerate(CONF_FACE_FIELDS, start=1):
            name = options.get(field)
            if name and str(name).strip():
                face_map[str(i)] = str(name).strip()
    return face_map



def format_access_log(entry: dict, face_map: dict[str, str] | None = None) -> dict[str, Any]:
    """Parse access log entry into cleaned message, who, method, and direction."""
    method_code = str(entry.get("mediaType") or "")
    who = entry.get("nickname") or ""
    raw_msg = entry.get("logMessage") or ""
    msg = raw_msg

    # 1. Resolve registered face recognition if custom names are mapped
    is_face = (method_code == "15") or ("얼굴" in msg)
    if is_face and face_map:
        for k, name in face_map.items():
            if f"{k}번" in msg and "얼굴" in msg:
                who = name
                msg = re.sub(
                    rf"{k}번\s*얼굴(?:인식)?(?:으로)?(?:\s*도어락을)?\s*열었습니다(?:\.\s*\(G\))?",
                    f"{name}님의 얼굴인식으로 열었습니다.",
                    msg,
                )
                msg = msg.replace(f"{k}번 얼굴인식", f"{name}님의 얼굴인식")
                break

    # 2. Parse who from message if nickname empty
    display_who = who
    if not display_who and msg:
        m = re.match(r"^(.+?)님이", msg)
        if m:
            display_who = m.group(1).strip()

    # 3. Direction and method detection
    is_remote = (method_code in ("6", "7")) or ("원격" in msg)
    is_app = (method_code == "4") or ("앱" in msg) or ("스마트폰" in msg)
    is_inside = (not is_remote) and (not is_app) and (("실내" in msg) or ("수동" in msg) or (not display_who and method_code in ("0", "5")))

    if is_remote:
        method_name = "원격 열기"
        direction = "remote"
        if not display_who:
            display_who = "원격 제어"
    elif is_app:
        method_name = "스마트폰 앱"
        direction = "outside"
        if not display_who:
            display_who = "스마트폰 앱"
    elif is_inside:
        method_name = METHOD_MAP.get(method_code, "실내 개폐")
        direction = "inside"
        if not display_who:
            display_who = "실내"
    elif is_face:
        method_name = "얼굴인식"
        direction = "outside"
    else:
        method_name = METHOD_MAP.get(method_code, method_code)
        direction = "outside"

    return {
        "message": msg,
        "who": display_who,
        "method": method_name,
        "method_code": method_code,
        "direction": direction,
    }
