"""Constants for the Smart Solity Doorlock integration."""

DOMAIN = "solity"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_HASHED_PWD = "hashed_pwd"
CONF_DEVICE_ID = "device_id"
CONF_NICKNAME = "nickname"

# Event-log poll (fast, seconds) — detects external open/close/fingerprint.
CONF_LOG_SECONDS = "log_seconds"
DEFAULT_LOG_SECONDS = 10
MIN_LOG_SECONDS = 5
MAX_LOG_SECONDS = 600

# Status poll (slow, minutes) — battery % and deadbolt resync (wakes the lock).
CONF_STATUS_MINUTES = "status_minutes"
DEFAULT_STATUS_MINUTES = 30
MIN_STATUS_MINUTES = 1
MAX_STATUS_MINUTES = 1440

MANUFACTURER = "SOLITY"
MODEL = "WELKOM"

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
