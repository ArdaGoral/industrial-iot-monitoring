/* Gateway sağlığı ve olay günlüğü */
import type { Heartbeat, LogEntry } from "../api";

function fmtUptime(s: number) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h} sa ${m} dk` : m ? `${m} dk ${sec} sn` : `${sec} sn`;
}

export function GatewayHealth({ hb, stale }: { hb: Heartbeat | null; stale: boolean }) {
  const total = hb ? hb.modbus_ok + hb.modbus_err : 0;
  const errRate = hb && total ? (hb.modbus_err / total) * 100 : 0;
  return (
    <section className={`panel health ${stale ? "stale" : ""}`}>
      <header className="panel-head"><h3>Gateway (ESP32)</h3></header>
      <dl className="kv">
        <dt>Çalışma süresi</dt><dd className="num">{hb ? fmtUptime(hb.uptime_s) : "—"}</dd>
        <dt>Boş heap</dt><dd className="num">{hb ? `${(hb.free_heap / 1024).toFixed(1)} KB` : "—"}</dd>
        <dt>Modbus başarılı</dt><dd className="num">{hb ? hb.modbus_ok.toLocaleString("tr-TR") : "—"}</dd>
        <dt>Modbus hata</dt>
        <dd className={`num ${errRate > 1 ? "warn" : ""}`}>
          {hb ? `${hb.modbus_err.toLocaleString("tr-TR")} (%${errRate.toFixed(1)})` : "—"}
        </dd>
      </dl>
    </section>
  );
}

export function EventLog({ log }: { log: LogEntry[] }) {
  return (
    <section className="panel log">
      <header className="panel-head"><h3>Olay günlüğü</h3></header>
      {log.length === 0 ? (
        <p className="hint">Henüz olay yok.</p>
      ) : (
        <ul>
          {log.map((e) => (
            <li key={e.id} className={e.level}>
              <time className="num">
                {new Date(e.t).toLocaleTimeString("tr-TR")}
              </time>
              <span>{e.text}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
