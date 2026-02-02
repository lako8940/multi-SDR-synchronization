"""
Load captured IQ from two RTL-SDRs, run the calibration pipeline,
print offset values, and plot verification of synchronization.
"""
import ast
import sys
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import fftconvolve

from beamform_ready import (
    load_c64, remove_dc, normalize_rms,
    estimate_integer_delay, apply_integer_delay,
    estimate_cfo_hz, correct_cfo,
    estimate_const_phase, correct_const_phase,
    prep_two_channel_for_beamforming,
)

# ── Load capture ─────────────────────────────────────────────────
capture_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("captures/run001")

# Find the two channel files
ch0_files = sorted(capture_dir.glob("ch0_*.c64"))
ch1_files = sorted(capture_dir.glob("ch1_*.c64"))
if not ch0_files or not ch1_files:
    sys.exit(f"Could not find ch0/ch1 .c64 files in {capture_dir}")

meta = ast.literal_eval((capture_dir / "meta.txt").read_text())
fs_hz = meta["fs_hz_requested"]
fc_hz = meta["fc_hz"]

print(f"Capture dir : {capture_dir}")
print(f"Center freq : {fc_hz/1e6:.3f} MHz")
print(f"Sample rate : {fs_hz/1e6:.3f} MSPS")
print(f"CH0 file    : {ch0_files[0].name}")
print(f"CH1 file    : {ch1_files[0].name}")

x1_raw = load_c64(ch0_files[0])
x2_raw = load_c64(ch1_files[0])
print(f"CH0 samples : {len(x1_raw):,}")
print(f"CH1 samples : {len(x2_raw):,}")

# ── Pre-correction phase difference (for plotting) ───────────────
n_pre = min(len(x1_raw), len(x2_raw))
x1_pre = normalize_rms(remove_dc(x1_raw[:n_pre]))
x2_pre = normalize_rms(remove_dc(x2_raw[:n_pre]))

# ── Run calibration pipeline ─────────────────────────────────────
x1, x2, cal = prep_two_channel_for_beamforming(x1_raw, x2_raw, fs_hz)

print("\n── Calibration Results ─────────────────────")
print(f"Integer delay : {cal['lag_samples']:+d} samples  "
      f"({cal['lag_samples']/fs_hz*1e6:+.2f} us)")
print(f"CFO           : {cal['cfo_hz']:+.4f} Hz")
print(f"Phase offset  : {cal['phase_rad']:+.4f} rad  "
      f"({np.degrees(cal['phase_rad']):+.2f} deg)")

# ── Verification plots ───────────────────────────────────────────
fig, axes = plt.subplots(3, 1, figsize=(12, 9))

# Panel 1: Cross-correlation peak
max_lag = 20000
r = fftconvolve(
    normalize_rms(remove_dc(x2_raw[:n_pre])),
    np.conj(normalize_rms(remove_dc(x1_raw[:n_pre]))[::-1]),
    mode="full",
)
mid = len(x1_raw[:n_pre]) - 1
r_win = r[mid - max_lag : mid + max_lag + 1]
lags = np.arange(-max_lag, max_lag + 1)

ax = axes[0]
ax.plot(lags, np.abs(r_win))
ax.axvline(cal["lag_samples"], color="r", ls="--", label=f"peak = {cal['lag_samples']:+d}")
ax.set_xlabel("Lag (samples)")
ax.set_ylabel("|Cross-correlation|")
ax.set_title("Cross-Correlation Peak (delay estimation)")
ax.legend()
ax.set_xlim(-max_lag, max_lag)

# Panel 2: Instantaneous phase difference over time (before / after)
chunk = min(len(x1), len(x2), 500_000)  # use up to 500k samples
t_ms = np.arange(chunk) / fs_hz * 1e3

phase_before = np.angle(x1_pre[:chunk] * np.conj(x2_pre[:chunk]))
phase_after = np.angle(x1[:chunk] * np.conj(x2[:chunk]))

ax = axes[1]
ax.plot(t_ms, np.degrees(phase_before), alpha=0.5, lw=0.4, label="Before correction")
ax.plot(t_ms, np.degrees(phase_after), alpha=0.7, lw=0.4, label="After correction")
ax.set_xlabel("Time (ms)")
ax.set_ylabel("Phase difference (deg)")
ax.set_title("Instantaneous Phase Difference: CH0 vs CH1")
ax.legend()

# Panel 3: Time-domain overlay (short segment, real part)
seg = 200  # samples to show
t_us = np.arange(seg) / fs_hz * 1e6

ax = axes[2]
ax.plot(t_us, x1[:seg].real, label="CH0 (ref)")
ax.plot(t_us, x2[:seg].real, label="CH1 (corrected)", alpha=0.8)
ax.set_xlabel("Time (us)")
ax.set_ylabel("Amplitude (normalized)")
ax.set_title("Time-Domain Overlay After Correction")
ax.legend()

fig.suptitle(f"Sync Verification  —  {fc_hz/1e6:.1f} MHz, {fs_hz/1e6:.1f} MSPS", fontsize=13)
fig.tight_layout()
plt.show()
