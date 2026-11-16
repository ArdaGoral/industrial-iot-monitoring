"""
Backend'in mesaj doğrulama testleri. Docker gerekmez:
    cd backend && python -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.state import SystemState                          # noqa: E402
from app.telemetry import (InvalidMessage, parse_heartbeat,  # noqa: E402
                           parse_status, parse_telemetry)

# ESP32'nin gerçekten gönderdiği mesaj (Faz 3 testinden kopya)
REAL = (b'{"ts_ms":17978,"vib_rms":0.363,"vib_peak":0.706,"kurtosis":1.67,'
        b'"temp_c":54.71,"current_a":5.061,"counter":748,"sensor_online":true,'
        b'"temp_alarm":false,"relay":0,"temp_alarm_limit_c":70.00}')


class TelemetryTests(unittest.TestCase):
    def test_gercek_mesaj(self):
        d = parse_telemetry(REAL)
        self.assertEqual(d["temp_c"], 54.71)
        self.assertEqual(d["counter"], 748)
        self.assertIs(d["sensor_online"], True)
        self.assertIsInstance(d["relay"], int)
        self.assertIsInstance(d["temp_alarm_limit_c"], float)   # 70.00 -> float kalmalı
        self.assertNotIn("ts_ms", d)                             # uptime saklanmıyor

    def test_eksik_alan(self):
        data = json.loads(REAL)
        del data["temp_c"]
        with self.assertRaises(InvalidMessage):
            parse_telemetry(json.dumps(data))

    def test_yanlis_tip(self):
        data = json.loads(REAL)
        data["sensor_online"] = 1               # bool yerine sayı
        with self.assertRaises(InvalidMessage):
            parse_telemetry(json.dumps(data))
        data = json.loads(REAL)
        data["temp_c"] = "55"                   # sayı yerine metin
        with self.assertRaises(InvalidMessage):
            parse_telemetry(json.dumps(data))

    def test_bozuk_json(self):
        with self.assertRaises(InvalidMessage):
            parse_telemetry(b'{"vib_rms":0.36,')
        with self.assertRaises(InvalidMessage):
            parse_telemetry(b'[1,2,3]')
        with self.assertRaises(InvalidMessage):
            parse_telemetry(b'\xff\xfe')

    def test_counter_ondalik_olamaz(self):
        data = json.loads(REAL)
        data["counter"] = 748.5
        with self.assertRaises(InvalidMessage):
            parse_telemetry(json.dumps(data))

    def test_interlock_opsiyonel(self):
        self.assertIs(parse_telemetry(REAL)["interlock"], False)      # eski firmware
        data = json.loads(REAL)
        data["interlock"] = True
        self.assertIs(parse_telemetry(json.dumps(data))["interlock"], True)
        data["interlock"] = 1
        with self.assertRaises(InvalidMessage):
            parse_telemetry(json.dumps(data))

    def test_heartbeat(self):
        hb = parse_heartbeat(b'{"uptime_s":21,"free_heap":255244,"modbus_ok":21,"modbus_err":0}')
        self.assertEqual(hb["modbus_ok"], 21)

    def test_status(self):
        self.assertEqual(parse_status(b"online"), "online")
        self.assertEqual(parse_status(b"offline\n"), "offline")
        with self.assertRaises(InvalidMessage):
            parse_status(b"belki")


class StateTests(unittest.TestCase):
    def test_veri_yoksa_bayat(self):
        s = SystemState()
        self.assertTrue(s.snapshot(now=100)["telemetry_stale"])

    def test_taze_ve_bayat(self):
        s = SystemState()
        s.set_telemetry({"temp_c": 55.0}, at=100.0)
        self.assertFalse(s.snapshot(now=102.0)["telemetry_stale"])
        self.assertTrue(s.snapshot(now=110.0)["telemetry_stale"])   # 5 sn'den eski


if __name__ == "__main__":
    unittest.main()
