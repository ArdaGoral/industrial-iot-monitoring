"""
Sanal sensör modülü.

sensor_sim.py'deki motor simülatörünün ölçümlerini sanal Blue Pill'in
USART3'üne (Renode, TCP 3457) gönderir. Gerçekte bu işi dijital bir
sensör modülü yapardı.

Çerçeve (14 bayt): AA 55 | 5 x uint16 (büyük bayt önce) | CRC-16 (küçük bayt önce)
Değer sırası: vib_rms, vib_peak, kurtosis, sıcaklık, akım

Kullanım (Renode çalışırken):
  python tools/sensor_feeder.py                                     # sağlıklı, ısınmış motor
  python tools/sensor_feeder.py --start-temp 40                     # soğuk motor, ısınma dahil
  python tools/sensor_feeder.py --mode bearing --severity 0.2 --degrade 0.02
  python tools/sensor_feeder.py --mode overheating --severity 0.8
  python tools/sensor_feeder.py --corrupt-every 10                  # her 10 çerçeveden birini boz
Durdurmak için Ctrl+C. Durdurunca cihaz 2 sn sonra "VERI GELMIYOR" demeli.

v2: Ölçüm, gönderim anında üretiliyor (tek saat). Önceki sürümde ölçüm ayrı bir
thread'de üretildiği için iki saat kayıyor, bazen aynı ölçüm iki kez gidiyordu.

v3: Kapalı döngü. PLC her çerçeveye röle durumunu içeren 6 baytlık bir çerçeveyle
cevap veriyor (AA 66 | röle | durum bitleri | CRC). Röle enerjisizse simülatördeki
motor durur: akım ~0, titreşim sadece gürültü, sıcaklık ortama doğru düşer.
Arızanın ilerlemesi (--degrade) de motor dururken bekler.
PLC açılışta röleyi enerjisiz başlatır: motoru dashboard'dan "Enerjilendir" ile kaldır.
"""

import argparse
import socket
import struct
import sys
import time
from pathlib import Path

# Proje kökündeki sensor_sim.py ve live_sensor.py'yi bulabilmek için
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live_sensor import to_input_registers  # noqa: E402
from sensor_sim import MODES, STEADY_TEMP, MotorSimulator  # noqa: E402

HOST, PORT = "127.0.0.1", 3457
LEGACY_WAIT_S = 3.0          # bu sürede PLC'den durum gelmezse eski firmware say
ST_INTERLOCK = 0x04          # firmware'deki MB_ST_INTERLOCK


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class StatusReader:
    """PLC'den gelen AA 66 | röle | bitler | CRC(LE) çerçevelerini çözer."""

    def __init__(self):
        self.buf = bytearray()
        self.relay = None        # henüz durum gelmedi
        self.flags = 0

    def feed(self, data: bytes):
        self.buf += data
        while len(self.buf) >= 6:
            i = self.buf.find(b"\xAA\x66")
            if i < 0:
                del self.buf[:-1]           # son bayt bir başlığın yarısı olabilir
                return
            del self.buf[:i]
            if len(self.buf) < 6:
                return
            frame = bytes(self.buf[:6])
            if crc16(frame[:4]) == struct.unpack("<H", frame[4:6])[0]:
                self.relay, self.flags = frame[2], frame[3]
                del self.buf[:6]
            else:
                del self.buf[:1]            # yanlış başlangıç: bir bayt kaydır ve ara


def encode(values) -> bytes:
    body = b"\xAA\x55" + struct.pack(">5H", *values)   # > = büyük bayt önce, H = uint16
    return body + struct.pack("<H", crc16(body))        # < = küçük bayt önce


def main():
    ap = argparse.ArgumentParser(description="Sanal sensör modülü")
    ap.add_argument("--mode", choices=MODES, default="normal")
    ap.add_argument("--severity", type=float, default=0.3, help="arıza şiddeti 0..1")
    ap.add_argument("--degrade", type=float, default=0.0, help="her ölçümde şiddete eklenen miktar")
    ap.add_argument("--interval", type=float, default=0.5, help="ölçüm aralığı (sn)")
    ap.add_argument("--start-temp", type=float, default=STEADY_TEMP,
                    help=f"motorun başlangıç sıcaklığı (varsayılan {STEADY_TEMP}, ısınmış)")
    ap.add_argument("--corrupt-every", type=int, default=0, help="her N çerçeveden birini boz (0 = hiç)")
    ap.add_argument("--seed", type=int, default=None, help="tekrarlanabilir gürültü için")
    args = ap.parse_args()

    try:
        sock = socket.create_connection((HOST, PORT), timeout=2)
    except OSError:
        print(f"{HOST}:{PORT} adresine baglanilamadi. Renode calisiyor ve rtos.resc yuklendi mi?")
        raise SystemExit(1)
    # Bağlandıktan sonra zaman aşımını kaldır: okumayı MSG_DONTWAIT ile beklemeden yapıyoruz
    sock.settimeout(None)

    sim = MotorSimulator(seed=args.seed, start_temp=args.start_temp)
    severity = 0.0 if args.mode == "normal" else args.severity
    status = StatusReader()
    started = time.monotonic()
    legacy = False               # PLC durum göndermiyorsa motoru hep çalışıyor say
    last_relay, last_locked = None, False

    # İlk ölçümden önce PLC'nin röle durumunu öğren (PLC 200 ms'de bir gönderir).
    # Öğrenmeden başlarsak ilk ölçüm motorun gerçek durumuyla çelişebilir.
    deadline = time.monotonic() + LEGACY_WAIT_S
    while status.relay is None and time.monotonic() < deadline:
        try:
            data = sock.recv(256, socket.MSG_DONTWAIT)
            if not data:
                raise SystemExit("Renode baglantiyi kapatti.")
            status.feed(data)
        except (BlockingIOError, InterruptedError):
            time.sleep(0.05)

    print(f"Gonderiliyor: mod={args.mode}  (Ctrl+C ile dur)")
    print("Motor, PLC'nin rolesi enerjiliyken doner. Dashboard'dan 'Enerjilendir' ile kaldir.\n")
    print(f"{'#':>5}  {'mod':<11}{'sev':>5}  {'rms':>6} {'kurt':>7} {'sicaklik':>8} {'akim':>6}  motor")

    n = 0
    next_time = time.monotonic()
    try:
        while True:
            # PLC'den gelen durum çerçevelerini oku (beklemeden)
            try:
                while True:
                    data = sock.recv(256, socket.MSG_DONTWAIT)
                    if not data:
                        raise ConnectionResetError
                    status.feed(data)
            except (BlockingIOError, InterruptedError):
                pass

            if status.relay is None and not legacy and time.monotonic() - started > 2 * LEGACY_WAIT_S:
                legacy = True
                print("  !! PLC'den role durumu gelmiyor (eski firmware?). Motor surekli calisiyor sayiliyor.")

            running = legacy or status.relay == 1
            locked = bool(status.flags & ST_INTERLOCK)
            if status.relay is not None and status.relay != last_relay:
                print(f"  -> role {'ENERJILI: motor kalkiyor' if status.relay else 'ENERJISIZ: motor duruyor'}")
                last_relay = status.relay
            if locked != last_locked:
                print("  !! PLC KORUMA KILIDI: asiri sicaklik, role dusuruldu" if locked
                      else "  -> koruma kilidi acildi (motor sogudu)")
                last_locked = locked

            # Ölçümü tam gönderim anında üret: tek saat, tekrar ya da atlama yok
            reading = sim.read_window(args.mode, severity, running=running)
            values = to_input_registers(reading, 0)[:5]
            frame = bytearray(encode(values))

            note = ""
            if args.corrupt_every and n % args.corrupt_every == args.corrupt_every - 1:
                frame[6] ^= 0x10            # yolda 1 bit bozulmuş gibi
                note = "  <- BOZUK gonderildi"

            sock.sendall(frame)
            n += 1
            print(f"{n:>5}  {args.mode:<11}{severity:>5.2f}  "
                  f"{reading['vib_rms']:>6} {reading['vib_kurtosis']:>7} "
                  f"{reading['temp_c']:>8} {reading['current_a']:>6}  "
                  f"{'doner' if running else 'DURUYOR'}{note}")

            # Arıza sadece motor dönerken ilerler
            if args.degrade and args.mode != "normal" and running:
                severity = min(1.0, severity + args.degrade)

            # Bir sonraki ölçüm zamanına kadar bekle. "Şimdiden itibaren 0.5 sn"
            # yerine sabit bir zaman çizelgesi kullanıyoruz ki gecikmeler birikmesin
            # (FreeRTOS'taki xTaskDelayUntil ile aynı mantık).
            next_time += args.interval
            time.sleep(max(0.0, next_time - time.monotonic()))
    except KeyboardInterrupt:
        print("\nDurduruldu.")
    except (BrokenPipeError, ConnectionResetError):
        print("\nRenode baglantiyi kapatti.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
