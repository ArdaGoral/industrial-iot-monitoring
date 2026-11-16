"""
Anomali dedektörü testleri. Eğitilmiş modeli kullanır (önce: python ml/train.py).
    cd backend && python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
MODEL = os.path.join(os.path.dirname(__file__), "..", "..", "ml", "model", "anomaly_model.joblib")

try:
    import sklearn  # noqa: F401
    HAVE_SKLEARN = True
except ImportError:
    HAVE_SKLEARN = False

NORMAL = {"vib_rms": 0.364, "vib_peak": 0.69, "kurtosis": 1.66, "temp_c": 55.0, "current_a": 5.0}


@unittest.skipUnless(HAVE_SKLEARN and os.path.exists(MODEL), "model ya da scikit-learn yok")
class AnomalyTests(unittest.TestCase):
    def setUp(self):
        from app.anomaly import AnomalyDetector
        self.d = AnomalyDetector(MODEL)
        self.assertTrue(self.d.ready)

    def test_normal_anomali_degil(self):
        for _ in range(10):
            r = self.d.score(NORMAL)
        self.assertLess(r["score"], 1.0)
        self.assertFalse(r["is_anomaly"])

    def test_tek_yuksek_deger_alarm_degil(self):
        r = self.d.score({**NORMAL, "temp_c": 90.0})
        self.assertGreater(r["score"], 1.0)
        self.assertFalse(r["is_anomaly"])          # debounce: tek pencere yetmez

    def test_asiri_isinma_basla_bitir(self):
        hot = {**NORMAL, "temp_c": 90.0, "current_a": 7.0}
        events = [self.d.score(hot)["event"] for _ in range(3)]
        self.assertEqual(events, [None, None, "start"])
        r = self.d.score(hot)
        self.assertTrue(r["is_anomaly"])
        self.assertEqual(r["top_feature"], "temp_c")
        self.assertGreater(r["top_z"], 0)
        events = [self.d.score(NORMAL)["event"] for _ in range(3)]
        self.assertEqual(events, [None, None, "end"])

    def test_rulman_baskin_etken_kurtosis_ya_da_tepe(self):
        bearing = {**NORMAL, "vib_peak": 3.8, "kurtosis": 11.0, "vib_rms": 0.7}
        r = self.d.score(bearing)
        self.assertIn(r["top_feature"], ("kurtosis", "vib_peak", "vib_rms"))

    def test_model_yoksa_calisir(self):
        from app.anomaly import AnomalyDetector
        d = AnomalyDetector("/yok/boyle/bir/dosya.joblib")
        self.assertFalse(d.ready)
        self.assertIsNone(d.score(NORMAL))


if __name__ == "__main__":
    unittest.main()
