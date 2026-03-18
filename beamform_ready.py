import json
import time
import threading
from datetime import datetime, timezone

import numpy as np
from scipy.signal import fftconvolve


# ── Signal conditioning ───────────────────────────────────────────────────────

def load_c64(path):
    return np.fromfile(path, dtype=np.complex64)

def remove_dc(x):
    return x - np.mean(x)

def normalize_rms(x, eps=1e-12):
    return x / np.sqrt(np.mean(np.abs(x)**2) + eps)


# ── Offset estimation ─────────────────────────────────────────────────────────

def estimate_integer_delay(x_ref, x, max_lag=20000):
    """Cross-correlation peak search on pre-processed IQ.

    Returns lag in samples. Positive lag means x is delayed vs x_ref.
    Inputs must already be DC-removed and RMS-normalised.
    For raw IQ use find_USB_delay() instead.
    """
    r = fftconvolve(x, np.conj(x_ref[::-1]), mode="full")
    mid = len(x_ref) - 1
    r = r[mid - max_lag : mid + max_lag + 1]
    return int(np.argmax(np.abs(r)) - max_lag)

def find_USB_delay(x1_raw, x2_raw, max_lag=20000):
    """Estimate the USB scheduling jitter between two simultaneous captures.

    Preprocesses both raw IQ arrays (DC removal + RMS normalisation) then
    returns the integer sample offset caused by non-deterministic USB bulk
    transfer start times.  Positive lag means ch1 started later than ch0.
    """
    x1 = normalize_rms(remove_dc(x1_raw))
    x2 = normalize_rms(remove_dc(x2_raw))
    n = min(len(x1), len(x2))
    return estimate_integer_delay(x1[:n], x2[:n], max_lag=max_lag)

def estimate_const_phase(x_ref, x):
    """Mean phase of x relative to x_ref over the supplied window."""
    return np.angle(np.mean(x * np.conj(x_ref)))


# ── Correction application ────────────────────────────────────────────────────

def apply_integer_delay(x, lag):
    """Shift x to align it to the reference channel.

    Positive lag: trim lag leading samples (x was delayed).
    Negative lag: prepend zeros (x was early); length preserved.
    """
    if lag > 0:
        return x[lag:]
    if lag < 0:
        return np.pad(x, (abs(lag), 0))[:len(x)]
    return x

def correct_const_phase(x, theta):
    return (x * np.exp(-1j * theta)).astype(np.complex64)


# ── Calibration pipeline ──────────────────────────────────────────────────────

def calibrate_and_save(x1_raw, x2_raw, fs_hz, fc_hz, cal_path):
    """Estimate USB delay and phase offset; save to JSON.

    CFO is not estimated — the shared SI5351 clock guarantees ~0 Hz drift and
    polyfit-on-unwrapped-phase fails at the low per-sample SNR of a received
    chirp (CLAUDE.md: 'CFO estimation destroys alignment on low-SNR chirp').

    Phase is estimated from a short 4 ms window just past the zero-pad boundary.
    A longer window is suppressed by the chirp beat (fractional-lag sinc effect).
    """
    lag = find_USB_delay(x1_raw, x2_raw)

    x1 = normalize_rms(remove_dc(x1_raw))
    x2 = normalize_rms(remove_dc(x2_raw))
    n = min(len(x1), len(x2))
    x1, x2 = x1[:n], x2[:n]

    x2a = apply_integer_delay(x2, lag)
    n = min(len(x1), len(x2a))
    x1, x2a = x1[:n], x2a[:n]

    est_start = abs(lag) + 200
    est_n = min(int(0.004 * fs_hz), n - est_start)
    theta = estimate_const_phase(x1[est_start:est_start + est_n],
                                 x2a[est_start:est_start + est_n])

    cal = {
        "lag_samples": int(lag),
        "phase_rad": float(theta),
        "fc_hz": float(fc_hz),
        "fs_hz": float(fs_hz),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(cal_path, "w") as f:
        json.dump(cal, f, indent=2)

    return cal

def load_calibration(cal_path):
    with open(cal_path) as f:
        return json.load(f)

def apply_calibration(x1_raw, x2_raw, cal):
    """Apply pre-computed calibration offsets to a raw capture pair.

    No estimation is performed — use the saved lag and phase directly.
    """
    x1 = normalize_rms(remove_dc(x1_raw))
    x2 = normalize_rms(remove_dc(x2_raw))
    n = min(len(x1), len(x2))
    x1, x2 = x1[:n], x2[:n]

    x2 = apply_integer_delay(x2, cal["lag_samples"])
    n = min(len(x1), len(x2))
    x1, x2 = x1[:n], x2[:n]

    x2 = correct_const_phase(x2, cal["phase_rad"])
    return x1, x2


# ── Hardware entry point ──────────────────────────────────────────────────────

def perform_two_element_cal(cal_path,
                             fc_hz=868_100_000,
                             fs_hz=2_400_000,
                             gain_db=30,
                             duration_s=5.0,
                             ppm=0):
    """Calibrate a two-element RTL-SDR array in one step.

    1. Prompt the operator to confirm the HackRF calibration chirp is running.
    2. Open both RTL-SDRs and capture simultaneously into memory — a threading
       Barrier fires both async readers at the same instant.
    3. Print the USB scheduling jitter offset from find_USB_delay().
    4. Estimate phase offset and write cal_path via calibrate_and_save().

    Parameters
    ----------
    cal_path   : str or Path — where to write cal.json
    fc_hz      : int         — center frequency in Hz (default 868.1 MHz)
    fs_hz      : int         — sample rate in Hz (default 2.4 MSPS)
    gain_db    : int/float   — RF gain in dB (default 30)
    duration_s : float       — capture length in seconds (default 5.0)
    ppm        : int         — frequency correction; 0 skips set_freq_correction
                               (the RTL-SDR driver crashes on set_freq_correction(0))

    Returns
    -------
    cal : dict — same dict written to cal_path
    """
    from rtlsdr import RtlSdr

    input("\nEnsure the HackRF is transmitting the calibration chirp.\n"
          "Press Enter when ready to capture... ")

    # Sorted serial numbers give consistent CH0/CH1 assignment across USB enumeration order
    serial_numbers = sorted(RtlSdr.get_device_serial_addresses())
    device_indices = [RtlSdr.get_device_index_by_serial(s) for s in serial_numbers]
    if len(device_indices) < 2:
        raise RuntimeError(f"Need at least 2 RTL-SDR devices; found {len(device_indices)}")

    sdrs = []
    for idx in device_indices[:2]:
        s = RtlSdr(device_index=idx)
        s.sample_rate = fs_hz
        s.center_freq = fc_hz
        s.gain = gain_db
        if ppm:
            s.freq_correction = ppm
        sdrs.append(s)

    # Barrier(3): CH0 worker + CH1 worker + main all rendezvous here before
    # either async reader starts — fires both SDRs as close to simultaneously as possible
    barrier = threading.Barrier(3)
    buffers = [[], []]

    def worker(ch_idx, sdr):
        def cb(samples, _ctx):
            buffers[ch_idx].append(np.asarray(samples, dtype=np.complex64))
        barrier.wait()
        try:
            sdr.read_samples_async(cb, num_samples=256 * 1024)
        finally:
            try: sdr.cancel_read_async()
            except: pass
            try: sdr.close()
            except: pass

    threads = []
    for k, sdr in enumerate(sdrs):
        t = threading.Thread(target=worker, args=(k, sdr), daemon=True)
        t.start()
        threads.append(t)

    barrier.wait()
    print(f"Capturing {duration_s:.1f} s calibration data...")
    time.sleep(duration_s)

    for s in sdrs:
        try: s.cancel_read_async()
        except: pass
    for t in threads:
        t.join(timeout=5)

    x1_raw = np.concatenate(buffers[0])
    x2_raw = np.concatenate(buffers[1])
    print(f"  CH0: {len(x1_raw):,} samples | CH1: {len(x2_raw):,} samples")

    usb_lag = find_USB_delay(x1_raw, x2_raw)
    print(f"\n── USB Delay Estimation ─────────────────────")
    print(f"  USB jitter offset : {usb_lag:+d} samples  ({usb_lag / fs_hz * 1e6:+.2f} us)")

    cal = calibrate_and_save(x1_raw, x2_raw, fs_hz, fc_hz, cal_path)
    print(f"  Phase offset      : {cal['phase_rad']:+.4f} rad  ({np.degrees(cal['phase_rad']):+.2f} deg)")
    print(f"  Calibration saved → {cal_path}")

    return cal


# ── Direction finding ─────────────────────────────────────────────────────────

def _aoa_from_phase_diff(delta_phi_rad):
    """Convert inter-element phase difference to angle of arrival.

    Assumes half-wavelength element spacing (d = λ/2).

    For d = λ/2:   Δφ = 2π·d·sin(θ)/λ = π·sin(θ)
    Invert:         θ  = arcsin(Δφ / π)

    θ = 0° is broadside (signal arriving perpendicular to the array baseline).
    θ = ±90° is end-fire.  Valid range is [-90°, +90°] (front hemisphere).
    """
    return np.degrees(np.arcsin(np.clip(delta_phi_rad / np.pi, -1.0, 1.0)))


def perform_two_element_DF(cal_path,
                            fs_hz=2_400_000,
                            gain_db=30,
                            ppm=0,
                            chunk_s=0.1,
                            history_s=10.0):
    """Stream IQ from both RTL-SDRs and display a live angle-of-arrival estimate.

    Loads the saved calibration, opens both RTL-SDRs at the calibrated frequency,
    and continuously estimates AoA by measuring the inter-element phase difference
    after applying the calibration offsets.  The display updates every chunk_s
    seconds until the plot window is closed.

    AoA convention
    --------------
    0° = broadside (signal arrives perpendicular to the element baseline).
    Positive angles lean toward CH0, negative toward CH1.
    Directional antennas constrain valid arrivals to ±90° (front hemisphere).

    Parameters
    ----------
    cal_path  : str or Path — path to cal.json written by perform_two_element_cal()
    fs_hz     : int         — sample rate (must match calibration)
    gain_db   : float       — RF gain in dB
    ppm       : int         — frequency correction; 0 skips set_freq_correction
    chunk_s   : float       — seconds of IQ per AoA estimate (default 0.1 s)
    history_s : float       — rolling window shown in history plot (default 10 s)
    """
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    from rtlsdr import RtlSdr

    cal = load_calibration(cal_path)
    fc_hz = int(cal["fc_hz"])

    # ── Open and configure both SDRs ──────────────────────────────────────────
    serial_numbers = sorted(RtlSdr.get_device_serial_addresses())
    device_indices = [RtlSdr.get_device_index_by_serial(s) for s in serial_numbers]
    if len(device_indices) < 2:
        raise RuntimeError(f"Need at least 2 RTL-SDR devices; found {len(device_indices)}")

    sdrs = []
    for idx in device_indices[:2]:
        s = RtlSdr(device_index=idx)
        s.sample_rate = fs_hz
        s.center_freq = fc_hz
        s.gain = gain_db
        if ppm:
            s.freq_correction = ppm
        sdrs.append(s)

    # ── Streaming workers ─────────────────────────────────────────────────────
    # Workers run continuously, appending 256k-sample chunks to bufs[].
    # Main thread drains bufs[] on each plot update under lock.
    bufs = [[], []]
    lock = threading.Lock()
    barrier = threading.Barrier(3)
    stop_event = threading.Event()

    def worker(ch_idx, sdr):
        def cb(samples, _ctx):
            if stop_event.is_set():
                sdr.cancel_read_async()
                return
            with lock:
                bufs[ch_idx].append(np.asarray(samples, dtype=np.complex64))
        barrier.wait()
        try:
            sdr.read_samples_async(cb, num_samples=256 * 1024)
        finally:
            try: sdr.cancel_read_async()
            except: pass
            try: sdr.close()
            except: pass

    threads = []
    for k, sdr in enumerate(sdrs):
        t = threading.Thread(target=worker, args=(k, sdr), daemon=True)
        t.start()
        threads.append(t)

    barrier.wait()   # fire both SDRs simultaneously
    print(f"DF streaming at {fc_hz/1e6:.3f} MHz — close the plot window to stop.")

    # ── Plot setup ────────────────────────────────────────────────────────────
    chunk_n = int(chunk_s * fs_hz)
    max_history = int(history_s / chunk_s)

    fig = plt.figure(figsize=(11, 5))
    fig.suptitle(
        f"Two-Element Direction Finding  —  {fc_hz/1e6:.3f} MHz, d=λ/2",
        fontsize=12
    )
    gs = gridspec.GridSpec(1, 2, width_ratios=[1, 2], figure=fig)

    # Left: half-polar plot (±90° front hemisphere)
    ax_pol = fig.add_subplot(gs[0], projection="polar")
    ax_pol.set_theta_zero_location("N")   # 0° (broadside) at top
    ax_pol.set_theta_direction(-1)        # clockwise = positive angle
    ax_pol.set_thetamin(-90)
    ax_pol.set_thetamax(90)
    ax_pol.set_ylim(0, 1)
    ax_pol.set_yticks([])
    ax_pol.set_xticks(np.radians([-90, -60, -30, 0, 30, 60, 90]))
    ax_pol.set_xticklabels(["-90°", "-60°", "-30°", "0°", "30°", "60°", "90°"])
    ax_pol.set_title("Current AoA", pad=18)

    needle, = ax_pol.plot([], [], color="crimson", lw=3)
    aoa_label = ax_pol.text(
        0.5, -0.08, "—", transform=ax_pol.transAxes,
        ha="center", va="top", fontsize=16, fontweight="bold", color="crimson"
    )

    # Right: rolling history
    ax_hist = fig.add_subplot(gs[1])
    ax_hist.set_ylim(-95, 95)
    ax_hist.set_xlabel("Time (s)")
    ax_hist.set_ylabel("Angle of Arrival (deg)")
    ax_hist.set_title("AoA History")
    ax_hist.axhline(0, color="k", ls="--", lw=0.8, alpha=0.4, label="Broadside (0°)")
    ax_hist.set_yticks(range(-90, 91, 15))
    ax_hist.grid(True, alpha=0.3)
    ax_hist.legend(loc="upper right", fontsize=8)
    hist_line, = ax_hist.plot([], [], "b.-", ms=3, lw=0.8)

    plt.tight_layout()
    plt.ion()
    plt.show()

    # ── Main update loop ──────────────────────────────────────────────────────
    aoa_history = []
    t_history = []
    t_start = time.time()

    while plt.fignum_exists(fig.number):
        time.sleep(chunk_s)

        with lock:
            if not bufs[0] or not bufs[1]:
                continue
            raw0 = np.concatenate(bufs[0])
            raw1 = np.concatenate(bufs[1])
            bufs[0].clear()
            bufs[1].clear()

        if len(raw0) < chunk_n or len(raw1) < chunk_n:
            continue

        # Use most-recent chunk_n samples from each buffer
        x1_cal, x2_cal = apply_calibration(raw0[-chunk_n:], raw1[-chunk_n:], cal)

        # Δφ = π·sin(θ)  →  θ = arcsin(Δφ/π)
        delta_phi = np.angle(np.mean(x1_cal * np.conj(x2_cal)))
        aoa_deg = _aoa_from_phase_diff(delta_phi)

        t_now = time.time() - t_start
        aoa_history.append(aoa_deg)
        t_history.append(t_now)

        # Trim to rolling window
        while len(aoa_history) > max_history:
            aoa_history.pop(0)
            t_history.pop(0)

        # Update polar needle
        theta_rad = np.radians(aoa_deg)
        needle.set_data([theta_rad, theta_rad], [0, 0.9])
        aoa_label.set_text(f"{aoa_deg:+.1f}°")

        # Update rolling history (x-axis always shows last history_s seconds)
        t_rel = [t - t_history[0] for t in t_history]
        hist_line.set_data(t_rel, aoa_history)
        ax_hist.set_xlim(0, max(history_s, t_rel[-1] + chunk_s))

        fig.canvas.draw_idle()
        fig.canvas.flush_events()

    # ── Cleanup ───────────────────────────────────────────────────────────────
    stop_event.set()
    for s in sdrs:
        try: s.cancel_read_async()
        except: pass
    for t in threads:
        t.join(timeout=5)

    print("DF stopped.")
