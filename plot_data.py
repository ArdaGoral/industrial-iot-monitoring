import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("motor_data.csv")
cols = ["vib_rms", "vib_kurtosis", "temp_c", "current_a"]

fig, axes = plt.subplots(2, 2, figsize=(11, 7))
for ax, col in zip(axes.flat, cols):
    for mode, g in df.groupby("mode"):
        ax.plot(g["step"], g[col], label=mode, alpha=0.8)
    ax.set_title(col)
    ax.set_xlabel("pencere (zaman)")
axes[0, 0].legend()
plt.tight_layout()
plt.show()
