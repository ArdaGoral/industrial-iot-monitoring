"""
Canlı anomali tespiti.

ml/train.py'nin ürettiği modeli yükler ve her telemetri mesajını skorlar.
Skor formülü eğitimdekiyle aynı (ml/train.py -> combined):

    skor = max(IF skoru / IF eşiği, en büyük |z| / z eşiği)     > 1 ise anomali

Tek bir yüksek skor alarm değildir: art arda `debounce` (3) pencere eşiği
aşınca anomali başlar, art arda `debounce` pencere eşiğin altında kalınca biter.
Bu "histerezis", sınırda dalgalanan bir değerin alarmı aç-kapa yapmasını önler.

Model dosyası yoksa backend yine çalışır; sadece anomali alanı boş gelir.
"""
import logging
import os

import numpy as np

log = logging.getLogger("anomaly")

MODEL_PATH = os.getenv("MODEL_PATH", "/srv/model/anomaly_model.joblib")

FEATURE_TR = {
    "vib_rms": "titreşim RMS",
    "vib_peak": "titreşim tepe",
    "kurtosis": "kurtosis",
    "temp_c": "sıcaklık",
    "current_a": "akım",
}


class AnomalyDetector:
    def __init__(self, path: str = MODEL_PATH):
        self.ready = False
        self.info: dict = {"loaded": False, "path": path}
        self._above = 0          # art arda eşik üstü sayısı
        self._below = 0          # art arda eşik altı sayısı
        self.active = False      # şu an anomali durumunda mı
        try:
            import joblib
            import sklearn
            b = joblib.load(path)
        except FileNotFoundError:
            log.warning("Model dosyasi yok (%s): anomali tespiti kapali", path)
            return
        except Exception as e:                       # bozuk dosya, sürüm uyumsuzluğu...
            log.error("Model yuklenemedi: %s", e)
            self.info["error"] = str(e)
            return

        self.pipeline = b["pipeline"]
        self.features: list[str] = b["features"]
        self.if_thr: float = b["if_threshold"]
        self.z_thr: float = b["z_threshold"]
        self.debounce: int = b["debounce"]
        self.mean = np.array([b["normal_mean"][f] for f in self.features])
        self.std = np.array([b["normal_std"][f] for f in self.features])
        self.ready = True
        self.info = {
            "loaded": True,
            "trained_at": b["trained_at"],
            "features": self.features,
            "debounce": self.debounce,
            "sklearn_trained": b["sklearn_version"],
            "sklearn_runtime": sklearn.__version__,
        }
        if b["sklearn_version"] != sklearn.__version__:
            log.warning("Model scikit-learn %s ile egitildi, burada %s var: sonuclar farkli olabilir",
                        b["sklearn_version"], sklearn.__version__)
        log.info("Anomali modeli yuklendi (egitim: %s)", b["trained_at"])

    def score(self, data: dict) -> dict | None:
        """
        Bir telemetri mesajını skorlar. Döndürdüğü sözlükteki 'event' alanı,
        anomali o an başladıysa "start", bittiyse "end", değilse None olur.
        """
        if not self.ready:
            return None
        x = np.array([[data[f] for f in self.features]], dtype=float)

        if_s = float(-self.pipeline.decision_function(x)[0])
        z = (x[0] - self.mean) / self.std
        score = max(if_s / self.if_thr, float(np.abs(z).max()) / self.z_thr)

        # Açıklama: normalden en çok sapan özellik (operatör "neden?" diye sorar)
        i = int(np.abs(z).argmax())

        event = None
        if score > 1.0:
            self._above += 1
            self._below = 0
            if not self.active and self._above >= self.debounce:
                self.active, event = True, "start"
        else:
            self._below += 1
            self._above = 0
            if self.active and self._below >= self.debounce:
                self.active, event = False, "end"

        return {
            "score": round(score, 3),
            "is_anomaly": self.active,
            "top_feature": self.features[i],
            "top_feature_tr": FEATURE_TR.get(self.features[i], self.features[i]),
            "top_z": round(float(z[i]), 1),
            "event": event,
        }

    def reset(self):
        """Sensör kopunca çağrılır: kopukluk öncesi sayaçlar sonrasına taşınmasın."""
        self._above = self._below = 0
        self.active = False
