"""Constants for the Smart Solity Doorlock integration."""
from __future__ import annotations

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
    "1": "비밀번호",
    "2": "카드키",
    "3": "지문",
    "4": "스마트폰 앱",
    "5": "비상키",
    "15": "얼굴인식",
    "auto": "자동잠김",
}
