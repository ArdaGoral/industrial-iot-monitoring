"""
Sistemin son bilinen durumu (bellekte).

MQTT iş parçacığı yazar, API okur; bu yüzden bir kilit (lock) ile korunur.
Dashboard açılır açılmaz bu anlık görüntüyü alır, sonra WebSocket'ten
gelen güncellemelerle devam eder.
"""
import threading
import time
from collections import deque

from . import config


class SystemState:
    def __init__(self):
        self._lock = threading.Lock()
        self.telemetry: dict | None = None
        self.telemetry_at: float | None = None      # backend'in aldığı zaman (unix sn)
        self.anomaly: dict | None = None
        self.heartbeat: dict | None = None
        self.gateway_status: str = "unknown"
        self.plc_status: str = "unknown"
        self.mqtt_connected: bool = False
        self.events: deque = deque(maxlen=50)

    def set_telemetry(self, data: dict, at: float, anomaly: dict | None = None):
        with self._lock:
            self.telemetry, self.telemetry_at, self.anomaly = data, at, anomaly

    def set_heartbeat(self, data: dict):
        with self._lock:
            self.heartbeat = data

    def set_status(self, which: str, value: str):
        with self._lock:
            setattr(self, f"{which}_status", value)

    def add_event(self, data: dict):
        with self._lock:
            self.events.appendleft(data)

    def snapshot(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self._lock:
            age = None if self.telemetry_at is None else round(now - self.telemetry_at, 1)
            return {
                "device": config.DEVICE,
                "mqtt_connected": self.mqtt_connected,
                "gateway_status": self.gateway_status,
                "plc_status": self.plc_status,
                "telemetry": self.telemetry,
                "telemetry_age_s": age,
                # Hiç veri yoksa ya da son veri çok eskiyse bayat say
                "telemetry_stale": age is None or age > config.STALE_AFTER_S,
                "anomaly": self.anomaly,
                "heartbeat": self.heartbeat,
                "events": list(self.events)[:10],
            }


state = SystemState()
