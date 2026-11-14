"""
Faz 1 - Canlı sensör (v2)

sensor_sim.py'deki simülatörü arka planda sürekli çalışan bir "sensöre" çevirir:
  - Her `interval` saniyede yeni bir ölçüm üretir
  - Modu dışarıdan değiştirebilirsin (normal / bearing / imbalance / overheating)
  - İstersen arıza zamanla kendiliğinden ağırlaşır (degrade_rate)
  - Son ölçümü hem sözlük hem de Modbus register formatında verir

v2 düzeltmeleri:
  - Sensör değerleri Modbus "input register" (FC04, salt okunur) olarak tanımlandı.
    Holding register'lar (FC03/06/16) Faz 2'de röle komutu, alarm eşiği gibi
    yazılabilir ayarlar için kullanılacak.
  - Etiket (mod/şiddet) artık ölçümle birlikte saklanıyor: etiket ile veri
    hiçbir zaman birbirinden kopmuyor (önceki sürümde 1 ölçümlük kayma oluyordu).
"""

import threading
import time

from sensor_sim import MotorSimulator, MODES

# Modbus INPUT register haritası (Function Code 04 ile okunur).
# Register'lar 16-bit işaretsiz tam sayıdır (0..65535), ondalık taşıyamaz.
# Bu yüzden değerleri bir katsayıyla çarpıp tam sayıya çeviririz (ölçekleme).
#   isim: (register adresi, çarpan)
INPUT_REGISTER_MAP = {
    "vib_rms":      (0, 1000),   # 0.364 g   -> 364
    "vib_peak":     (1, 1000),
    "vib_kurtosis": (2, 100),    # 13.33     -> 1333 (kurtosis büyüyebildiği için 100)
    "temp_c":       (3, 100),    # 54.55 C   -> 5455
    "current_a":    (4, 1000),   # 5.007 A   -> 5007
}
COUNTER_REGISTER = 5             # her ölçümde artar (heartbeat / canlılık göstergesi)
INPUT_REGISTER_COUNT = 6


def to_input_registers(reading, counter):
    """Sensör sözlüğünü Modbus input register listesine çevirir."""
    regs = [0] * INPUT_REGISTER_COUNT
    for name, (addr, scale) in INPUT_REGISTER_MAP.items():
        value = int(round(reading[name] * scale))
        regs[addr] = max(0, min(65535, value))     # 16-bit sınırına sıkıştır
    regs[COUNTER_REGISTER] = counter % 65536       # 65535'ten sonra 0'a döner
    return regs


class LiveSensor:
    def __init__(self, interval=1.0, seed=None):
        self._sim = MotorSimulator(seed=seed)      # soğuk başlar: ısınma da simüle edilir
        self._interval = interval
        self._lock = threading.Lock()      # iki thread aynı veriye dokunmasın diye
        self._stop = threading.Event()
        self._thread = None

        self._mode = "normal"
        self._severity = 0.0
        self._degrade_rate = 0.0
        self._latest = None                # son ölçüm + o ölçümün etiketi
        self._counter = 0

    # --- dışarıdan kontrol ---------------------------------------------
    def set_mode(self, mode, severity=0.5, degrade_rate=0.0):
        """mode: MODES'tan biri. degrade_rate: her ölçümde severity'ye eklenen miktar."""
        if mode not in MODES:
            raise ValueError(f"Bilinmeyen mod: {mode}. Geçerli: {MODES}")
        with self._lock:
            self._mode = mode
            self._severity = 0.0 if mode == "normal" else severity
            self._degrade_rate = degrade_rate

    def get_latest(self):
        """Son ölçüm sözlüğü (henüz yoksa None). 'mode' ve 'severity' alanları
        bu ölçümün hangi durumda alındığını gösterir (sadece test/ML için)."""
        with self._lock:
            return dict(self._latest) if self._latest else None

    def get_input_registers(self):
        """Son ölçüm Modbus input register listesi olarak."""
        with self._lock:
            if self._latest is None:
                return [0] * INPUT_REGISTER_COUNT
            return to_input_registers(self._latest, self._counter)

    # --- yaşam döngüsü -------------------------------------------------
    def start(self):
        self._tick()                       # ilk değer hemen hazır olsun
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join()

    # --- iç mekanizma --------------------------------------------------
    def _tick(self):
        with self._lock:
            mode, sev = self._mode, self._severity
        reading = self._sim.read_window(mode, sev)
        reading["mode"] = mode             # etiket ölçümle birlikte saklanır
        reading["severity"] = round(sev, 3)
        with self._lock:
            self._latest = reading
            self._counter += 1
            if self._degrade_rate and self._mode != "normal":
                self._severity = min(1.0, self._severity + self._degrade_rate)

    def _run(self):
        while not self._stop.wait(self._interval):
            self._tick()


if __name__ == "__main__":
    # Senaryo: sağlıklı -> rulman arızası başlıyor ve ağırlaşıyor -> tamir -> sağlıklı
    sensor = LiveSensor(interval=0.5, seed=1)
    sensor.start()

    scenario = [
        ("normal",  0.0, 0.0,  4),
        ("bearing", 0.2, 0.08, 10),
        ("normal",  0.0, 0.0,  4),
    ]

    print(f"{'mod':<10}{'sev':>6}  {'rms':>6} {'kurt':>6} {'temp':>6} {'amp':>6}   input registers")
    try:
        for mode, sev, rate, ticks in scenario:
            sensor.set_mode(mode, sev, rate)
            for _ in range(ticks):
                time.sleep(0.5)
                r = sensor.get_latest()
                print(f"{r['mode']:<10}{r['severity']:>6}  {r['vib_rms']:>6} "
                      f"{r['vib_kurtosis']:>6} {r['temp_c']:>6} {r['current_a']:>6}   "
                      f"{sensor.get_input_registers()}")
    finally:
        sensor.stop()
