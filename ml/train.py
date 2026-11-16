"""
Faz 5 - Anomali tespit modeli (Isolation Forest)

Çalıştırma (proje kökünden, .venv açıkken):
    python ml/train.py

Neden sadece NORMAL veriyle eğitiyoruz?
  Sahada arıza verisi nadirdir ve etiketlenmemiştir: bir motor bozulana kadar
  "rulman arızası şöyle görünür" diye örneğimiz olmaz. Bu yüzden model sadece
  "normal çalışma nasıl görünür" öğrenir; normalden sapan her şeyi anomali sayar.
  Arıza verisini sadece DEĞERLENDİRME için kullanıyoruz, eğitimde asla.

Isolation Forest nasıl çalışır?
  Veriyi rastgele eksen/eşiklerle tekrar tekrar ikiye böler. Normal noktalar
  kalabalık bölgededir; onları tek başına ayırmak için çok bölme gerekir.
  Anormal noktalar ise yalnızdır; birkaç bölmede izole olurlar.
  Kısa yol = anomali.

Neden yanına z-skoru da ekledik?
  Isolation Forest bir noktanın ne kadar KOLAY ayrıldığını ölçer, ne kadar UZAK
  olduğunu değil. Eğitim aralığının dışına çıkan bir değer 60 °C de olsa 100 °C de
  olsa aynı kolaylıkla ayrılır: skor doyar. Testte 100 °C'lik motor, normal verideki
  gürültü noktalarıyla aynı skoru aldı. Bu yüzden her özelliğin normalden kaç
  standart sapma uzak olduğunu (z-skoru) ikinci bir bileşen olarak ekliyoruz:
    - Isolation Forest: özelliklerin ALIŞILMADIK BİRLİKTELİĞİNİ yakalar
    - z-skoru: tek bir özelliğin AŞIRI SAPMASINI yakalar
  Her iki skor kendi eşiğine bölünür; son skor ikisinin büyüğüdür. Eşik = 1.0.

Çıktılar:
  ml/model/anomaly_model.joblib   backend'in yükleyeceği model + eşik
  ml/reports/egitim_raporu.md     sayılarla değerlendirme
  ml/reports/*.png                grafikler
"""
import json
import os
import sys
from datetime import datetime, timezone

import joblib
import matplotlib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

matplotlib.use("Agg")                 # ekran açmadan PNG üret
import matplotlib.pyplot as plt       # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from sensor_sim import STEADY_TEMP, MotorSimulator   # noqa: E402

ML_DIR = os.path.join(ROOT, "ml")
MODEL_DIR = os.path.join(ML_DIR, "model")
REPORT_DIR = os.path.join(ML_DIR, "reports")

# Backend'in telemetriden aldığı alanlarla birebir aynı isim ve sıra
FEATURES = ["vib_rms", "vib_peak", "kurtosis", "temp_c", "current_a"]

# Sahte alarm oranı hedefi: normal verinin %0.5'i eşiği aşabilir.
# Ayrıca alarm için art arda 3 anomali şartı (debounce) bunu daha da düşürür.
THRESHOLD_QUANTILE = 0.9975   # her bileşen için; birleşince ~%0.5
DEBOUNCE = 3
FEEDER_INTERVAL_S = 0.5          # sensor_feeder.py varsayılanı


def quantize(row: dict) -> dict:
    """
    Modelin canlıda göreceği çözünürlüğe yuvarla. Değerler STM32 register'larından
    tamsayı olarak geçiyor (vib x1000, kurtosis x100, sıcaklık x100, akım x1000).
    Eğitim verisi de aynı çözünürlükte olmalı.
    """
    return {
        "vib_rms": round(row["vib_rms"], 3),
        "vib_peak": round(row["vib_peak"], 3),
        "kurtosis": round(row["vib_kurtosis"], 2),
        "temp_c": round(row["temp_c"], 2),
        "current_a": round(row["current_a"], 3),
    }


def run(sim: MotorSimulator, mode: str, severities) -> pd.DataFrame:
    rows = []
    for i, sev in enumerate(severities):
        r = quantize(sim.read_window(mode, float(sev)))
        r.update({"mode": mode, "severity": float(sev), "step": i})
        rows.append(r)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- veri
def make_data():
    print("Veri uretiliyor...")
    # Eğitim ve doğrulama farklı seed: model ezber yapıyor mu görelim
    train = run(MotorSimulator(seed=100, start_temp=STEADY_TEMP), "normal", np.zeros(3000))
    val = run(MotorSimulator(seed=200, start_temp=STEADY_TEMP), "normal", np.zeros(2000))

    # Arıza taraması: her şiddet seviyesinde 60 pencere (sıcaklık oturabilsin)
    sweep = []
    levels = np.round(np.arange(0.05, 1.0001, 0.05), 2)
    for k, mode in enumerate(["bearing", "imbalance", "overheating"]):
        sim = MotorSimulator(seed=300 + k, start_temp=STEADY_TEMP)
        sweep.append(run(sim, mode, np.repeat(levels, 60)))
    sweep = pd.concat(sweep, ignore_index=True)

    # Yavaş bozulma: dashboard'daki testin aynısı (her ölçümde +0.003)
    degrade = []
    for k, mode in enumerate(["bearing", "imbalance", "overheating"]):
        sim = MotorSimulator(seed=400 + k, start_temp=STEADY_TEMP)
        sev = np.minimum(np.arange(500) * 0.003, 1.0)
        degrade.append(run(sim, mode, sev))
    return train, val, sweep, degrade


# ---------------------------------------------------------------- model
def if_score(model: Pipeline, df: pd.DataFrame) -> np.ndarray:
    # decision_function: pozitif = normal, negatif = anormal.
    # Ters çeviriyoruz ki "büyük skor = daha anormal" olsun (okuması kolay).
    return -model.decision_function(df[FEATURES].to_numpy())


def max_z(model: Pipeline, df: pd.DataFrame) -> np.ndarray:
    """Her satırda, normalden en çok sapan özelliğin |z| değeri."""
    z = model.named_steps["scaler"].transform(df[FEATURES].to_numpy())
    return np.abs(z).max(axis=1)


def combined(model: Pipeline, df: pd.DataFrame, if_thr: float, z_thr: float) -> np.ndarray:
    """Birleşik skor: >1 ise anomali. Backend'de aynı formül var (app/anomaly.py)."""
    return np.maximum(if_score(model, df) / if_thr, max_z(model, df) / z_thr)


def first_alarm(flags: np.ndarray, n: int = DEBOUNCE) -> int | None:
    """Art arda n anomalinin ilk tamamlandığı indeks (yoksa None)."""
    run_len = 0
    for i, f in enumerate(flags):
        run_len = run_len + 1 if f else 0
        if run_len >= n:
            return i
    return None


def main():
    os.makedirs(MODEL_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)
    train, val, sweep, degrade = make_data()

    model = Pipeline([
        # Özellikler farklı birimlerde (g, °C, A): ortak ölçeğe getir.
        # Isolation Forest buna çok duyarlı değil ama açıklama (z-skoru) için lazım.
        ("scaler", StandardScaler()),
        ("iforest", IsolationForest(n_estimators=300, max_samples=256,
                                    contamination="auto", random_state=0)),
    ])
    # numpy dizisiyle eğitiyoruz: backend pandas'a ihtiyaç duymadan aynı sırayla besleyebilsin
    model.fit(train[FEATURES].to_numpy())

    # Eşikler: eğitimde HİÇ görmediği normal veriden belirleniyor
    if_thr = float(np.quantile(if_score(model, val), THRESHOLD_QUANTILE))
    z_thr = float(np.quantile(max_z(model, val), THRESHOLD_QUANTILE))
    threshold = 1.0
    score = lambda m, df: combined(m, df, if_thr, z_thr)      # noqa: E731
    val_scores = score(model, val)

    # Karşılaştırma için: sadece Isolation Forest olsaydı
    sweep_if_only = None

    # ---- 1) sahte alarm oranı
    raw_fp = float(np.mean(val_scores > threshold))
    debounced_fp = sum(
        1 for i in range(len(val_scores) - DEBOUNCE + 1)
        if np.all(val_scores[i:i + DEBOUNCE] > threshold)
    ) / len(val_scores)

    # ---- 2) şiddete göre yakalama oranı
    sweep["score"] = score(model, sweep)
    sweep["anomaly"] = sweep["score"] > threshold
    det = sweep.groupby(["mode", "severity"])["anomaly"].mean().unstack(0)
    sweep["if_only"] = if_score(model, sweep) > if_thr
    sweep_if_only = sweep.groupby(["mode", "severity"])["if_only"].mean().unstack(0)

    # Klasik eşik kuralı ile karşılaştırma: sistemde bugün olan tek alarm
    sweep["temp_rule"] = sweep["temp_c"] >= 70.0
    det_rule = sweep.groupby(["mode", "severity"])["temp_rule"].mean().unstack(0)

    # ---- 3) yavaş bozulmada tespit süresi
    delays = []
    for d in degrade:
        mode = d["mode"].iloc[0]
        d["score"] = score(model, d)
        i_ml = first_alarm((d["score"] > threshold).to_numpy())
        i_rule = first_alarm((d["temp_c"] >= 70.0).to_numpy())
        delays.append({
            "mode": mode,
            "ml_s": None if i_ml is None else round(i_ml * FEEDER_INTERVAL_S, 1),
            "ml_severity": None if i_ml is None else round(d["severity"].iloc[i_ml], 3),
            "rule_s": None if i_rule is None else round(i_rule * FEEDER_INTERVAL_S, 1),
            "rule_severity": None if i_rule is None else round(d["severity"].iloc[i_rule], 3),
        })

    # ---- grafikler
    plot_detection(det, det_rule)
    plot_degrade(degrade, threshold)

    # ---- modeli kaydet
    scaler: StandardScaler = model.named_steps["scaler"]
    bundle = {
        "pipeline": model,
        "features": FEATURES,
        "threshold": threshold,
        "if_threshold": if_thr,
        "z_threshold": z_thr,
        "debounce": DEBOUNCE,
        # Canlıda "hangi özellik normalden ne kadar sapıyor" açıklaması için
        "normal_mean": dict(zip(FEATURES, scaler.mean_.round(5).tolist())),
        "normal_std": dict(zip(FEATURES, scaler.scale_.round(5).tolist())),
        "sklearn_version": sklearn.__version__,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    joblib.dump(bundle, os.path.join(MODEL_DIR, "anomaly_model.joblib"))

    meta = {k: v for k, v in bundle.items() if k != "pipeline"}
    meta.update({"val_false_positive_raw": raw_fp, "val_false_positive_debounced": debounced_fp,
                 "degrade_detection": delays})
    with open(os.path.join(MODEL_DIR, "model_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    write_report(threshold, raw_fp, debounced_fp, det, det_rule, delays, bundle, sweep_if_only)

    # ---- konsol özeti
    print(f"\nEsikler: Isolation Forest {if_thr:.4f}, z-skoru {z_thr:.2f} (birlesik esik 1.0)")
    print(f"Sahte alarm, normal dogrulama verisi: ham %{raw_fp*100:.2f}, "
          f"{DEBOUNCE} ardisik kuralı ile %{debounced_fp*100:.2f}")
    print("\nYakalama orani (siddete gore):")
    print((det.loc[[0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0]] * 100).round(0).to_string())
    print("\nSadece Isolation Forest olsaydi:")
    print((sweep_if_only.loc[[0.05, 0.1, 0.2, 0.5, 1.0]] * 100).round(0).to_string())
    print("\nYavas bozulmada ilk alarm:")
    for d in delays:
        print(f"  {d['mode']:12s} ML: {d['ml_s']} sn (siddet {d['ml_severity']})   "
              f"sicaklik>=70 kurali: {d['rule_s']} sn (siddet {d['rule_severity']})")
    print(f"\nModel kaydedildi: ml/model/anomaly_model.joblib  (scikit-learn {sklearn.__version__})")
    print("Rapor: ml/reports/egitim_raporu.md")


# ---------------------------------------------------------------- çıktılar
TR = {"bearing": "Rulman", "imbalance": "Dengesizlik", "overheating": "Aşırı ısınma"}


def plot_detection(det, det_rule):
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for mode in det.columns:
        line, = ax.plot(det.index, det[mode] * 100, marker="o", ms=3, label=f"ML · {TR[mode]}")
        ax.plot(det_rule.index, det_rule[mode] * 100, ls="--", color=line.get_color(),
                alpha=0.6, label=f"Sıcaklık ≥70 °C · {TR[mode]}")
    ax.set_xlabel("Arıza şiddeti")
    ax.set_ylabel("Yakalanan pencere (%)")
    ax.set_title("Şiddete göre tespit: ML modeli vs sıcaklık eşiği")
    ax.set_ylim(-3, 103)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7.5, ncol=2, loc="lower right")
    fig.tight_layout()
    fig.savefig(os.path.join(REPORT_DIR, "tespit_siddet.png"), dpi=140)
    plt.close(fig)


def plot_degrade(degrade, threshold):
    fig, axes = plt.subplots(len(degrade), 1, figsize=(8, 6.5), sharex=True)
    for ax, d in zip(axes, degrade):
        t = np.arange(len(d)) * FEEDER_INTERVAL_S
        ax.plot(t, np.minimum(d["score"], 5), lw=1, label="Anomali skoru (5'te kırpıldı)")
        ax.axhline(threshold, color="crimson", ls="--", lw=1, label="Eşik")
        ax.set_ylabel(TR[d["mode"].iloc[0]], fontsize=9)
        ax.grid(alpha=0.3)
        ax2 = ax.twinx()
        ax2.plot(t, d["temp_c"], color="gray", lw=0.8, alpha=0.6)
        ax2.axhline(70, color="gray", ls=":", lw=0.8)
        ax2.set_ylabel("°C", fontsize=8, color="gray")
    axes[0].legend(fontsize=8, loc="upper left")
    axes[-1].set_xlabel("Süre (sn) — şiddet her 0,5 sn'de +0,003")
    fig.suptitle("Yavaş bozulma: anomali skoru (mavi) ve sıcaklık (gri)", fontsize=11)
    fig.tight_layout()
    fig.savefig(os.path.join(REPORT_DIR, "yavas_bozulma.png"), dpi=140)
    plt.close(fig)


def write_report(threshold, raw_fp, debounced_fp, det, det_rule, delays, bundle, det_if):
    rows = [0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0]
    lines = [
        "# Anomali modeli eğitim raporu", "",
        f"Eğitim zamanı: {bundle['trained_at']} · scikit-learn {bundle['sklearn_version']}", "",
        "## Yöntem", "",
        "- Model: StandardScaler + Isolation Forest (300 ağaç) + z-skoru bileşeni, **sadece normal veriyle** eğitildi (3000 pencere).",
        f"- Birleşik skor = max(IF skoru / {bundle['if_threshold']:.4f}, en büyük |z| / {bundle['z_threshold']:.2f}). 1'in üstü anomali.",
        f"- Özellikler: {', '.join(FEATURES)}. Değerler STM32 register çözünürlüğüne yuvarlandı.",
        f"- Bileşen eşikleri: eğitimde görülmemiş 2000 normal pencerenin %{THRESHOLD_QUANTILE*100:.2f}'lik dilimi.",
        f"- Alarm kuralı: art arda {DEBOUNCE} pencere eşiğin üstünde.", "",
        "## Sahte alarm", "",
        "| | Oran |", "|---|---|",
        f"| Tek pencere | %{raw_fp*100:.2f} |",
        f"| {DEBOUNCE} ardışık kuralı | %{debounced_fp*100:.2f} |", "",
        "## Şiddete göre yakalama oranı (%)", "",
        "| Şiddet | " + " | ".join(f"ML · {TR[m]}" for m in det.columns)
        + " | " + " | ".join(f"≥70 °C · {TR[m]}" for m in det_rule.columns) + " |",
        "|---" * (1 + 2 * len(det.columns)) + "|",
    ]
    for s in rows:
        vals = [f"{det.loc[s, m]*100:.0f}" for m in det.columns] + \
               [f"{det_rule.loc[s, m]*100:.0f}" for m in det_rule.columns]
        lines.append(f"| {s:.2f} | " + " | ".join(vals) + " |")
    lines += ["", "### Neden z-skoru bileşeni?", "",
              "Sadece Isolation Forest kullanılsaydı yakalama oranı (%):", "",
              "| Şiddet | " + " | ".join(TR[m] for m in det_if.columns) + " |",
              "|---" * (1 + len(det_if.columns)) + "|"]
    for s in rows:
        lines.append(f"| {s:.2f} | " + " | ".join(f"{det_if.loc[s, m]*100:.0f}" for m in det_if.columns) + " |")
    lines += ["", "Aşırı ısınmada Isolation Forest'ın skoru doyuyor: 100 °C'lik motor, normal "
              "verideki gürültü noktalarıyla aynı skoru alıyor. z-skoru bu açığı kapatıyor.",
              "", "![tespit](tespit_siddet.png)", "",
              "## Yavaş bozulmada ilk alarm", "",
              "Şiddet 0'dan başlayıp her 0,5 sn'de 0,003 artıyor (dashboard testindeki gibi).", "",
              "| Arıza | ML alarmı | ML'de şiddet | ≥70 °C alarmı | Kuralda şiddet |",
              "|---|---|---|---|---|"]
    for d in delays:
        fmt = lambda v, u="": "yok" if v is None else f"{v}{u}"   # noqa: E731
        lines.append(f"| {TR[d['mode']]} | {fmt(d['ml_s'], ' sn')} | {fmt(d['ml_severity'])} | "
                     f"{fmt(d['rule_s'], ' sn')} | {fmt(d['rule_severity'])} |")
    lines += ["", "![bozulma](yavas_bozulma.png)", "",
              "## Sınırlar", "",
              "- Veri simülatörden geliyor; model simülatörün 'normal'ini öğrendi. Gerçek motorda "
              "yük değişimi, ortam sıcaklığı gibi normal çeşitlilik daha fazladır ve yeniden eğitim gerekir.",
              "- Soğuk başlangıç (motor ısınırken) normal veride yok; ısınma sırasında anomali görülmesi beklenir.",
              "- Model anomali olduğunu söyler, arızanın türünü söylemez. Tür için hangi özelliğin "
              "saptığına bakılır (backend bunu z-skoru ile gösteriyor)."]
    with open(os.path.join(REPORT_DIR, "egitim_raporu.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
