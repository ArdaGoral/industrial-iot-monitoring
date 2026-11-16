"""Ayarlar ortam değişkenlerinden okunur (docker-compose.yml'de tanımlı)."""
import os

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC_BASE = os.getenv("TOPIC_BASE", "factory/motor1")
DEVICE = TOPIC_BASE.rsplit("/", 1)[-1]          # "motor1" -> InfluxDB'de etiket

INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
INFLUX_ORG = os.getenv("INFLUX_ORG", "factory")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "telemetry")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "dev-token-degistir")

# Bu kadar süre telemetri gelmezse veri "bayat" sayılır
STALE_AFTER_S = float(os.getenv("STALE_AFTER_S", "5"))

# Motor kalktıktan sonra bu süre anomali skorlanmaz (yol alma bastırması).
# Uzun duruştan sonra motor soğuk kalkar; ısınana kadar sıcaklık "normal"in
# altındadır. Bu bir arıza değil, beklenen bir geçici durumdur.
STARTUP_INHIBIT_S = float(os.getenv("STARTUP_INHIBIT_S", "30"))
