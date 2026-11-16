"""
FastAPI uygulaması: dashboard'un konuştuğu tek kapı.

  GET  /api/health                  servis sağlığı
  GET  /api/state                   son bilinen durum (anlık görüntü)
  GET  /api/history?minutes=30      geçmiş telemetri (InfluxDB)
  GET  /api/events                  son komut olayları
  POST /api/cmd/relay               {"on": true}
  POST /api/cmd/temp_alarm_limit    {"celsius": 65}
  WS   /ws                          canlı akış

Tarayıcıda http://localhost:8000/docs adresi bu API'yi otomatik belgeler ve
her uç noktayı oradan deneyebilirsin (Swagger UI).
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .mqtt_bridge import MqttBridge
from .state import state
from .storage import Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")


# ---------------- WebSocket yayıncısı ----------------

class Hub:
    """Bağlı tüm WebSocket istemcilerine aynı mesajı gönderir."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def broadcast_forever(self, outbox: asyncio.Queue):
        while True:
            msg = await outbox.get()
            dead = []
            for ws in list(self.clients):
                try:
                    await ws.send_json(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:                 # bağlantısı kopmuş istemcileri temizle
                self.clients.discard(ws)


hub = Hub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    outbox: asyncio.Queue = asyncio.Queue(maxsize=1000)
    app.state.storage = Storage()
    app.state.bridge = MqttBridge(app.state.storage, loop, outbox)
    app.state.bridge.start()
    task = asyncio.create_task(hub.broadcast_forever(outbox))
    log.info("Backend basladi")
    yield
    task.cancel()
    app.state.bridge.stop()
    app.state.storage.close()


app = FastAPI(title="Endustriyel Izleme API", version="0.1.0", lifespan=lifespan)

# Geliştirmede dashboard başka bir portta (Vite: 5173) çalışacak; tarayıcı izin versin
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------- REST ----------------

@app.get("/api/health")
def health():
    return {
        "mqtt_connected": state.mqtt_connected,
        "influxdb": app.state.storage.ping(),
        "mqtt_stats": app.state.bridge.stats,
        "websocket_clients": len(hub.clients),
    }


@app.get("/api/state")
def get_state():
    return state.snapshot()


@app.get("/api/history")
def get_history(minutes: int = Query(30, ge=1, le=1440, description="Kac dakika geriye")):
    try:
        return {"minutes": minutes, "points": app.state.storage.history(minutes)}
    except Exception as e:
        log.warning("Gecmis sorgusu basarisiz: %s", e)
        raise HTTPException(status_code=503, detail="Veritabanina ulasilamiyor")


@app.get("/api/events")
def get_events():
    return state.snapshot()["events"]


class RelayCommand(BaseModel):
    on: bool


class TempLimitCommand(BaseModel):
    celsius: float = Field(ge=0, le=150, description="Sicaklik alarm esigi (C)")


def _send(name: str, payload: str):
    if not state.mqtt_connected:
        raise HTTPException(status_code=503, detail="MQTT broker'a bagli degil")
    if not app.state.bridge.publish_command(name, payload):
        raise HTTPException(status_code=502, detail="Komut yayinlanamadi")
    # Komutun STM32'ye ulaşıp ulaşmadığı sonradan 'event' olarak gelir (WebSocket)
    return {"sent": True, "topic": name, "payload": payload}


@app.post("/api/cmd/relay", status_code=202)
def cmd_relay(cmd: RelayCommand):
    return _send("relay", "1" if cmd.on else "0")


@app.post("/api/cmd/temp_alarm_limit", status_code=202)
def cmd_temp_limit(cmd: TempLimitCommand):
    return _send("temp_alarm_limit", f"{cmd.celsius:.2f}")


# ---------------- WebSocket ----------------

@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    hub.clients.add(ws)
    try:
        await ws.send_json({"type": "snapshot", "data": state.snapshot()})
        while True:
            await ws.receive_text()         # istemciden bir şey beklemiyoruz; bağlantıyı açık tut
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)
