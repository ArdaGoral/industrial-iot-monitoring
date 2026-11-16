/*
 * Kütüphanesiz SVG trend grafiği.
 *
 * - X ekseni her zaman "şimdi - aralık ... şimdi": veri gelmese de eksen kayar,
 *   böylece veri kesintisi grafikte boşluk olarak görünür (gizlenmez).
 * - Ardışık iki nokta arasında GAP_MS'den fazla süre varsa çizgi koparılır.
 * - Eşik (alarm sınırı) verilirse kesikli kırmızı çizgi olarak çizilir.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { NumericKey, TrendPoint } from "../api";

export interface Series {
  key: NumericKey;
  label: string;
  color: string;      // CSS değişkeni, ör. "var(--trend-1)"
  digits: number;
}

interface Props {
  title: string;
  unit: string;
  series: Series[];
  points: TrendPoint[];
  rangeMinutes: number;
  now: number;
  threshold?: { value: number; label: string } | null;
}

const H = 180;
const PAD = { l: 44, r: 12, t: 10, b: 22 };

function niceTicks(min: number, max: number, count = 4): number[] {
  const span = max - min || 1;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

const hhmm = (t: number, withSec: boolean) =>
  new Date(t).toLocaleTimeString("tr-TR", {
    hour: "2-digit", minute: "2-digit", ...(withSec ? { second: "2-digit" } : {}),
  });

export function TrendChart({ title, unit, series, points, rangeMinutes, now, threshold }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hoverX, setHoverX] = useState<number | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(240, e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const x0 = now - rangeMinutes * 60_000;
  const x1 = now;
  const visible = useMemo(() => points.filter((p) => p.t >= x0 && p.t <= x1), [points, x0, x1]);

  // Y aralığı: görünen veriler + eşik; biraz pay bırak
  const [y0, y1] = useMemo(() => {
    const vals: number[] = [];
    for (const p of visible) for (const s of series) { const v = p[s.key]; if (v != null) vals.push(v); }
    if (threshold) vals.push(threshold.value);
    if (!vals.length) return [0, 1];
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.15 || Math.abs(hi) * 0.05 || 1;
    lo -= pad; hi += pad;
    return [lo, hi];
  }, [visible, series, threshold]);

  const iw = width - PAD.l - PAD.r;
  const ih = H - PAD.t - PAD.b;
  const sx = (t: number) => PAD.l + ((t - x0) / (x1 - x0)) * iw;
  const sy = (v: number) => PAD.t + (1 - (v - y0) / (y1 - y0)) * ih;

  // Kesinti eşiği: aralığa göre (downsample penceresi büyüdükçe artar)
  const gapMs = Math.max(5000, ((rangeMinutes * 60) / 600) * 3000);

  const paths = series.map((s) => {
    let d = "";
    let prevT: number | null = null;
    for (const p of visible) {
      const v = p[s.key];
      if (v == null) { prevT = null; continue; }
      const cmd = prevT == null || p.t - prevT > gapMs ? "M" : "L";
      d += `${cmd}${sx(p.t).toFixed(1)},${sy(v).toFixed(1)}`;
      prevT = p.t;
    }
    return d;
  });

  const yTicks = niceTicks(y0, y1);
  const xTicks = Array.from({ length: 5 }, (_, i) => x0 + ((x1 - x0) * i) / 4);

  // Fare altındaki en yakın nokta
  let hover: TrendPoint | null = null;
  if (hoverX != null && visible.length) {
    const t = x0 + ((hoverX - PAD.l) / iw) * (x1 - x0);
    hover = visible.reduce((a, b) => (Math.abs(b.t - t) < Math.abs(a.t - t) ? b : a));
  }
  const last = visible.length ? visible[visible.length - 1] : null;
  const shown = hover ?? last;

  return (
    <section className="panel trend">
      <header className="panel-head">
        <h3>{title}</h3>
        <div className="legend">
          {series.map((s) => (
            <span key={s.key} className="legend-item">
              <i style={{ background: s.color }} />
              {s.label}
              <b className="num">{shown?.[s.key] != null ? shown[s.key]!.toFixed(s.digits) : "—"}</b>
              <small>{unit}</small>
            </span>
          ))}
          {hover && <span className="legend-time num">{hhmm(hover.t, true)}</span>}
        </div>
      </header>

      <div ref={wrapRef} className="chart-wrap">
        <svg
          width={width}
          height={H}
          onMouseMove={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            const x = e.clientX - r.left;
            setHoverX(x >= PAD.l && x <= width - PAD.r ? x : null);
          }}
          onMouseLeave={() => setHoverX(null)}
        >
          {yTicks.map((v) => (
            <g key={v}>
              <line className="grid" x1={PAD.l} x2={width - PAD.r} y1={sy(v)} y2={sy(v)} />
              <text className="tick" x={PAD.l - 6} y={sy(v) + 3} textAnchor="end">
                {Math.abs(v) >= 100 ? v.toFixed(0) : +v.toFixed(3)}
              </text>
            </g>
          ))}
          {xTicks.map((t, i) => (
            <text key={i} className="tick" x={sx(t)} y={H - 6}
                  textAnchor={i === 0 ? "start" : i === 4 ? "end" : "middle"}>
              {hhmm(t, rangeMinutes <= 5)}
            </text>
          ))}

          {threshold && threshold.value >= y0 && threshold.value <= y1 && (
            <g>
              <line className="threshold" x1={PAD.l} x2={width - PAD.r}
                    y1={sy(threshold.value)} y2={sy(threshold.value)} />
              <text className="threshold-label" x={width - PAD.r - 4} y={sy(threshold.value) - 4}
                    textAnchor="end">{threshold.label}</text>
            </g>
          )}

          {paths.map((d, i) => (
            <path key={series[i].key} d={d} fill="none" stroke={series[i].color}
                  strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" />
          ))}

          {hover && (
            <line className="crosshair" x1={sx(hover.t)} x2={sx(hover.t)} y1={PAD.t} y2={H - PAD.b} />
          )}

          {!visible.length && (
            <text className="empty" x={PAD.l + iw / 2} y={PAD.t + ih / 2 - 14} textAnchor="middle">
              Bu aralıkta veri yok
            </text>
          )}
        </svg>
      </div>
    </section>
  );
}
