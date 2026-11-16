"""
Gateway'den gelen MQTT mesajlarını doğrular ve tipli hale getirir.

Bu dosya hiçbir dış kütüphaneye bağlı değil; bu yüzden Docker olmadan
`python -m unittest` ile test edilebilir.
"""
import json
import math

# Telemetri alanları ve beklenen tipleri. Listede olmayan alanlar atılır,
# eksik ya da yanlış tipte alan varsa mesaj reddedilir.
TELEMETRY_FIELDS = {
    "vib_rms": float,
    "vib_peak": float,
    "kurtosis": float,
    "temp_c": float,
    "current_a": float,
    "counter": int,
    "sensor_online": bool,
    "temp_alarm": bool,
    "relay": int,
    "temp_alarm_limit_c": float,
}

# Grafiklerde gösterilecek, ortalaması alınabilen sayısal alanlar
NUMERIC_FIELDS = ["vib_rms", "vib_peak", "kurtosis", "temp_c", "current_a"]

HEARTBEAT_FIELDS = {"uptime_s": int, "free_heap": int, "modbus_ok": int, "modbus_err": int}


class InvalidMessage(ValueError):
    pass


def _coerce(name, value, typ):
    # JSON'da bool da sayı gibi görünebilir; karışmasın diye ayrı kontrol
    if typ is bool:
        if isinstance(value, bool):
            return value
        raise InvalidMessage(f"{name}: bool bekleniyordu, {value!r} geldi")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidMessage(f"{name}: sayi bekleniyordu, {value!r} geldi")
    if typ is int:
        if float(value) != int(value):
            raise InvalidMessage(f"{name}: tam sayi bekleniyordu, {value!r} geldi")
        return int(value)
    value = float(value)
    if not math.isfinite(value):
        raise InvalidMessage(f"{name}: sonlu bir sayi degil")
    return value


def _parse_object(payload: bytes | str, fields: dict) -> dict:
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise InvalidMessage(f"gecersiz JSON: {e}") from None
    if not isinstance(data, dict):
        raise InvalidMessage("JSON nesnesi bekleniyordu")

    out = {}
    for name, typ in fields.items():
        if name not in data:
            raise InvalidMessage(f"eksik alan: {name}")
        out[name] = _coerce(name, data[name], typ)
    return out


def parse_telemetry(payload: bytes | str) -> dict:
    return _parse_object(payload, TELEMETRY_FIELDS)


def parse_heartbeat(payload: bytes | str) -> dict:
    return _parse_object(payload, HEARTBEAT_FIELDS)


def parse_status(payload: bytes | str) -> str:
    text = payload.decode() if isinstance(payload, bytes) else payload
    text = text.strip()
    if text not in ("online", "offline"):
        raise InvalidMessage(f"durum online/offline olmali, {text!r} geldi")
    return text


def parse_event(payload: bytes | str) -> dict:
    """Komut sonuçları. Biçimi serbest bırakıyoruz, sadece JSON nesnesi olmalı."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise InvalidMessage(f"gecersiz JSON: {e}") from None
    if not isinstance(data, dict):
        raise InvalidMessage("JSON nesnesi bekleniyordu")
    return data
