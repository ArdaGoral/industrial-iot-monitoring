"""
InfluxDB'ye yazma ve geçmiş veriyi okuma.

Zaman damgası: ESP32'de gerçek saat (RTC/NTP) yok, ts_ms sadece açılıştan beri
geçen süre. Bu yüzden ölçümün zamanını backend'in mesajı aldığı an olarak
kaydediyoruz. Ağ gecikmesi milisaniyeler mertebesinde, bizim için yeterli.
"""
import logging
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from . import config
from .telemetry import NUMERIC_FIELDS

log = logging.getLogger("storage")


class Storage:
    def __init__(self):
        self.client = InfluxDBClient(url=config.INFLUX_URL, token=config.INFLUX_TOKEN,
                                     org=config.INFLUX_ORG, timeout=5000)
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self.query_api = self.client.query_api()

    def close(self):
        self.client.close()

    def _write(self, point: Point):
        try:
            self.write_api.write(bucket=config.INFLUX_BUCKET, record=point)
        except Exception as e:                      # DB geçici olarak yoksa backend çökmesin
            log.warning("InfluxDB yazma hatasi: %s", e)

    def write_telemetry(self, data: dict, at: datetime):
        p = Point("telemetry").tag("device", config.DEVICE).time(at, WritePrecision.MS)
        for k, v in data.items():
            p = p.field(k, v)
        self._write(p)

    def write_heartbeat(self, data: dict, at: datetime):
        p = Point("heartbeat").tag("device", config.DEVICE).time(at, WritePrecision.MS)
        for k, v in data.items():
            p = p.field(k, v)
        self._write(p)

    def write_status(self, which: str, value: str, at: datetime):
        p = (Point("status").tag("device", config.DEVICE).tag("component", which)
             .field("online", value == "online").time(at, WritePrecision.MS))
        self._write(p)

    def write_event(self, data: dict, at: datetime):
        p = (Point("event").tag("device", config.DEVICE)
             .tag("cmd", str(data.get("cmd", data.get("topic", "?"))))
             .field("result", str(data.get("result", "?")))
             .time(at, WritePrecision.MS))
        if isinstance(data.get("value"), (int, float)) and not isinstance(data.get("value"), bool):
            p = p.field("value", float(data["value"]))
        self._write(p)

    def history(self, minutes: int, max_points: int = 600) -> list[dict]:
        """
        Son `minutes` dakikanın sayısal telemetrisi.
        Çok uzun aralıkta binlerce nokta göndermemek için zaman pencerelerine
        bölüp ortalama alıyoruz (downsampling): en fazla ~max_points satır döner.
        """
        window_s = max(1, (minutes * 60) // max_points)
        field_filter = " or ".join(f'r._field == "{f}"' for f in NUMERIC_FIELDS)
        flux = f'''
from(bucket: "{config.INFLUX_BUCKET}")
  |> range(start: -{int(minutes)}m)
  |> filter(fn: (r) => r._measurement == "telemetry" and r.device == "{config.DEVICE}")
  |> filter(fn: (r) => {field_filter})
  |> aggregateWindow(every: {window_s}s, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
'''
        rows = []
        for table in self.query_api.query(flux, org=config.INFLUX_ORG):
            for rec in table.records:
                row = {"time": rec.get_time().astimezone(timezone.utc).isoformat()}
                for f in NUMERIC_FIELDS:
                    v = rec.values.get(f)
                    row[f] = None if v is None else round(float(v), 4)
                rows.append(row)
        rows.sort(key=lambda r: r["time"])
        return rows

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False
