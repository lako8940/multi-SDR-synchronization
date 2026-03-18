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
    find_USB_delay,
    apply_integer_delay, correct_const_phase,
    calibrate_and_save, load_calibration, apply_calibration,
)

# ── Load capture ─────────────────────────────────────────────────
capture_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("captures/run001")

ch0_files = sorted(capture_dir.glob("ch0_*.c64"))
ch1_files = sorted(capture_dir.glob("ch1_*.c64"))
if not ch0_files or not ch1_files:
    sys.exit(f"Could not find ch0/ch1 .c64 files in {capture_dir}")

meta = ast.literal_eval((capture_dir / "meta.txt").read_text())
fs_hz = meta["fs_hz_requested"]
fc_hz = meta["fc_hz"]

x1_raw = load_c64(ch0_files[0])
x2_raw = load_c64(ch1_files[0])

rms0_db = 10 * np.log10(np.mean(np.abs(x1_raw)**2))
rms1_db = 10 * np.log10(np.mean(np.abs(x2_raw)**2))

# ── Pre-correction baseline ───────────────────────────────────────
n_pre = min(len(x1_raw), len(x2_raw))
x1_pre = normalize_rms(remove_dc(x1_raw[:n_pre]))
x2_pre = normalize_rms(remove_dc(x2_raw[:n_pre]))

# ── USB jitter delay ──────────────────────────────────────────────
usb_lag = find_USB_delay(x1_raw, x2_raw)
print(f"\n── USB Delay Estimation ────────────────────")
print(f"USB jitter offset : {usb_lag:+d} samples  ({usb_lag / fs_hz * 1e6:+.2f} us)")

# ── Run calibration and save to JSON ─────────────────────────────
cal_path = capture_dir / "cal.json"
cal = calibrate_and_save(x1_raw, x2_raw, fs_hz, fc_hz, cal_path, capture_dir)
print("\n── Calibration Results ─────────────────────")
print(f"Integer delay : {cal['lag_samples']:+d} samples  ({cal['lag_samples']/fs_hz*1e6:+.2f} us)")
print(f"Phase offset  : {cal['phase_rad']:+.4f} rad  ({np.degrees(cal['phase_rad']):+.2f} deg)")

# ── Step-by-step coherence diagnostics ───────────────────────────
# Coherence = |mean(x1 * conj(x2))| after normalization.
# 1.0 = perfect alignment, ~0 = signals are unrelated (noise-dominated).
def coherence(a, b):
    z = a * np.conj(b)
    c = np.abs(np.mean(z))
    mean_phase = np.degrees(np.angle(np.mean(z)))
    return c, mean_phase

valid_start = abs(cal["lag_samples"]) + 200

a = normalize_rms(remove_dc(x1_raw))
b = normalize_rms(remove_dc(x2_raw))
ns = min(len(a), len(b))
a, b = a[:ns], b[:ns]

b1 = apply_integer_delay(b, cal["lag_samples"])
ns = min(len(a), len(b1))
a, b1 = a[:ns], b1[:ns]

b2 = correct_const_phase(b1, cal["phase_rad"])

v = slice(valid_start, valid_start + min(500_000, ns - valid_start))

c0, p0 = coherence(a[v], b[v])
c1, p1 = coherence(a[v], b1[v])
c2, p2 = coherence(a[v], b2[v])

print("\n── Step-by-step coherence (valid region) ────")
print(f"  Raw (no correction)   : {c0:.4f}  mean phase = {p0:+.1f}°")
print(f"  After integer delay   : {c1:.4f}  mean phase = {p1:+.1f}°")
print(f"  After phase correction: {c2:.4f}  mean phase = {p2:+.1f}°  ← should be ~1.0 / ~0°")
print()
if c2 > 0.9:
    print("  RESULT: PASS — corrections are working correctly")
elif c2 > 0.5:
    print("  RESULT: PARTIAL — some alignment but phase is not fully corrected")
else:
    print("  RESULT: FAIL — coherence too low; check SNR or re-capture with HackRF transmitting")

# ── Load calibration back and apply for plotting ──────────────────
cal = load_calibration(cal_path)
x1, x2 = apply_calibration(x1_raw, x2_raw, cal)

# ── Verification plots ────────────────────────────────────────────
fig, axes = plt.subplots(4, 1, figsize=(12, 13))

# Panel 1: Raw spectra
N_fft = min(65536, len(x1_raw), len(x2_raw))
freqs_khz = np.fft.fftshift(np.fft.fftfreq(N_fft, d=1/fs_hz)) / 1e3
spec0 = 20 * np.log10(np.abs(np.fft.fftshift(np.fft.fft(x1_raw[:N_fft]))) / N_fft + 1e-12)
spec1 = 20 * np.log10(np.abs(np.fft.fftshift(np.fft.fft(x2_raw[:N_fft]))) / N_fft + 1e-12)

ax = axes[0]
ax.plot(freqs_khz, spec0, lw=0.6, label=f"CH0  (RMS {rms0_db:.1f} dBFS)")
ax.plot(freqs_khz, spec1, lw=0.6, alpha=0.8, label=f"CH1  (RMS {rms1_db:.1f} dBFS)")
ax.set_xlabel("Frequency offset from center (kHz)")
ax.set_ylabel("Power (dB)")
ax.set_title("Raw Capture Spectra — signal must be visible in both channels")
ax.legend()

# Panel 2: Cross-correlation peak
max_lag = 20000
r = fftconvolve(
    normalize_rms(remove_dc(x2_raw[:n_pre])),
    np.conj(normalize_rms(remove_dc(x1_raw[:n_pre]))[::-1]),
    mode="full",
)
mid = len(x1_raw[:n_pre]) - 1
r_win = r[mid - max_lag : mid + max_lag + 1]
lags = np.arange(-max_lag, max_lag + 1)

ax = axes[1]
ax.plot(lags, np.abs(r_win))
ax.axvline(cal["lag_samples"], color="r", ls="--", label=f"peak = {cal['lag_samples']:+d}")
ax.set_xlabel("Lag (samples)")
ax.set_ylabel("|Cross-correlation|")
ax.set_title("Cross-Correlation Peak (delay estimation)")
ax.legend()
ax.set_xlim(-max_lag, max_lag)

# Panel 3: Instantaneous phase difference — raw, corrected, and smoothed
chunk = min(len(x1) - valid_start, len(x2) - valid_start, 500_000)
t_ms = np.arange(chunk) / fs_hz * 1e3

phase_before = np.angle(x1_pre[valid_start:valid_start+chunk] * np.conj(x2_pre[valid_start:valid_start+chunk]))
phase_after  = np.angle(x1[valid_start:valid_start+chunk]     * np.conj(x2[valid_start:valid_start+chunk]))

# Smooth the phase via complex averaging (avoids wrap artifacts)
smooth_n = min(5000, chunk // 10)
kernel = np.ones(smooth_n) / smooth_n
phase_after_smooth = np.angle(np.convolve(np.exp(1j * phase_after), kernel, mode="same"))

ax = axes[2]
ax.plot(t_ms, np.degrees(phase_before), alpha=0.3, lw=0.3, color="tab:blue", label="Before correction")
ax.plot(t_ms, np.degrees(phase_after),  alpha=0.3, lw=0.3, color="tab:orange", label="After correction (raw)")
ax.plot(t_ms, np.degrees(phase_after_smooth), lw=1.2, color="tab:red", label=f"After correction (smoothed, N={smooth_n})")
ax.axhline(0, color="k", ls="--", lw=0.8, label="Target: 0°")
ax.set_xlabel("Time (ms)")
ax.set_ylabel("Phase difference (deg)")
ax.set_title(f"Instantaneous Phase Difference  —  coherence after correction: {c2:.3f}  (mean: {p2:+.1f}°)")
ax.legend(fontsize=8)

# Panel 4: Cross-correlation AFTER integer delay correction (proof peak moved to 0)
# Uses a 200k-sample segment starting at valid_start to keep it fast.
seg_cc = min(200_000, len(x1) - valid_start)
r_after = fftconvolve(
    x2[valid_start:valid_start+seg_cc],
    np.conj(x1[valid_start:valid_start+seg_cc][::-1]),
    mode="full",
)
zoom = 2000
mid_after = seg_cc - 1
r_after_win = r_after[mid_after - zoom : mid_after + zoom + 1]
lags_after = np.arange(-zoom, zoom + 1)
peak_after = int(np.argmax(np.abs(r_after_win)) - zoom)

ax = axes[3]
ax.plot(lags_after, np.abs(r_after_win))
ax.axvline(peak_after, color="r", ls="--", label=f"peak = {peak_after:+d}  (should be 0)")
ax.axvline(0, color="g", ls=":", lw=1.5, label="target lag = 0")
ax.set_xlabel("Lag (samples)")
ax.set_ylabel("|Cross-correlation|")
ax.set_title(f"Cross-Correlation AFTER Lag+Phase Correction — peak at {peak_after:+d} (want 0)")
ax.set_xlim(-zoom, zoom)
ax.legend()

fig.suptitle(f"Sync Verification  —  {fc_hz/1e6:.1f} MHz, {fs_hz/1e6:.1f} MSPS", fontsize=13)
fig.tight_layout()
plt.show()
