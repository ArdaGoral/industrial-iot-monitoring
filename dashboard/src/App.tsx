import { useEffect, useRef, useState } from "react";
import { useLiveData, type LinkStatus } from "./api";
import { Controls } from "./components/Controls";
import { Kpi } from "./components/Kpi";
import { EventLog, GatewayHealth } from "./components/SidePanels";
import { StatusBar, toHealth, type Link } from "./components/StatusBar";
import { TrendChart } from "./components/TrendChart";

const RANGES = [
  { min: 5, label: "5 dk" },
  { min: 30, label: "30 dk" },
  { min: 120, label: "2 sa" },
  { min: 1440, label: "24 sa" },
];

const STATUS_TR: Record<LinkStatus, string> = { online: "çevrimiçi", offline: "kopuk", unknown: "bilinmiyor" };

export default function App() {
  const [range, setRange] = useState(5);
  const live = useLiveData(range);
  const { snap, wsConnected, stale, addLog } = live;
  const t = snap.telemetry;
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  // Sensör kopmuşsa STM32 son değeri tutar: değerler var ama güncel değil
  const sensorOnline = t?.sensor_online ?? false;
  const valuesStale = stale || !sensorOnline;

  // PLC durumunu gateway bildiriyor. Gateway kopuksa son "PLC online" mesajı
  // eski bir bilgidir (broker'da retained kalmış): ona güvenmeyip "bilinmiyor" diyoruz.
  // Aynı mantık bir halka yukarı: sunucu ya da MQTT kopuksa gateway'i de bilemeyiz.
  const gatewayStatus: LinkStatus = wsConnected && snap.mqtt_connected ? snap.gateway_status : "unknown";
  const gatewayOnline = gatewayStatus === "online";
  const plcStatus: LinkStatus = gatewayOnline ? snap.plc_status : "unknown";

  // ---- zincir göstergeleri ----
  const links: Link[] = [
    { label: "Sunucu", health: wsConnected ? "ok" : "fault", detail: wsConnected ? "bağlı" : "bağlantı yok" },
    {
      label: "MQTT",
      health: !wsConnected ? "unknown" : snap.mqtt_connected ? "ok" : "fault",
      detail: !wsConnected ? "—" : snap.mqtt_connected ? "bağlı" : "broker yok",
    },
    { label: "Gateway", health: toHealth(gatewayStatus), detail: STATUS_TR[gatewayStatus] },
    {
      label: "PLC",
      health: toHealth(plcStatus),
      detail: !gatewayOnline && gatewayStatus !== "unknown" ? "gateway yok" : STATUS_TR[plcStatus],
    },
    {
      label: "Sensör",
      health: !t || stale ? "unknown" : sensorOnline ? "ok" : "fault",
      detail: !t || stale ? "veri yok" : sensorOnline ? "veri geliyor" : "kopuk",
    },
  ];

  // ---- durum değişikliklerini günlüğe yaz ----
  const prev = useRef<Record<string, string>>({});
  useEffect(() => {
    const cur: Record<string, string> = {
      Gateway: gatewayStatus,
      PLC: plcStatus,
      Sensör: t ? (sensorOnline ? "online" : "offline") : "unknown",
      Sıcaklık: t ? (t.temp_alarm ? "alarm" : "normal") : "unknown",
    };
    for (const [k, v] of Object.entries(cur)) {
      const p = prev.current[k];
      // İlk yükleme (unknown -> x) günlüğe yazılmaz; sadece gerçek değişiklikler
      if (p !== undefined && p !== "unknown" && p !== v && v !== "unknown") {
        if (k === "Sıcaklık")
          addLog(v === "alarm" ? "alarm" : "info",
                 v === "alarm" ? `Sıcaklık alarmı: ${t!.temp_c.toFixed(1)} °C ≥ ${t!.temp_alarm_limit_c.toFixed(1)} °C`
                               : "Sıcaklık normale döndü");
        else addLog(v === "offline" ? "alarm" : "info", `${k} ${STATUS_TR[v as LinkStatus] ?? v}`);
      }
      prev.current[k] = v;
    }
  }, [gatewayStatus, plcStatus, sensorOnline, t, addLog]);

  // Sunucu bağlantısı koptu / geri geldi
  const prevWs = useRef(false);
  const everConnected = useRef(false);
  useEffect(() => {
    if (wsConnected && !prevWs.current) {
      addLog("info", everConnected.current ? "Sunucuya yeniden bağlanıldı" : "Sunucuya bağlanıldı");
      everConnected.current = true;
    } else if (!wsConnected && prevWs.current) {
      addLog("alarm", "Sunucu bağlantısı koptu");
    }
    prevWs.current = wsConnected;
  }, [wsConnected, addLog]);

  // PLC "online" görünse bile 5 sn'dir veri gelmiyorsa komut göndermeyiz
  const controlsEnabled = plcStatus === "online" && !stale;
  const disabledReason = !wsConnected ? "Sunucuya bağlantı yok."
    : !snap.mqtt_connected ? "Sunucu MQTT broker'a bağlı değil."
    : !gatewayOnline ? "Gateway kopuk, komut gönderilemez."
    : plcStatus !== "online" ? "PLC çevrimiçi değil, komut gönderilemez."
    : "PLC'den güncel veri gelmiyor, komut gönderilemez.";

  const ageS = live.lastTelemetryAt ? Math.round((now - live.lastTelemetryAt) / 1000) : 0;
  const ageText = ageS < 90 ? `${ageS} sn` : ageS < 5400 ? `${Math.round(ageS / 60)} dk` : `${Math.round(ageS / 3600)} saat`;

  return (
    <div className="app">
      <header className="topbar">
        <div className="title">
          <h1>Motor 1</h1>
          <span className="subtitle">factory/motor1 · durum izleme</span>
        </div>
        <StatusBar links={links} />
        <div className="clock num">{new Date(now).toLocaleTimeString("tr-TR")}</div>
      </header>

      {valuesStale && t && (
        <div className="banner">
          {stale
            ? `Son ölçüm ${ageText} önce alındı. Gösterilen değerler güncel değil.`
            : "Sensör bağlantısı kopuk. PLC son okunan değeri tutuyor; değerler güncel değil."}
        </div>
      )}

      <section className="kpis">
        <Kpi label="Sıcaklık" value={t?.temp_c} unit="°C" digits={1} stale={valuesStale}
             alarm={t?.temp_alarm} note={t ? `eşik ${t.temp_alarm_limit_c.toFixed(1)} °C` : undefined} />
        <Kpi label="Akım" value={t?.current_a} unit="A" digits={2} stale={valuesStale} />
        <Kpi label="Titreşim RMS" value={t?.vib_rms} unit="g" digits={3} stale={valuesStale} />
        <Kpi label="Titreşim tepe" value={t?.vib_peak} unit="g" digits={3} stale={valuesStale} />
        <Kpi label="Kurtosis" value={t?.kurtosis} unit="" digits={2} stale={valuesStale}
             note="darbeli arızada yükselir" />
      </section>

      <div className="main-grid">
        <div className="trends">
          <div className="range" role="tablist" aria-label="Zaman aralığı">
            {RANGES.map((r) => (
              <button key={r.min} role="tab" aria-selected={range === r.min}
                      className={range === r.min ? "active" : ""} onClick={() => setRange(r.min)}>
                {r.label}
              </button>
            ))}
            {live.historyError && <span className="hint warn">Geçmiş alınamadı: {live.historyError}</span>}
          </div>

          <TrendChart title="Sıcaklık" unit="°C" minSpan={10} points={live.points} rangeMinutes={range} now={now}
                      series={[{ key: "temp_c", label: "Sargı", color: "var(--trend-1)", digits: 2 }]}
                      threshold={t ? { value: t.temp_alarm_limit_c, label: `alarm ${t.temp_alarm_limit_c.toFixed(0)} °C` } : null} />
          <TrendChart title="Titreşim" unit="g" minSpan={1} points={live.points} rangeMinutes={range} now={now}
                      series={[
                        { key: "vib_rms", label: "RMS", color: "var(--trend-1)", digits: 3 },
                        { key: "vib_peak", label: "Tepe", color: "var(--trend-2)", digits: 3 },
                      ]} />
          {/* Kurtosis ayrı grafikte: akımla aynı eksende düz çizgi gibi görünüp kayboluyordu */}
          <TrendChart title="Kurtosis" unit="" minSpan={4} points={live.points} rangeMinutes={range} now={now}
                      series={[{ key: "kurtosis", label: "Kurtosis", color: "var(--trend-1)", digits: 2 }]} />
          <TrendChart title="Motor akımı" unit="A" minSpan={2} points={live.points} rangeMinutes={range} now={now}
                      series={[{ key: "current_a", label: "Faz akımı", color: "var(--trend-1)", digits: 2 }]} />
        </div>

        <aside className="side">
          <Controls telemetry={t} enabled={controlsEnabled} disabledReason={disabledReason}
                    lastEvent={live.lastEvent} onLog={addLog} />
          <GatewayHealth hb={snap.heartbeat} stale={!gatewayOnline} />
          <EventLog log={live.log} />
        </aside>
      </div>
    </div>
  );
}
