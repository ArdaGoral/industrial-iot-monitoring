"""
Sanal Blue Pill'deki Modbus RTU slave'i Mac'ten test eder.

Renode, USART2'yi TCP 3456 portuna bağlıyor. Biz de oraya Modbus RTU
paketleri gönderiyoruz (RTU over TCP).

İki tür test var:
  1) pymodbus ile: bağımsız, hazır bir Modbus kütüphanesi bizim C kodumuzu
     doğru anlıyor mu?
  2) Ham soket ile: pymodbus'ın göndermeyeceği bozuk paketlere cihaz
     doğru tepki veriyor mu?

Çalıştırma (Renode çalışırken, başka bir terminalde):
    python tools/modbus_test.py
"""

import socket
import time

from pymodbus import FramerType
from pymodbus.client import ModbusTcpClient

HOST, PORT, SLAVE = "127.0.0.1", 3456, 1

# Input register haritası: (isim, çarpan) - firmware/modbus.h ile aynı sırada
INPUT_MAP = [
    ("vib_rms (g)", 1000),
    ("vib_peak (g)", 1000),
    ("kurtosis", 100),
    ("sicaklik (C)", 100),
    ("akim (A)", 1000),
    ("sayac", 1),
]

passed = failed = 0


def check(cond, name):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK    {name}")
    else:
        failed += 1
        print(f"  HATA  {name}")


def call(fn, *args, **kwargs):
    """pymodbus 3.10'da 'slave' parametresinin adı 'device_id' oldu; ikisini de dener."""
    try:
        return fn(*args, device_id=SLAVE, **kwargs)
    except TypeError:
        return fn(*args, slave=SLAVE, **kwargs)


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def frame(body: bytes) -> bytes:
    c = crc16(body)
    return body + bytes([c & 0xFF, c >> 8])


def raw_exchange(packet: bytes, wait=1.0) -> bytes:
    """Ham paketi gönderir, `wait` saniye içinde gelen her şeyi döndürür."""
    with socket.create_connection((HOST, PORT), timeout=2) as s:
        s.sendall(packet)
        s.settimeout(wait)
        data = b""
        try:
            while True:
                chunk = s.recv(256)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
        return data


def pymodbus_tests():
    client = ModbusTcpClient(HOST, port=PORT, framer=FramerType.RTU, timeout=2)
    if not client.connect():
        print("Renode'a baglanilamadi. Renode calisiyor ve rtos.resc yuklendi mi?")
        raise SystemExit(1)

    try:
        print("FC04: sensor degerlerini oku")
        r = call(client.read_input_registers, 0, count=6)
        check(not r.isError(), "cevap geldi")
        if not r.isError():
            for (name, scale), raw in zip(INPUT_MAP, r.registers):
                value = raw if scale == 1 else raw / scale
                print(f"        {name:<14} {value:>8}   (ham {raw})")
            check(5400 <= r.registers[3] <= 5600, "sicaklik makul aralikta")

        print("FC04: sayac artiyor mu (heartbeat)")
        a = call(client.read_input_registers, 5, count=1).registers[0]
        time.sleep(1.2)
        b = call(client.read_input_registers, 5, count=1).registers[0]
        check(b > a, f"sayac {a} -> {b}")

        print("FC03: ayarlari oku")
        r = call(client.read_holding_registers, 0, count=2)
        check(not r.isError() and r.registers[1] == 7000, "alarm esigi = 7000 (70.00 C)")

        print("FC06: roleyi ac, geri oku, kapat")
        r = call(client.write_register, 0, 1)
        check(not r.isError(), "role=1 yazildi")
        r = call(client.read_holding_registers, 0, count=1)
        check(r.registers[0] == 1, "geri okunan deger 1")
        call(client.write_register, 0, 0)

        print("Istisnalar")
        r = call(client.read_input_registers, 4, count=5)
        check(r.isError() and getattr(r, "exception_code", None) == 2,
              "tablo disi okuma -> istisna 02")
        r = call(client.write_register, 0, 5)
        check(r.isError() and getattr(r, "exception_code", None) == 3,
              "role'ye 5 yazma -> istisna 03")
    finally:
        client.close()


def raw_tests():
    print("Ham paketler")
    good = frame(bytes([SLAVE, 0x04, 0, 0, 0, 1]))

    resp = raw_exchange(good)
    check(len(resp) == 7 and crc16(resp[:-2]) == (resp[-2] | resp[-1] << 8),
          f"gecerli paket -> 7 baytlik cevap ({resp.hex(' ')})")

    bad = bytearray(good)
    bad[-1] ^= 0xFF
    check(raw_exchange(bytes(bad)) == b"", "bozuk CRC -> cevap yok")

    other = frame(bytes([7, 0x04, 0, 0, 0, 1]))
    check(raw_exchange(other) == b"", "baska cihaza (7) giden paket -> cevap yok")


if __name__ == "__main__":
    pymodbus_tests()
    time.sleep(0.3)
    raw_tests()
    print(f"\n{passed} gecti, {failed} hata")
    raise SystemExit(1 if failed else 0)
