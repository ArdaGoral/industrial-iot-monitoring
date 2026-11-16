/*
 * Backend ile konuşan her şey burada: tipler, REST çağrıları ve canlı veri hook'u.
 *
 * Veri akışı:
 *   1) Sayfa açılınca /api/history ile seçili aralığın geçmişi çekilir
 *   2) /ws WebSocket'ine bağlanılır; önce "snapshot" (anlık durum) gelir,
 *      sonra her yeni telemetri/olay/durum değişikliği ayrı mesaj olarak gelir
 *   3) Bağlantı koparsa artan bekleme süreleriyle yeniden bağlanılır
 */
import { useCallback, useEffect, useRef, useState } from "react";

// ---------------- tipler (backend/app ile aynı alanlar) ----------------

export interface Telemetry {
  vib_rms: number;
  vib_peak: number;
  kurtosis: number;
  temp_c: number;
  current_a: number;
  counter: number;
  sensor_online: boolean;
  temp_alarm: boolean;
  relay: number;
  temp_alarm_limit_c: number;
}

export interface Heartbeat {
  uptime_s: number;
  free_heap: number;
  modbus_ok: number;
  modbus_err: number;
}

export type LinkStatus = "online" | "offline" | "unknown";

/** Backend'deki anomali modelinin her telemetri için verdiği sonuç */
export interface Anomaly {
  score: number;          // > 1 ise o pencere anormal
  is_anomaly: boolean;    // art arda 3 pencere kuralıyla (debounce) alarm durumu
  top_feature: string;    // normalden en çok sapan özellik
  top_feature_tr: string;
  top_z: number;          // o özelliğin kaç standart sapma uzakta olduğu
}

export interface CommandEvent {
  cmd?: string;
  topic?: string;
  value?: number;
  result?: string;
  exception?: number;
  time: string;
}

export interface Snapshot {
  mqtt_connected: boolean;
  gateway_status: LinkStatus;
  plc_status: LinkStatus;
  telemetry: Telemetry | null;
  anomaly: Anomaly | null;
  heartbeat: Heartbeat | null;
  events: CommandEvent[];
}

export const NUMERIC_KEYS = ["vib_rms", "vib_peak", "kurtosis", "temp_c", "current_a", "anomaly_score"] as const;
export type NumericKey = (typeof NUMERIC_KEYS)[number];

/** Grafik noktası: t = unix milisaniye */
export type TrendPoint = { t: number } & { [K in NumericKey]: number | null };

/** Dashboard'un kendi tuttuğu olay günlüğü satırı */
export interface LogEntry {
  id: number;
  t: number;
  level: "info" | "warn" | "alarm";
  text: string;
}

// ---------------- REST ----------------

async function postJson(path: string, body: unknown) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : (j.detail?.[0]?.msg ?? detail);
    } catch {
      /* gövde JSON değilse durum kodu yeter */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const sendRelay = (on: boolean) => postJson("/api/cmd/relay", { on });
export const sendTempLimit = (celsius: number) => postJson("/api/cmd/temp_alarm_limit", { celsius });

async function fetchHistory(minutes: number): Promise<TrendPoint[]> {
  const res = await fetch(`/api/history?minutes=${minutes}`);
  if (!res.ok) throw new Error(`gecmis alinamadi (${res.status})`);
  const body: { points: Array<{ time: string } & Record<NumericKey, number | null>> } = await res.json();
  return body.points.map((p) => ({
    t: Date.parse(p.time),
    vib_rms: p.vib_rms,
    vib_peak: p.vib_peak,
    kurtosis: p.kurtosis,
    temp_c: p.temp_c,
    current_a: p.current_a,
    anomaly_score: p.anomaly_score ?? null,
  }));
}

// ---------------- canlı veri hook'u ----------------

const STALE_AFTER_MS = 5000;   // backend ile aynı eşik

const EMPTY: Snapshot = {
  mqtt_connected: false,
  gateway_status: "unknown",
  plc_status: "unknown",
  telemetry: null,
  anomaly: null,
  heartbeat: null,
  events: [],
};

export interface LiveData {
  snap: Snapshot;
  points: TrendPoint[];
  wsConnected: boolean;
  /** Son telemetriden bu yana 5 sn'den fazla geçtiyse true */
  stale: boolean;
  lastTelemetryAt: number | null;
  historyError: string | null;
  log: LogEntry[];
  addLog: (level: LogEntry["level"], text: string) => void;
  /** Son komut sonucu (Controls bileşeni bekleyen komutu bununla eşleştirir) */
  lastEvent: CommandEvent | null;
}

export function useLiveData(rangeMinutes: number): LiveData {
  const [snap, setSnap] = useState<Snapshot>(EMPTY);
  const [points, setPoints] = useState<TrendPoint[]>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const [lastTelemetryAt, setLastTelemetryAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [lastEvent, setLastEvent] = useState<CommandEvent | null>(null);

  const rangeRef = useRef(rangeMinutes);
  rangeRef.current = rangeMinutes;
  const logId = useRef(0);

  const addLog = useCallback((level: LogEntry["level"], text: string) => {
    setLog((l) => [{ id: ++logId.current, t: Date.now(), level, text }, ...l].slice(0, 100));
  }, []);

  // Saniyede bir saat tik'i: bayatlık ve grafik ekseni bununla güncellenir
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  // Aralık değişince geçmişi yeniden çek
  useEffect(() => {
    let cancelled = false;
    fetchHistory(rangeMinutes)
      .then((p) => {
        if (cancelled) return;
        setHistoryError(null);
        // Geçmiş gelene kadar WebSocket'ten gelen canlı noktaları kaybetme
        setPoints((live) => {
          const lastHist = p.length ? p[p.length - 1].t : 0;
          return [...p, ...live.filter((x) => x.t > lastHist)];
        });
      })
      .catch((e: Error) => !cancelled && setHistoryError(e.message));
    return () => {
      cancelled = true;
    };
  }, [rangeMinutes]);

  // WebSocket bağlantısı (bileşen yaşadığı sürece açık, koparsa yeniden bağlanır)
  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let closedByUs = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);

      ws.onopen = () => {
        retry = 0;
        setWsConnected(true);
      };

      ws.onclose = () => {
        // Bileşen kapanırken biz kapattıysak hiçbir şeye dokunma. Bu kontrol önce
        // gelmeli: yoksa eski bağlantının geç gelen "kapandı" olayı, yeni açılmış
        // bağlantıyı "kopuk" gösterir (React StrictMode'da efekt iki kez çalışır).
        if (closedByUs) return;
        setWsConnected(false);
        const delay = Math.min(1000 * 2 ** retry, 10000);   // 1, 2, 4, 8, 10, 10... sn
        retry++;
        timer = setTimeout(connect, delay);
      };

      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        switch (msg.type) {
          case "snapshot": {
            const s = msg.data;
            setSnap({
              mqtt_connected: s.mqtt_connected,
              gateway_status: s.gateway_status,
              plc_status: s.plc_status,
              telemetry: s.telemetry,
              anomaly: s.anomaly ?? null,
              heartbeat: s.heartbeat,
              events: s.events ?? [],
            });
            if (s.telemetry && s.telemetry_age_s != null)
              setLastTelemetryAt(Date.now() - s.telemetry_age_s * 1000);
            break;
          }
          case "telemetry": {
            const d: Telemetry = msg.data;
            const t = Date.parse(msg.time);
            const a: Anomaly | null = msg.anomaly ?? null;
            setSnap((s) => ({ ...s, telemetry: d, anomaly: a }));
            setLastTelemetryAt(Date.now());
            // Sensör kopukken gelen değerler eski ölçümün tekrarı: grafiğe ekleme
            if (!d.sensor_online) break;
            setPoints((p) => {
              const cutoff = Date.now() - rangeRef.current * 60_000;
              const next = p.length && p[0].t < cutoff ? p.filter((x) => x.t >= cutoff) : p;
              return [...next, {
                t, vib_rms: d.vib_rms, vib_peak: d.vib_peak, kurtosis: d.kurtosis,
                temp_c: d.temp_c, current_a: d.current_a, anomaly_score: a ? a.score : null,
              }];
            });
            break;
          }
          case "anomaly": {
            const e = msg.data;
            const why = `${e.top_feature_tr} ${e.top_z > 0 ? "+" : ""}${e.top_z}σ`;
            addLog(e.started ? "alarm" : "info",
                   e.started ? `Anomali başladı (skor ${e.score.toFixed(1)}, baskın etken: ${why})`
                             : "Anomali sona erdi, davranış normale döndü");
            break;
          }
          case "heartbeat":
            setSnap((s) => ({ ...s, heartbeat: msg.data }));
            break;
          case "status":
            setSnap((s) => ({ ...s, [`${msg.component}_status`]: msg.value }));
            break;
          case "backend":
            setSnap((s) => ({ ...s, mqtt_connected: msg.mqtt_connected }));
            break;
          case "event":
            setSnap((s) => ({ ...s, events: [msg.data, ...s.events].slice(0, 50) }));
            setLastEvent(msg.data);
            break;
        }
      };
    };

    connect();
    return () => {
      closedByUs = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, [addLog]);

  const stale = lastTelemetryAt == null || now - lastTelemetryAt > STALE_AFTER_MS;

  return { snap, points, wsConnected, stale, lastTelemetryAt, historyError, log, addLog, lastEvent };
}
