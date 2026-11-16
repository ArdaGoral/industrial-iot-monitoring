"""
Faz 1 - Sentetik motor sensör simülatörü (v2)

Bir elektrik motorunu taklit eder ve her "pencere" (1 saniye) için
titreşim, sıcaklık ve akım değeri üretir.

Modlar:
  normal       -> sağlıklı motor
  bearing      -> rulman arızası (periyodik, kısa, keskin darbeler)
  imbalance    -> dengesizlik (dönüş frekansında büyük titreşim)
  overheating  -> aşırı ısınma (sıcaklık ve akım yükselir)

severity (0..1): arızanın şiddeti. 0 = yeni başlayan, 1 = ileri seviye.

v2 düzeltmeleri:
  - Örnekleme 10 kHz'e çıktı: rulman darbeleri artık üst üste binmiyor,
    kurtosis arıza ağırlaştıkça (gerçekte olduğu gibi) yükseliyor.
  - Veri setinde her sınıf farklı seed kullanıyor: sınıflar aynı gürültüyü paylaşmıyor.
  - Veri setinde motor sıcak başlıyor: ısınma dönemi "normal" verisine karışmıyor.
"""

import numpy as np
import pandas as pd

FS = 10_000        # örnekleme frekansı (Hz): saniyede 10.000 ölçüm
WINDOW_S = 1.0     # her pencere 1 saniye
MODES = ["normal", "bearing", "imbalance", "overheating"]

COLD_TEMP = 40.0       # soğuk motor sıcaklığı (°C)
STEADY_TEMP = 55.0     # normal çalışmada oturduğu sıcaklık (°C)
AMBIENT_TEMP = 25.0    # motor dururken soğuyacağı ortam sıcaklığı (°C)


class MotorSimulator:
    def __init__(self, rpm=3000, seed=None, start_temp=COLD_TEMP):
        self.rng = np.random.default_rng(seed)
        self.f_rot = rpm / 60.0          # dönüş frekansı: 3000 rpm = 50 Hz
        self.temp = start_temp
        self.t0 = 0.0                    # simülasyon zamanı (s)

    def _vibration(self, mode, severity):
        """1 saniyelik ham titreşim sinyali üretir (birim: g)."""
        t = self.t0 + np.arange(int(FS * WINDOW_S)) / FS

        # Sağlıklı motor: dönüş frekansında sinüs + 2. harmonik + gürültü
        sig = 0.5 * np.sin(2 * np.pi * self.f_rot * t)
        sig += 0.1 * np.sin(2 * np.pi * 2 * self.f_rot * t)
        sig += self.rng.normal(0, 0.05, t.size)

        if mode == "imbalance":
            # Dengesizlik: 1x bileşeni büyür
            sig += (1.5 * severity) * np.sin(2 * np.pi * self.f_rot * t)

        elif mode == "bearing":
            # Rulman arızası: her hasarlı noktadan geçişte kısa bir "çınlama".
            # Darbe ~4 ms sürüp sönüyor, darbeler arası ~5.7 ms -> üst üste binmezler.
            f_fault = 3.5 * self.f_rot               # arıza frekansı (basitleştirilmiş)
            period = int(FS / f_fault)               # ~57 örnek
            n = np.arange(40)
            burst = np.exp(-n / 6.0) * np.sin(2 * np.pi * 0.25 * n)   # ~2.5 kHz rezonans
            for i in range(0, sig.size - n.size, period):
                sig[i:i + n.size] += (5.0 * severity) * burst

        self.t0 += WINDOW_S
        return sig

    def read_window(self, mode="normal", severity=0.0, running=True):
        """
        Bir pencere için özet sensör değerleri döndürür.

        running=False: kontaktör enerjisiz, motor duruyor. Dönüş yok, titreşim
        sadece sensör gürültüsü, akım ~0 ve motor ortam sıcaklığına doğru yavaşça
        soğur (ısınmadan ~20 kat yavaş: gövdenin ısıl kütlesi büyük).
        Arıza (mode/severity) motor dönmediği için etkisini göstermez.
        """
        if not running:
            return self._stopped_window()

        sig = self._vibration(mode, severity)

        # Özellik çıkarımı (feature extraction)
        rms = float(np.sqrt(np.mean(sig ** 2)))
        peak = float(np.max(np.abs(sig)))
        m, s = sig.mean(), sig.std()
        kurt = float(np.mean(((sig - m) / s) ** 4)) if s > 0 else 0.0

        # Sıcaklık: hedefe doğru yavaşça yaklaşır
        target = STEADY_TEMP
        if mode == "overheating":
            target = STEADY_TEMP + 45.0 * severity
        elif mode in ("bearing", "imbalance"):
            target = STEADY_TEMP + 10.0 * severity
        self.temp += (target - self.temp) * 0.1 + self.rng.normal(0, 0.15)

        # Akım: normalde ~5 A, arızada yükselir
        current = 5.0 + self.rng.normal(0, 0.1)
        if mode == "overheating":
            current += 2.5 * severity
        elif mode in ("bearing", "imbalance"):
            current += 1.0 * severity

        return {
            "vib_rms": round(rms, 4),
            "vib_peak": round(peak, 4),
            "vib_kurtosis": round(kurt, 4),
            "temp_c": round(self.temp, 2),
            "current_a": round(float(current), 3),
        }


    def _stopped_window(self):
        sig = self.rng.normal(0, 0.01, int(FS * WINDOW_S))      # sadece sensör gürültüsü
        self.t0 += WINDOW_S
        m, s = sig.mean(), sig.std()
        self.temp += (AMBIENT_TEMP - self.temp) * 0.005 + self.rng.normal(0, 0.05)
        return {
            "vib_rms": round(float(np.sqrt(np.mean(sig ** 2))), 4),
            "vib_peak": round(float(np.max(np.abs(sig))), 4),
            "vib_kurtosis": round(float(np.mean(((sig - m) / s) ** 4)), 4),
            "temp_c": round(self.temp, 2),
            "current_a": round(abs(float(self.rng.normal(0, 0.02))), 3),
        }


def generate_dataset(n_per_class=300, seed=42):
    """
    ML için etiketli veri seti üretir.
    Arıza sınıflarında severity 0.2'den 1.0'a kademeli artar (yavaş bozulma).
    Her sınıf kendi seed'ini kullanır ve motor sıcak (ısınmış) başlar.
    """
    rows = []
    for k, mode in enumerate(MODES):
        sim = MotorSimulator(seed=seed + k, start_temp=STEADY_TEMP)
        for i in range(n_per_class):
            sev = 0.0 if mode == "normal" else 0.2 + 0.8 * i / n_per_class
            row = sim.read_window(mode, sev)
            row.update({"mode": mode, "step": i, "severity": round(sev, 3),
                        "label": 0 if mode == "normal" else 1})
            rows.append(row)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = generate_dataset()
    df.to_csv("motor_data.csv", index=False)
    print(f"{len(df)} satır üretildi -> motor_data.csv\n")
    print(df.groupby("mode")[["vib_rms", "vib_kurtosis", "temp_c", "current_a"]]
            .mean().round(3))
