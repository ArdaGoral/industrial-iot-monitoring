/*
 * Komut paneli.
 *
 * Komutlar asenkron: API "ilettim" der (202), STM32'nin uyguladığı bilgisi
 * sonradan "event" olarak gelir. Bu yüzden her komut üç aşamadan geçer:
 *   gönderiliyor → (event gelince) uygulandı / hata  — 5 sn'de gelmezse: cevap yok
 */
import { useEffect, useState, type FormEvent } from "react";
import { sendRelay, sendTempLimit, type CommandEvent, type Telemetry } from "../api";

type CmdName = "relay" | "temp_alarm_limit";

interface Pending {
  cmd: CmdName;
  since: number;
  label: string;
}

interface Outcome {
  ok: boolean;
  text: string;
}

const TIMEOUT_MS = 5000;

interface Props {
  telemetry: Telemetry | null;
  enabled: boolean;
  disabledReason: string;
  lastEvent: CommandEvent | null;
  onLog: (level: "info" | "warn" | "alarm", text: string) => void;
}

export function Controls({ telemetry, enabled, disabledReason, lastEvent, onLog }: Props) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [limitInput, setLimitInput] = useState("");

  // Bekleyen komutun sonucu geldi mi?
  useEffect(() => {
    if (!pending || !lastEvent || lastEvent.cmd !== pending.cmd) return;
    if (Date.parse(lastEvent.time) < pending.since - 2000) return;    // eski olay
    const ok = lastEvent.result === "ok";
    const text = ok ? `${pending.label}: uygulandı` : `${pending.label}: ${lastEvent.result}`;
    setOutcome({ ok, text });
    onLog(ok ? "info" : "warn", text);
    setPending(null);
  }, [lastEvent, pending, onLog]);

  // Zaman aşımı
  useEffect(() => {
    if (!pending) return;
    const id = setTimeout(() => {
      const text = `${pending.label}: PLC'den cevap gelmedi`;
      setOutcome({ ok: false, text });
      onLog("warn", text);
      setPending(null);
    }, TIMEOUT_MS);
    return () => clearTimeout(id);
  }, [pending, onLog]);

  async function run(cmd: CmdName, label: string, fn: () => Promise<unknown>) {
    setOutcome(null);
    setPending({ cmd, since: Date.now(), label });
    onLog("info", `${label}: gönderildi`);
    try {
      await fn();
    } catch (e) {
      const text = `${label}: ${(e as Error).message}`;
      setOutcome({ ok: false, text });
      onLog("warn", text);
      setPending(null);
    }
  }

  const relayOn = telemetry?.relay === 1;
  const busy = pending != null;
  const disabled = !enabled || busy;

  const submitLimit = (e: FormEvent) => {
    e.preventDefault();
    const c = Number(limitInput.replace(",", "."));
    if (!Number.isFinite(c) || c < 0 || c > 150) {
      setOutcome({ ok: false, text: "Eşik 0–150 °C arasında olmalı" });
      return;
    }
    run("temp_alarm_limit", `Sıcaklık eşiği ${c.toFixed(1)} °C`, () => sendTempLimit(c));
    setLimitInput("");
  };

  return (
    <section className="panel controls">
      <header className="panel-head"><h3>Kontrol</h3></header>

      {!enabled && <p className="hint warn">{disabledReason}</p>}

      <div className="control-row">
        <div>
          <div className="control-label">Röle (motor kontaktörü)</div>
          <div className={`relay-state ${relayOn ? "on" : ""}`}>
            {telemetry == null ? "—" : relayOn ? "ENERJİLİ" : "ENERJİSİZ"}
          </div>
        </div>
        <div className="btn-group">
          <button disabled={disabled || relayOn}
                  onClick={() => run("relay", "Röle enerjilendir", () => sendRelay(true))}>
            Enerjilendir
          </button>
          <button disabled={disabled || !relayOn}
                  onClick={() => run("relay", "Röle bırak", () => sendRelay(false))}>
            Bırak
          </button>
        </div>
      </div>

      <form className="control-row" onSubmit={submitLimit}>
        <div>
          <label className="control-label" htmlFor="limit">Sıcaklık alarm eşiği</label>
          <div className="num current-limit">
            şu an {telemetry ? telemetry.temp_alarm_limit_c.toFixed(1) : "—"} °C
          </div>
        </div>
        <div className="btn-group">
          <input id="limit" inputMode="decimal" placeholder="°C" value={limitInput}
                 onChange={(e) => setLimitInput(e.target.value)} disabled={disabled} />
          <button type="submit" disabled={disabled || !limitInput.trim()}>Uygula</button>
        </div>
      </form>

      <div className="cmd-status" aria-live="polite">
        {pending && <span className="pending">{pending.label}: PLC onayı bekleniyor…</span>}
        {!pending && outcome && (
          <span className={outcome.ok ? "ok" : "fail"}>{outcome.ok ? "✓ " : "✕ "}{outcome.text}</span>
        )}
      </div>
    </section>
  );
}
