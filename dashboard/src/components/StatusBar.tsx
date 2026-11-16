/*
 * Zincirin her halkası için bir gösterge:
 *   Sunucu (WebSocket) → MQTT → Gateway (ESP32) → PLC (STM32) → Sensör
 *
 * ISA-101 (yüksek performanslı HMI) ilkesi: normal durum sakin gri,
 * renk sadece anormal durumda. Operatörün gözü sadece sorunu yakalasın.
 */
import type { LinkStatus } from "../api";

export type Health = "ok" | "fault" | "unknown";

export interface Link {
  label: string;
  health: Health;
  detail: string;
}

export function toHealth(s: LinkStatus): Health {
  return s === "online" ? "ok" : s === "offline" ? "fault" : "unknown";
}

export function StatusBar({ links }: { links: Link[] }) {
  return (
    <ol className="chain" aria-label="Haberlesme zinciri">
      {links.map((l) => (
        <li key={l.label} className={`link ${l.health}`} title={l.detail}>
          <span className="dot" aria-hidden />
          <span className="link-label">{l.label}</span>
          <span className="link-detail">{l.detail}</span>
        </li>
      ))}
    </ol>
  );
}
