import json
from datetime import datetime, timezone

import numpy as np
from scipy.signal import fftconvolve

def load_c64(path):
    return np.fromfile(path, dtype=np.complex64)

def remove_dc(x):
    return x - np.mean(x)

def normalize_rms(x, eps=1e-12):
    return x / np.sqrt(np.mean(np.abs(x)**2) + eps)

def estimate_integer_delay(x_ref, x, max_lag=20000):
    # Returns lag in samples. Positive lag means x is delayed vs ref.
    # FFT-based cross-correlation: correlate(x, x_ref) = ifft(fft(x) * conj(fft(x_ref)))
    r = fftconvolve(x, np.conj(x_ref[::-1]), mode="full")
    mid = len(x_ref) - 1
    r = r[mid - max_lag : mid + max_lag + 1]
    lag = int(np.argmax(np.abs(r)) - max_lag)
    return lag

def apply_integer_delay(x, lag):
    # Shift x to align to reference
    if lag > 0:
        return x[lag:]
    elif lag < 0:
        return np.pad(x, (abs(lag), 0))[:len(x)]
    return x

def estimate_cfo_hz(x_ref, x, fs_hz):
    z = x * np.conj(x_ref)
    phi = np.unwrap(np.angle(z))
    n = np.arange(len(phi))
    a, b = np.polyfit(n, phi, 1)
    return a * fs_hz / (2*np.pi)

def correct_cfo(x, cfo_hz, fs_hz):
    n = np.arange(len(x))
    rot = np.exp(-1j * 2*np.pi * cfo_hz * n / fs_hz)
    return (x * rot).astype(np.complex64)

def estimate_const_phase(x_ref, x):
    z = x * np.conj(x_ref)
    return np.angle(np.mean(z))

def correct_const_phase(x, theta):
    return (x * np.exp(-1j * theta)).astype(np.complex64)

def prep_two_channel_for_beamforming(x1, x2, fs_hz, do_cfo=True):
    x1 = normalize_rms(remove_dc(x1))
    x2 = normalize_rms(remove_dc(x2))
    n = min(len(x1), len(x2))
    x1, x2 = x1[:n], x2[:n]

    # Time align
    lag = estimate_integer_delay(x1, x2)
    x2a = apply_integer_delay(x2, lag)
    n = min(len(x1), len(x2a))
    x1, x2a = x1[:n], x2a[:n]

    # CFO (should be tiny if clocks truly shared)
    if do_cfo:
        cfo = estimate_cfo_hz(x1, x2a, fs_hz)
        x2a = correct_cfo(x2a, cfo, fs_hz)
    else:
        cfo = 0.0

    # Constant phase
    theta = estimate_const_phase(x1, x2a)
    x2b = correct_const_phase(x2a, theta)

    return x1, x2b, {"lag_samples": lag, "cfo_hz": cfo, "phase_rad": theta}


def calibrate_and_save(x1_raw, x2_raw, fs_hz, fc_hz, cal_path, capture_dir=None):
    """Estimate calibration offsets and save to JSON. Does not apply corrections."""
    x1 = normalize_rms(remove_dc(x1_raw))
    x2 = normalize_rms(remove_dc(x2_raw))
    n = min(len(x1), len(x2))
    x1, x2 = x1[:n], x2[:n]

    lag = estimate_integer_delay(x1, x2)
    x2a = apply_integer_delay(x2, lag)
    n = min(len(x1), len(x2a))
    x1, x2a = x1[:n], x2a[:n]

    cfo = estimate_cfo_hz(x1, x2a, fs_hz)
    x2a = correct_cfo(x2a, cfo, fs_hz)

    theta = estimate_const_phase(x1, x2a)

    cal = {
        "lag_samples": int(lag),
        "cfo_hz": float(cfo),
        "phase_rad": float(theta),
        "fc_hz": float(fc_hz),
        "fs_hz": float(fs_hz),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if capture_dir is not None:
        cal["capture_dir"] = str(capture_dir)

    with open(cal_path, "w") as f:
        json.dump(cal, f, indent=2)

    return cal


def load_calibration(cal_path):
    """Load calibration dict from JSON."""
    with open(cal_path) as f:
        return json.load(f)


def apply_calibration(x1_raw, x2_raw, cal, fs_hz):
    """Apply pre-computed calibration offsets. No estimation performed."""
    x1 = normalize_rms(remove_dc(x1_raw))
    x2 = normalize_rms(remove_dc(x2_raw))
    n = min(len(x1), len(x2))
    x1, x2 = x1[:n], x2[:n]

    x2 = apply_integer_delay(x2, cal["lag_samples"])
    n = min(len(x1), len(x2))
    x1, x2 = x1[:n], x2[:n]

    if cal["cfo_hz"] != 0:
        x2 = correct_cfo(x2, cal["cfo_hz"], fs_hz)
    x2 = correct_const_phase(x2, cal["phase_rad"])

    return x1, x2
