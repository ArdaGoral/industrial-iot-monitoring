"""
Kontrol paneli (launcher)

Bütün sistemi terminal açmadan tarayıcıdan yönetmek için. Mac'te çalışır,
diğer programları (Renode, sensör, QEMU, dashboard) alt süreç olarak başlatır,
loglarını toplar ve http://localhost:9000 adresinde bir panel sunar.

    source .venv/bin/activate
    python tools/launcher.py

Sadece Python'un standart kütüphanesini kullanır. Sadece bu bilgisayardan
(127.0.0.1) erişilebilir ve sadece aşağıda tanımlı komutları çalıştırır.
Kapatınca (Ctrl+C) başlattığı her şeyi durdurur; Docker servisleri çalışmaya
devam eder (panelden ayrıca durdurulabilir).
"""
import glob
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
PORT = int(os.getenv("LAUNCHER_PORT", "9000"))
HTML = Path(__file__).with_name("launcher.html")

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r")
UTF8_ENV = {"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}


# ---------------------------------------------------------------- araç yolları

def find_tool(*candidates: str) -> str | None:
    """Önce PATH'te, sonra verilen yollarda (glob destekli) arar."""
    for c in candidates:
        if "/" not in c:
            p = shutil.which(c)
            if p:
                return p
        else:
            for p in sorted(glob.glob(os.path.expanduser(c)), reverse=True):
                if os.access(p, os.X_OK):
                    return p
    return None


RENODE = find_tool("/Applications/Renode.app/Contents/MacOS/macos_run.command", "renode")
QEMU = find_tool("qemu-system-xtensa",
                 "~/.espressif/tools/tools/qemu-xtensa/*/qemu/bin/qemu-system-xtensa")
PIO = find_tool("~/.platformio/penv/bin/pio", "pio")
NPM = find_tool("npm", "/opt/homebrew/bin/npm", "/usr/local/bin/npm")
DOCKER = find_tool("docker", "/usr/local/bin/docker", "/opt/homebrew/bin/docker",
                   "/Applications/Docker.app/Contents/Resources/bin/docker")
IDF_ACTIVATE = next(iter(sorted(glob.glob(str(HOME / ".espressif/tools/activate_idf_v*.sh")),
                                reverse=True)), None)

PLC_DIR = ROOT / "firmware/bluepill_rtos"
GW_DIR = ROOT / "firmware/esp32_gateway"
PLC_ELF = PLC_DIR / ".pio/build/bluepill_f103c8/firmware.elf"
GW_FLASH = GW_DIR / "build/qemu_flash.bin"
GW_EFUSE = GW_DIR / "build/qemu_efuse.bin"

# Senaryolar: sensör modülünü bu argümanlarla yeniden başlatır
SCENARIOS = {
    "normal":      {"title": "Normal çalışma", "args": []},
    "bearing":     {"title": "Rulman hasarı", "args": ["--mode", "bearing", "--severity", "0", "--degrade", "0.003"]},
    "imbalance":   {"title": "Dengesizlik", "args": ["--mode", "imbalance", "--severity", "0", "--degrade", "0.003"]},
    "overheating": {"title": "Aşırı ısınma", "args": ["--mode", "overheating", "--severity", "0", "--degrade", "0.003"]},
}


# ---------------------------------------------------------------- loglar

class LogBuffer:
    """Son N satırı tutar. Her satırın artan bir sıra numarası var; tarayıcı
    "şu numaradan sonrasını ver" diye sorar, böylece satırlar tekrar gelmez."""

    def __init__(self, maxlen=3000):
        self.lines: deque = deque(maxlen=maxlen)
        self.seq = 0
        self.lock = threading.Lock()

    def add(self, text: str):
        text = ANSI.sub("", text).rstrip("\n")
        if not text.strip():
            return
        with self.lock:
            self.seq += 1
            self.lines.append((self.seq, time.strftime("%H:%M:%S"), text))

    def since(self, seq: int, limit=500):
        with self.lock:
            out = [l for l in self.lines if l[0] > seq]
        return out[-limit:]

    def last(self) -> str:
        with self.lock:
            return self.lines[-1][2] if self.lines else ""


# ---------------------------------------------------------------- uzun süren süreçler

class Managed:
    """Arka planda sürekli çalışan bir program (Renode, QEMU, sensör, Vite)."""

    def __init__(self, cid, name, desc, ready_pattern, ready_timeout):
        self.id, self.name, self.desc = cid, name, desc
        self.ready_re = re.compile(ready_pattern)
        self.ready_timeout = ready_timeout
        self.log = LogBuffer()
        self.proc: subprocess.Popen | None = None
        self.state = "stopped"          # stopped | starting | running | exited | error
        self.note = ""
        self.started_at = None
        self.ready = threading.Event()
        self.lock = threading.Lock()

    # Alt sınıflar doldurur
    def command(self) -> tuple[list[str], Path, dict]:
        raise NotImplementedError

    def precheck(self) -> str | None:
        """Başlatmadan önce eksik bir şey varsa açıklayıcı hata mesajı döndürür."""
        return None

    def start(self, wait=False) -> str | None:
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return None
            err = self.precheck()
            if err:
                self.state, self.note = "error", err
                self.log.add(f"[panel] {err}")
                return err
            cmd, cwd, extra_env = self.command()
            env = {**os.environ, **UTF8_ENV, "PYTHONUNBUFFERED": "1", **extra_env}
            self.log.add("[panel] $ " + " ".join(cmd))
            self.ready.clear()
            try:
                # start_new_session: kendi süreç grubu olsun ki alt süreçleriyle
                # birlikte (ör. npm -> node -> vite) tek seferde durdurabilelim
                self.proc = subprocess.Popen(
                    cmd, cwd=cwd, env=env, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    start_new_session=True, bufsize=0)
            except OSError as e:
                self.state, self.note = "error", f"başlatılamadı: {e}"
                self.log.add(f"[panel] {self.note}")
                return self.note
            self.state, self.note, self.started_at = "starting", "", time.time()
            threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
            threading.Thread(target=self._ready_timer, args=(self.proc,), daemon=True).start()
        if wait:
            if not self.ready.wait(self.ready_timeout + 1):
                return self.note or "hazır olmadı (zaman aşımı)"
        return None

    def _reader(self, proc):
        buf = b""
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                line = raw.decode("utf-8", "replace")
                self.log.add(line)
                if not self.ready.is_set() and self.ready_re.search(ANSI.sub("", line)):
                    self.ready.set()
                    if self.proc is proc and self.state == "starting":
                        self.state = "running"
        if buf:
            self.log.add(buf.decode("utf-8", "replace"))
        code = proc.wait()
        if self.proc is proc and self.state != "stopped":
            self.state = "exited"
            self.note = f"kapandı (çıkış kodu {code})"
            self.log.add(f"[panel] süreç {self.note}")
        self.ready.set()        # bekleyen varsa serbest bıraksın

    def _ready_timer(self, proc):
        if not self.ready.wait(self.ready_timeout) and self.proc is proc and self.state == "starting":
            self.note = "beklenen 'hazır' satırı gelmedi, logları kontrol et"
            self.log.add(f"[panel] {self.note}")

    def stop(self):
        with self.lock:
            proc = self.proc
            if not proc or proc.poll() is not None:
                self.state = "stopped"
                return
            self.state, self.note = "stopped", ""
            self.log.add("[panel] durduruluyor...")
        # Önce Ctrl+C gibi kibarca (SIGINT): sensör bağlantısını düzgün kapatır,
        # Vite ve QEMU temiz çıkar. Olmazsa SIGTERM, en son SIGKILL.
        for sig, wait in ((signal.SIGINT, 3), (signal.SIGTERM, 3), (signal.SIGKILL, 3)):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            try:
                proc.wait(wait)
                break
            except subprocess.TimeoutExpired:
                continue
        self.log.add("[panel] durdu")

    def send(self, text: str) -> bool:
        """Sürecin standart girdisine yazar (Renode monitörüne komut göndermek için)."""
        p = self.proc
        if not p or p.poll() is not None:
            return False
        try:
            p.stdin.write(text.encode() + b"\n")
            p.stdin.flush()
            self.log.add(f"[panel] > {text}")
            return True
        except OSError:
            return False

    def status(self) -> dict:
        p = self.proc
        alive = bool(p and p.poll() is None)
        return {
            "id": self.id, "name": self.name, "desc": self.desc,
            "state": self.state if (alive or self.state in ("error", "exited", "stopped")) else "stopped",
            "ready": alive and self.ready.is_set(),
            "pid": p.pid if alive else None,
            "uptime": int(time.time() - self.started_at) if alive and self.started_at else None,
            "note": self.note, "last": self.log.last(),
        }


class Plc(Managed):
    paused = False

    def precheck(self):
        if not RENODE:
            return "Renode bulunamadı (/Applications/Renode.app)"
        if not PLC_ELF.exists():
            return "PLC firmware'i derlenmemiş: 'Derle' butonuna bas"

    MONITOR_PORT = 33334

    def command(self):
        # --disable-gui: pencere yok, loglar standart çıkışa (panele) akar.
        # --port: Renode monitörü bu TCP portunu dinler; "pause"/"start" komutlarını
        # oradan gönderiyoruz (terminaldeki (bluepill) isteminin yerine).
        env = {"DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1"}
        return [RENODE, "--disable-gui", "--port", str(self.MONITOR_PORT), "rtos.resc"], PLC_DIR, env

    def start(self, wait=False):
        self.paused = False
        return super().start(wait)

    def send(self, text: str) -> bool:
        if not self.proc or self.proc.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", self.MONITOR_PORT), timeout=2) as s:
                s.sendall(text.encode() + b"\r\n")
                time.sleep(0.3)             # komut işlensin, bağlantıyı hemen kesmeyelim
            self.log.add(f"[panel] monitör > {text}")
            return True
        except OSError as e:
            self.log.add(f"[panel] monitöre bağlanılamadı: {e}")
            return False

    def status(self):
        s = super().status()
        s["paused"] = self.paused and s["ready"]
        return s


class Sensor(Managed):
    scenario = "normal"

    def precheck(self):
        if not plc.status()["ready"]:
            return "Önce PLC (Renode) çalışıyor olmalı"

    def command(self):
        args = list(SCENARIOS[self.scenario]["args"])
        # Motorun sıcaklığı yeni senaryoya kaldığı yerden devam etsin
        # (yoksa simülatör 55 °C'den başlar, grafikte gerçek dışı bir sıçrama olur)
        t = last_temperature()
        if t is not None:
            args += ["--start-temp", f"{t:.2f}"]
        return [sys.executable, "-u", str(ROOT / "tools/sensor_feeder.py"), *args], ROOT, {}

    def status(self):
        s = super().status()
        s["scenario"] = self.scenario
        return s


class Gateway(Managed):
    def precheck(self):
        if not QEMU:
            return "qemu-system-xtensa bulunamadı (~/.espressif/tools/qemu-xtensa)"
        if not GW_FLASH.exists() or not GW_EFUSE.exists():
            return "Gateway imajı yok: 'Derle' butonuna bas (ya da bir kez idf.py qemu çalıştır)"
        if not plc.status()["ready"]:
            return "Önce PLC (Renode) çalışıyor olmalı: gateway onun Modbus portuna bağlanıyor"

    def command(self):
        # idf.py qemu'nun yaptığının aynısı, monitör olmadan:
        #   1. seri port (UART0, ESP log'ları) -> standart çıkış -> panel
        #   2. seri port (UART1, Modbus)       -> Renode'un 3456 portu
        cmd = [QEMU, "-M", "esp32", "-m", "4M",
               "-drive", f"file={GW_FLASH},if=mtd,format=raw",
               "-drive", f"file={GW_EFUSE},if=none,format=raw,id=efuse",
               "-global", "driver=nvram.esp32.efuse,property=drive,value=efuse",
               "-global", "driver=timer.esp32.timg,property=wdt_disable,value=true",
               "-nic", "user,model=open_eth",
               "-display", "none", "-monitor", "none",
               "-serial", "stdio", "-serial", "tcp:127.0.0.1:3456"]
        return cmd, GW_DIR, {}


class Dashboard(Managed):
    def precheck(self):
        if not NPM:
            return "npm bulunamadı (Node.js kurulu mu?)"
        if not (ROOT / "dashboard/node_modules").exists():
            return "dashboard/node_modules yok: önce 'npm install' (Derle butonu)"

    def command(self):
        return [NPM, "run", "dev", "--", "--strictPort"], ROOT / "dashboard", {"NO_COLOR": "1"}


# ---------------------------------------------------------------- Docker

class Docker:
    """Docker Compose servisleri. Sürekli bir süreç değil; durumu ara ara sorgulanır."""

    id, name = "docker", "Sunucu servisleri"
    desc = "Docker: Mosquitto (MQTT), InfluxDB, backend (FastAPI + model)"

    def __init__(self):
        self.log = LogBuffer()
        self.services: dict[str, str] = {}
        self.backend_ok = False
        self.daemon_ok = False
        self.busy = False
        self.note = ""
        threading.Thread(target=self._poll, daemon=True).start()

    def _run(self, args, timeout=180):
        self.log.add("[panel] $ docker " + " ".join(args))
        try:
            r = subprocess.run([DOCKER, *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            self.log.add(f"[panel] hata: {e}")
            return False
        for line in (r.stdout + r.stderr).splitlines():
            self.log.add(line)
        return r.returncode == 0

    def _poll(self):
        while True:
            if DOCKER:
                try:
                    r = subprocess.run([DOCKER, "compose", "ps", "--all", "--format", "json"], cwd=ROOT,
                                       capture_output=True, text=True, timeout=10)
                    self.daemon_ok = r.returncode == 0
                    svc = {}
                    if r.returncode == 0 and r.stdout.strip():
                        text = r.stdout.strip()
                        items = json.loads(text) if text.startswith("[") else [json.loads(l) for l in text.splitlines()]
                        for it in items:
                            svc[it.get("Service", it.get("Name", "?"))] = it.get("State", "?")
                    self.services = svc
                except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
                    self.daemon_ok = False
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2) as resp:
                        h = json.load(resp)
                        self.backend_ok = bool(h.get("mqtt_connected") and h.get("influxdb"))
                except Exception:
                    self.backend_ok = False
            time.sleep(3)

    def start(self, wait=False):
        if not DOCKER:
            self.note = "docker komutu bulunamadı"
            return self.note
        self.busy = True
        try:
            if subprocess.run([DOCKER, "info"], capture_output=True).returncode != 0:
                self.log.add("[panel] Docker Desktop kapalı, açılıyor...")
                subprocess.run(["open", "-a", "Docker"])
                for _ in range(90):
                    time.sleep(1)
                    if subprocess.run([DOCKER, "info"], capture_output=True).returncode == 0:
                        break
                else:
                    self.note = "Docker Desktop 90 sn içinde açılmadı"
                    return self.note
            if not self._run(["compose", "up", "-d"]):
                self.note = "docker compose up başarısız"
                return self.note
            self.note = ""
            for _ in range(60):                 # backend MQTT ve InfluxDB'ye bağlanana kadar
                if self.backend_ok:
                    break
                time.sleep(1)
            return None if self.backend_ok else "backend sağlıklı görünmüyor"
        finally:
            self.busy = False

    def stop(self):
        self.busy = True
        try:
            self._run(["compose", "stop"])
        finally:
            self.busy = False

    def status(self):
        running = [s for s, st in self.services.items() if st == "running"]
        if self.busy:
            state = "starting"
        elif self.services and len(running) == len(self.services):
            state = "running"
        elif running:
            state = "starting"
        else:
            state = "stopped"
        return {"id": self.id, "name": self.name, "desc": self.desc, "state": state,
                "ready": state == "running" and self.backend_ok, "pid": None, "uptime": None,
                "note": self.note or ("" if self.daemon_ok else "Docker Desktop kapalı"),
                "last": self.log.last(), "services": self.services}


# ---------------------------------------------------------------- bileşenler

docker = Docker()
plc = Plc("plc", "PLC", "STM32 Blue Pill · FreeRTOS · Renode", r"Modbus RTU slave basliyor", 30)
sensor = Sensor("sensor", "Sensör modülü", "Motor simülatörü · USART3", r"Gonderiliyor", 10)
gateway = Gateway("gateway", "Gateway", "ESP32 · ESP-IDF · QEMU", r"Broker'a baglandi", 45)
dash = Dashboard("dashboard", "Dashboard", "React · Vite · http://localhost:5173", r"Local:|ready in", 30)
MANAGED = {c.id: c for c in (plc, sensor, gateway, dash)}
ALL = {"docker": docker, **MANAGED}
START_ORDER = ["docker", "plc", "sensor", "gateway", "dashboard"]


def last_temperature():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/state", timeout=1) as r:
            s = json.load(r)
        t = s.get("telemetry") or {}
        if t.get("sensor_online") and not s.get("telemetry_stale"):
            return float(t["temp_c"])
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- işler (job)

class Job:
    """Aynı anda tek bir uzun iş (sıralı başlatma, derleme) çalışır."""

    def __init__(self):
        self.name, self.step, self.running, self.error = "", "", False, ""
        self.lock = threading.Lock()

    def run(self, name, fn):
        with self.lock:
            if self.running:
                return False
            self.name, self.step, self.running, self.error = name, "", True, ""

        def wrapper():
            try:
                fn(self)
            except Exception as e:                  # panelin kendisi çökmesin
                self.error = str(e)
            finally:
                self.running = False
        threading.Thread(target=wrapper, daemon=True).start()
        return True

    def status(self):
        return {"name": self.name, "step": self.step, "running": self.running, "error": self.error}


job = Job()


def start_all(j: Job):
    for cid in START_ORDER:
        comp = ALL[cid]
        if comp.status()["ready"]:
            continue
        j.step = f"{comp.name} başlatılıyor"
        err = comp.start(wait=True)
        if err:
            j.error = f"{comp.name}: {err}"
            return
        if cid == "plc":
            time.sleep(0.5)                 # Renode portları açsın
    j.step = "hepsi çalışıyor"


def stop_all(j: Job):
    for cid in reversed(START_ORDER):
        if cid == "docker":
            continue                         # veritabanı ayakta kalsın; ayrıca durdurulabilir
        j.step = f"{ALL[cid].name} durduruluyor"
        ALL[cid].stop()
    j.step = "durduruldu"


def run_logged(comp, cmd, cwd, shell=False):
    comp.log.add("[derle] $ " + (cmd if isinstance(cmd, str) else " ".join(cmd)))
    p = subprocess.Popen(cmd, cwd=cwd, shell=shell, executable="/bin/zsh" if shell else None,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         env={**os.environ, **UTF8_ENV}, text=True, errors="replace")
    for line in p.stdout:
        comp.log.add(line)
    code = p.wait()
    comp.log.add(f"[derle] çıkış kodu {code}")
    return code == 0


def build_plc(j: Job):
    j.step = "PLC derleniyor (pio run)"
    if not PIO:
        j.error = "PlatformIO (pio) bulunamadı"
        return
    if not run_logged(plc, [PIO, "run"], PLC_DIR):
        j.error = "PLC derlemesi başarısız, loglara bak"


def build_gateway(j: Job):
    if not IDF_ACTIVATE:
        j.error = "ESP-IDF aktivasyon betiği bulunamadı (~/.espressif/tools/activate_idf_v*.sh)"
        return
    j.step = "Gateway derleniyor (idf.py build + flash imajı)"
    merge = ("esptool --chip=esp32 merge-bin --output=build/qemu_flash.bin --pad-to-size=2MB "
             "--flash-mode dio --flash-freq 40m --flash-size 2MB "
             "0x1000 build/bootloader/bootloader.bin 0x8000 build/partition_table/partition-table.bin "
             "0x10000 build/esp32_gateway.bin")
    cmd = f"source '{IDF_ACTIVATE}' >/dev/null && idf.py build && {merge}"
    if not run_logged(gateway, cmd, GW_DIR, shell=True):
        j.error = "Gateway derlemesi başarısız, loglara bak"
    elif not GW_EFUSE.exists():
        j.error = "qemu_efuse.bin yok: bir kez terminalden 'idf.py qemu' çalıştırılmalı"


def build_dashboard(j: Job):
    j.step = "Dashboard paketleri kuruluyor (npm install)"
    if not NPM or not run_logged(dash, [NPM, "install"], ROOT / "dashboard"):
        j.error = "npm install başarısız"


def apply_scenario(j: Job, name: str):
    j.step = SCENARIOS[name]["title"]
    sensor.stop()
    sensor.scenario = name
    time.sleep(0.3)
    err = sensor.start(wait=True)
    if err:
        j.error = err


def cleanup(j: Job):
    """Panel dışında kalmış (ör. kapanan terminalden artan) süreçleri temizler."""
    j.step = "Artık süreçler temizleniyor"
    for c in MANAGED.values():
        c.stop()
    for pat in ("qemu-system-xtensa", "[Rr]enode", "vite", "sensor_feeder"):
        subprocess.run(["pkill", "-f", pat])
    j.step = "temizlendi"


# ---------------------------------------------------------------- HTTP

def port_in_use(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):          # her isteği konsola yazmasın
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            body = HTML.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/api/status":
            self._json({
                "components": [ALL[c].status() for c in START_ORDER],
                "job": job.status(),
                "scenarios": {k: v["title"] for k, v in SCENARIOS.items()},
                "tools": {"renode": bool(RENODE), "qemu": bool(QEMU), "pio": bool(PIO),
                          "npm": bool(NPM), "docker": bool(DOCKER), "idf": bool(IDF_ACTIVATE)},
            })
        elif u.path == "/api/logs":
            q = parse_qs(u.query)
            comp = ALL.get(q.get("c", [""])[0])
            if not comp:
                return self._json({"error": "bilinmeyen bileşen"}, 404)
            since = int(q.get("since", ["0"])[0])
            self._json({"lines": comp.log.since(since)})
        else:
            self._json({"error": "bulunamadı"}, 404)

    def do_POST(self):
        # Başka bir sitenin tarayıcı üzerinden bu panele komut göndermesini engelle
        origin = self.headers.get("Origin", "")
        if origin and urlparse(origin).hostname not in ("localhost", "127.0.0.1"):
            return self._json({"error": "izin yok"}, 403)

        parts = urlparse(self.path).path.strip("/").split("/")   # api/<bileşen>/<işlem>
        if len(parts) != 3 or parts[0] != "api":
            return self._json({"error": "bulunamadı"}, 404)
        _, target, action = parts

        if target == "all":
            fn = {"start": start_all, "stop": stop_all, "cleanup": cleanup}.get(action)
            if not fn:
                return self._json({"error": "bilinmeyen işlem"}, 400)
            ok = job.run({"start": "Hepsini başlat", "stop": "Hepsini durdur", "cleanup": "Temizlik"}[action], fn)
            return self._json({"ok": ok, "busy": not ok})

        if target == "scenario":
            if action not in SCENARIOS:
                return self._json({"error": "bilinmeyen senaryo"}, 400)
            ok = job.run("Senaryo", lambda j: apply_scenario(j, action))
            return self._json({"ok": ok, "busy": not ok})

        comp = ALL.get(target)
        if not comp:
            return self._json({"error": "bilinmeyen bileşen"}, 404)

        if action == "start":
            if target == "docker":
                ok = job.run("Docker başlatılıyor", lambda j: setattr(j, "error", docker.start() or ""))
                return self._json({"ok": ok, "busy": not ok})
            err = comp.start()
            return self._json({"ok": err is None, "error": err})
        if action == "stop":
            if target == "docker":
                ok = job.run("Docker durduruluyor", lambda j: docker.stop())
                return self._json({"ok": ok, "busy": not ok})
            threading.Thread(target=comp.stop, daemon=True).start()
            return self._json({"ok": True})
        if action == "restart" and target in MANAGED:
            ok = job.run(f"{comp.name} yeniden", lambda j: (comp.stop(), setattr(j, "error", comp.start(wait=True) or "")))
            return self._json({"ok": ok, "busy": not ok})
        if action == "build":
            fn = {"plc": build_plc, "gateway": build_gateway, "dashboard": build_dashboard}.get(target)
            if not fn:
                return self._json({"error": "bu bileşen derlenmez"}, 400)
            ok = job.run(f"{comp.name} derleniyor", fn)
            return self._json({"ok": ok, "busy": not ok})
        if target == "plc" and action in ("pause", "resume"):
            # Renode monitörüne komut: PLC'yi dondur / devam ettir (elektrik kesintisi gibi)
            if not plc.send("pause" if action == "pause" else "start"):
                return self._json({"ok": False, "error": "PLC çalışmıyor"})
            plc.paused = action == "pause"
            return self._json({"ok": True})
        return self._json({"error": "bilinmeyen işlem"}, 400)


def shutdown(*_):
    print("\nPanel kapanıyor, başlattığı süreçler durduruluyor...")
    for c in MANAGED.values():
        c.stop()
    os._exit(0)


def main():
    if port_in_use(PORT):
        print(f"{PORT} portu dolu. Panel zaten açık olabilir: http://localhost:{PORT}")
        sys.exit(1)
    missing = [n for n, p in (("Renode", RENODE), ("QEMU", QEMU), ("PlatformIO", PIO),
                              ("npm", NPM), ("docker", DOCKER), ("ESP-IDF", IDF_ACTIVATE)) if not p]
    if missing:
        print("Bulunamayan araçlar:", ", ".join(missing), "(ilgili butonlar hata verecek)")
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Kontrol paneli: http://localhost:{PORT}   (kapatmak için Ctrl+C)")
    if "--no-browser" not in sys.argv:
        subprocess.run(["open", f"http://localhost:{PORT}"], check=False)
    server.serve_forever()


if __name__ == "__main__":
    main()
