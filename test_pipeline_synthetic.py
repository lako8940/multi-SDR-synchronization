"""
Synthetic proof-of-concept for the sync calibration pipeline.

Creates x1 and x2 from the SAME chirp signal with KNOWN offsets applied,
runs the full pipeline, and verifies the estimates match truth.

If this script passes: the functions are correct and any issues are in the
hardware captures (SNR, signal configuration, etc.).
If this script fails: a specific function is broken.
"""
import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import fftconvolve

from beamform_ready import (
    remove_dc, normalize_rms,
    estimate_integer_delay, apply_integer_delay,
    estimate_cfo_hz, correct_cfo,
    estimate_const_phase, correct_const_phase,
)

# ── Synthetic signal parameters ───────────────────────────────────────────
fs_hz      = 2_400_000   # match hardware
N          = int(2 * fs_hz)  # 2 seconds
TRUE_LAG   = -500        # samples: x2 leads x1 by 500 (same sign convention as real data)
TRUE_CFO   = 0.0         # Hz  (shared SI5351 clock → ~0 Hz real CFO; any nonzero value
                         #      must satisfy CFO × T_total << 1 rad or cross-corr peak → 0)
TRUE_PHASE = 0.8         # rad
SNR_DB     = 30          # dB (generous — isolates algorithm from noise)

# ── Generate LFM chirp (identical to HackRF setup) ───────────────────────
# 25 Hz sawtooth → sweeping ±125 kHz around center
t = np.arange(N) / fs_hz
sawtooth = (t * 25) % 1.0               # 0→1 per chirp period
inst_freq = (sawtooth - 0.5) * 250_000  # -125 kHz to +125 kHz
chirp_phase = np.cumsum(inst_freq) / fs_hz * 2 * np.pi
chirp = np.exp(1j * chirp_phase).astype(np.complex64)

# ── Build x1 and x2 ───────────────────────────────────────────────────────
rng = np.random.default_rng(42)
noise_sigma = np.sqrt(0.5 * 10 ** (-SNR_DB / 10))

def add_noise(x):
    n = (rng.standard_normal(len(x)) + 1j * rng.standard_normal(len(x))) * noise_sigma
    return (x + n.astype(np.complex64)).astype(np.complex64)

x1 = add_noise(chirp)

# Apply: integer delay, constant phase, CFO  (in that order)
x2_clean = np.roll(chirp, TRUE_LAG)    # TRUE_LAG=-500 → roll left 500 → x2 leads x1
if TRUE_LAG < 0:
    x2_clean[len(chirp)+TRUE_LAG:] = 0  # zero wrapped tail
else:
    x2_clean[:TRUE_LAG] = 0
x2_clean = x2_clean * np.exp(1j * TRUE_PHASE)
n_idx = np.arange(N)
x2_clean = x2_clean * np.exp(1j * 2 * np.pi * TRUE_CFO * n_idx / fs_hz)
x2 = add_noise(x2_clean.astype(np.complex64))

# ── Pipeline ───────────────────────────────────────────────────────────────
x1n = normalize_rms(remove_dc(x1))
x2n = normalize_rms(remove_dc(x2))
ns = min(len(x1n), len(x2n))
x1n, x2n = x1n[:ns], x2n[:ns]

# Step 1: Integer delay
lag = estimate_integer_delay(x1n, x2n)
x2a = apply_integer_delay(x2n, lag)
ns = min(len(x1n), len(x2a))
x1n, x2a = x1n[:ns], x2a[:ns]

# Step 2: CFO
cfo = estimate_cfo_hz(x1n, x2a, fs_hz)
x2b = correct_cfo(x2a, cfo, fs_hz)

# Step 3: Constant phase
theta = estimate_const_phase(x1n, x2b)
x2c = correct_const_phase(x2b, theta)

# ── Print results ──────────────────────────────────────────────────────────
print("─── Truth ───────────────────────────────────")
print(f"  lag   : {TRUE_LAG:+d} samples")
print(f"  CFO   : {TRUE_CFO:+.2f} Hz")
print(f"  phase : {TRUE_PHASE:+.4f} rad  ({np.degrees(TRUE_PHASE):+.2f}°)")
print()
print("─── Estimated ───────────────────────────────")
print(f"  lag   : {lag:+d} samples   (error {lag - TRUE_LAG:+d})")
print(f"  CFO   : {cfo:+.2f} Hz   (error {cfo - TRUE_CFO:+.2f} Hz)")
print(f"  phase : {theta:+.4f} rad  ({np.degrees(theta):+.2f}°)   (error {np.degrees(theta - TRUE_PHASE):+.2f}°)")
print()

lag_ok   = abs(lag - TRUE_LAG) == 0
cfo_ok   = abs(cfo - TRUE_CFO) < 1.0
phase_ok = abs(np.degrees(theta - TRUE_PHASE)) < 2.0
print(f"  lag   : {'PASS ✓' if lag_ok   else 'FAIL ✗'}")
print(f"  CFO   : {'PASS ✓' if cfo_ok   else 'FAIL ✗'}")
print(f"  phase : {'PASS ✓' if phase_ok else 'FAIL ✗'}")

# ── Plots ──────────────────────────────────────────────────────────────────
valid_start = abs(lag) + 200

fig, axes = plt.subplots(3, 1, figsize=(12, 10))

# Panel 1: Cross-correlation (zoomed to ±2000 lags)
zoom = 2000
r = fftconvolve(x2n, np.conj(x1n[::-1]), mode="full")
mid = len(x1n) - 1
r_win = r[mid - zoom : mid + zoom + 1]
lags_plt = np.arange(-zoom, zoom + 1)

ax = axes[0]
ax.plot(lags_plt, np.abs(r_win))
ax.axvline(lag,      color="r", ls="--", lw=1.5, label=f"estimated lag = {lag:+d}")
ax.axvline(TRUE_LAG, color="g", ls=":",  lw=1.5, label=f"true lag = {TRUE_LAG:+d}")
ax.set_title("Cross-Correlation — peak must land on true lag")
ax.set_xlabel("Lag (samples)")
ax.set_ylabel("|Xcorr|")
ax.legend()

# Panel 2: Phase difference (before = after lag only; after = all corrections)
chunk = min(len(x1n) - valid_start, len(x2c) - valid_start, 500_000)
t_ms = np.arange(chunk) / fs_hz * 1e3

phase_before = np.angle(x1n[valid_start:valid_start+chunk] * np.conj(x2a[valid_start:valid_start+chunk]))
phase_after  = np.angle(x1n[valid_start:valid_start+chunk] * np.conj(x2c[valid_start:valid_start+chunk]))

ax = axes[1]
ax.plot(t_ms, np.degrees(phase_before), alpha=0.5, lw=0.4, label="After lag correction only")
ax.plot(t_ms, np.degrees(phase_after),  alpha=0.9, lw=0.4, label="After ALL corrections")
ax.axhline(0, color="k", ls="--", lw=1, label="Target: 0°")
ax.set_title("Instantaneous Phase Difference — must be flat 0° after all corrections")
ax.set_xlabel("Time (ms)")
ax.set_ylabel("Phase difference (°)")
ax.legend()

# Panel 3: Time-domain overlay (500 samples)
seg = 500
t_us = np.arange(seg) / fs_hz * 1e6

ax = axes[2]
ax.plot(t_us, x1n[valid_start:valid_start+seg].real, lw=0.8, label="CH0 (ref)")
ax.plot(t_us, x2c[valid_start:valid_start+seg].real, lw=0.8, ls="--", alpha=0.8, label="CH1 (corrected)")
ax.set_title("Time-Domain Overlay — waveforms must overlap")
ax.set_xlabel("Time (μs)")
ax.set_ylabel("Amplitude (normalized)")
ax.legend()

fig.suptitle(
    f"SYNTHETIC TEST  —  SNR={SNR_DB} dB | lag={TRUE_LAG} | CFO={TRUE_CFO} Hz | phase={np.degrees(TRUE_PHASE):.1f}°",
    fontsize=12
)
fig.tight_layout()
plt.savefig("captures/synthetic_test.png", dpi=120)
plt.show()
print("\nPlot saved to captures/synthetic_test.png")
