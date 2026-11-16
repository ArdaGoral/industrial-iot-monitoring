"""
MQTT köprüsü: gateway'in yayınladıklarını dinler, InfluxDB'ye yazar,
son durumu günceller ve WebSocket istemcilerine iletir. Dashboard'dan
gelen komutları da MQTT'ye yayınlar.

paho-mqtt kendi iş parçacığında (thread) çalışır, FastAPI ise asyncio ile.
İkisi arasında köprü: loop.call_soon_threadsafe ile asyncio kuyruğuna mesaj atmak.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from . import config
from .anomaly import AnomalyDetector
from .state import state
from .storage import Storage
from .telemetry import (InvalidMessage, parse_event, parse_heartbeat,
                        parse_status, parse_telemetry)

log = logging.getLogger("mqtt")

T = config.TOPIC_BASE
TOPIC_TELEMETRY = f"{T}/telemetry"
TOPIC_HEARTBEAT = f"{T}/gateway/heartbeat"
TOPIC_GW_STATUS = f"{T}/gateway/status"
TOPIC_PLC_STATUS = f"{T}/plc/status"
TOPIC_EVENT = f"{T}/event"
TOPIC_CMD = f"{T}/cmd"


class MqttBridge:
    def __init__(self, storage: Storage, loop: asyncio.AbstractEventLoop, outbox: asyncio.Queue,
                 detector: AnomalyDetector):
        self.storage = storage
        self.detector = detector
        self.loop = loop
        self.outbox = outbox            # WebSocket yayıncısının okuduğu kuyruk
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="iiot-backend")
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)
        self.stats = {"received": 0, "rejected": 0}
        self.motor_started_at: float | None = None     # motor en son ne zaman kalktı

    def start(self):
        # connect_async: broker henüz hazır değilse de çökmez, arka planda dener
        self.client.connect_async(config.MQTT_HOST, config.MQTT_PORT, keepalive=30)
        self.client.loop_start()

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()

    def publish_command(self, name: str, payload: str) -> bool:
        info = self.client.publish(f"{TOPIC_CMD}/{name}", payload, qos=1)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def _anomaly_status(self, data: dict, now: float) -> str:
        """
        Bu ölçüm anomali modeline sorulmalı mı? Model sadece "motor normal
        şartlarda dönüyor" durumunu öğrendi; diğer durumlarda skor anlamsız.
          no_model        model dosyası yok
          sensor_offline  değer bayat (PLC son ölçümü tekrarlıyor)
          motor_stopped   röle enerjisiz, motor duruyor
          startup         motor yeni kalktı, ısınıyor (STARTUP_INHIBIT_S)
          active          skorlanıyor
        """
        if not self.detector.ready:
            return "no_model"
        if not data["sensor_online"]:
            return "sensor_offline"
        if data["relay"] != 1:
            self.motor_started_at = None
            return "motor_stopped"
        if self.motor_started_at is None:
            self.motor_started_at = now
        if now - self.motor_started_at < config.STARTUP_INHIBIT_S:
            return "startup"
        return "active"

    # ---------- paho geri çağrıları (MQTT iş parçacığında çalışır) ----------

    def _put(self, msg: dict):
        try:
            self.outbox.put_nowait(msg)
        except asyncio.QueueFull:           # yayıncı yetişemiyorsa eski canlı veriyi bırak
            pass

    def _emit(self, msg: dict):
        self.loop.call_soon_threadsafe(self._put, msg)

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("Broker baglantisi reddedildi: %s", reason_code)
            return
        log.info("Broker'a baglandi: %s:%s", config.MQTT_HOST, config.MQTT_PORT)
        state.mqtt_connected = True
        # cmd/# 'e abone OLMUYORUZ: kendi gönderdiğimiz komutları geri almak istemiyoruz
        for topic in (TOPIC_TELEMETRY, TOPIC_HEARTBEAT, TOPIC_GW_STATUS, TOPIC_PLC_STATUS, TOPIC_EVENT):
            client.subscribe(topic, qos=1)
        self._emit({"type": "backend", "mqtt_connected": True})

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        log.warning("Broker baglantisi koptu: %s", reason_code)
        state.mqtt_connected = False
        self._emit({"type": "backend", "mqtt_connected": False})

    def _on_message(self, client, userdata, msg):
        now = time.time()
        at = datetime.fromtimestamp(now, tz=timezone.utc)
        self.stats["received"] += 1
        try:
            if msg.topic == TOPIC_TELEMETRY:
                data = parse_telemetry(msg.payload)
                # Bayat değeri (sensör kopuk) skorlamak anlamsız: model eski bir ölçümü
                # tekrar tekrar görüp "normal" ya da "anomali" demeye devam ederdi.
                anomaly, anomaly_status = None, self._anomaly_status(data, now)
                if anomaly_status == "active":
                    anomaly = self.detector.score(data)
                else:
                    self.detector.reset()
                state.set_telemetry(data, now, anomaly, anomaly_status)
                self.storage.write_telemetry(data, at, anomaly["score"] if anomaly else None)
                self._emit({"type": "telemetry", "time": at.isoformat(), "data": data,
                            "anomaly": anomaly, "anomaly_status": anomaly_status})

                if anomaly and anomaly["event"]:
                    started = anomaly["event"] == "start"
                    ev = {"time": at.isoformat(), "started": started, "score": anomaly["score"],
                          "top_feature": anomaly["top_feature"], "top_feature_tr": anomaly["top_feature_tr"],
                          "top_z": anomaly["top_z"]}
                    self.storage.write_anomaly_event(ev, at)
                    self._emit({"type": "anomaly", "data": ev})
                    log.warning("Anomali %s: skor %.2f, baskin etken %s (z=%+.1f)",
                                "BASLADI" if started else "bitti", anomaly["score"],
                                anomaly["top_feature"], anomaly["top_z"])

            elif msg.topic == TOPIC_HEARTBEAT:
                data = parse_heartbeat(msg.payload)
                state.set_heartbeat(data)
                self.storage.write_heartbeat(data, at)
                self._emit({"type": "heartbeat", "data": data})

            elif msg.topic in (TOPIC_GW_STATUS, TOPIC_PLC_STATUS):
                which = "gateway" if msg.topic == TOPIC_GW_STATUS else "plc"
                value = parse_status(msg.payload)
                state.set_status(which, value)
                # Retained mesaj eski bir durumdur: DB'ye "şimdi oldu" diye yazmayalım
                if not msg.retain:
                    self.storage.write_status(which, value, at)
                self._emit({"type": "status", "component": which, "value": value})
                log.info("%s durumu: %s%s", which, value, " (retained)" if msg.retain else "")

            elif msg.topic == TOPIC_EVENT:
                data = parse_event(msg.payload)
                data["time"] = at.isoformat()
                state.add_event(data)
                self.storage.write_event(data, at)
                self._emit({"type": "event", "data": data})

        except InvalidMessage as e:
            self.stats["rejected"] += 1
            log.warning("Gecersiz mesaj (%s): %s", msg.topic, e)
