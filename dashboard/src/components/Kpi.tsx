/*
 * Tek bir ölçüm kutusu. Değer bayatsa gri ve üstü çizgili gösterilir:
 * operatör eski bir değere bakıp "her şey yolunda" sanmasın.
 */
interface Props {
  label: string;
  value: number | null | undefined;
  unit: string;
  digits: number;
  stale: boolean;
  alarm?: boolean;
  note?: string;
}

export function Kpi({ label, value, unit, digits, stale, alarm = false, note }: Props) {
  const cls = ["kpi", stale ? "stale" : "", alarm && !stale ? "alarm" : ""].join(" ");
  return (
    <div className={cls}>
      <div className="kpi-label">
        {label}
        {stale && <span className="badge">BAYAT</span>}
        {alarm && !stale && <span className="badge alarm">ALARM</span>}
      </div>
      <div className="kpi-value num">
        <span className="kpi-number">{value == null ? "—" : value.toFixed(digits)}</span>
        <small>{unit}</small>
      </div>
      {note && <div className="kpi-note">{note}</div>}
    </div>
  );
}
