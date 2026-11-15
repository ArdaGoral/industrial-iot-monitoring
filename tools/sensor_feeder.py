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


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


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

    sim = MotorSimulator(seed=args.seed, start_temp=args.start_temp)
    severity = 0.0 if args.mode == "normal" else args.severity

    print(f"Gonderiliyor: mod={args.mode}  (Ctrl+C ile dur)\n")
    print(f"{'#':>5}  {'mod':<11}{'sev':>5}  {'rms':>6} {'kurt':>7} {'sicaklik':>8} {'akim':>6}")

    n = 0
    next_time = time.monotonic()
    try:
        while True:
            # Ölçümü tam gönderim anında üret: tek saat, tekrar ya da atlama yok
            reading = sim.read_window(args.mode, severity)
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
                  f"{reading['temp_c']:>8} {reading['current_a']:>6}{note}")

            if args.degrade and args.mode != "normal":
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
