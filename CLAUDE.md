# Project Context

## Overview

Two-RTL-SDR synchronization and direction-finding proof-of-concept, part of a larger four-channel beamforming system. This repo serves as a GitHub portfolio piece targeting RF/DSP engineering roles.

## Full System Architecture

```
Custom Patch Antennas → Clock-Coherent RTL-SDRs → Custom Python Processing → DF / Beamforming Output
```

- **Antennas**: Custom patch antennas (designed separately), highly directional — front hemisphere only
- **Receivers**: RTL-SDR dongles sharing a common external clock via SI5351 clock generator
- **Signal source**: HackRF One (stable reference transmitter for calibration/testing)
- **Processing**: Python scripts for IQ capture, offset calibration, AoA estimation, and beamforming

## This Repo's Scope (Two-Radio Proof-of-Concept)

1. **Simultaneous IQ capture** from two clock-coherent RTL-SDRs
2. **Calibrate per-session offsets** (USB integer delay + phase) using a known over-the-air chirp signal
3. **Apply corrections** to subsequent captures
4. **Two-element direction finding** — estimate angle of arrival and display on a live polar plot

## Hardware Setup

- 2x RTL-SDR dongles with external clock injection (SI5351)
- 1x HackRF One as stable signal source / calibration transmitter
- SI5351 provides coherent clock to both RTL-SDRs (replaces their individual oscillators)
- Elements assumed to be half-wavelength spaced (d = λ/2) for DF

## Key Technical Details

- RTL-SDR device enumeration uses serial numbers (sorted) for consistent CH0/CH1 assignment
- Threading + `threading.Barrier(3)` fires both async readers simultaneously
- Signal processing chain: IQ capture → DC removal + RMS normalisation → cross-correlation delay → phase estimation → correction → AoA

## Calibration Architecture

The shared SI5351 clock provides **frequency coherence** (identical sample rates, no drift) but NOT phase coherence or sample-aligned startup. Each session has non-deterministic offsets due to:

- **Integer sample delay**: USB bulk transfer start times differ per dongle (OS scheduling, USB bus arbitration). Changes every session.
- **CFO**: Zero with shared SI5351 clock — not estimated or applied.
- **Phase offset**: R820T PLL locks to an arbitrary initial phase on each power-up/retune. Changes every session.

### Calibration workflow

Calibration must be performed **once per session** (after tuning, before DF captures). It does NOT need to be repeated per-capture as long as the SDR devices remain open and tuned.

1. HackRF One transmits a **repeating LFM chirp** over the air — all receivers capture simultaneously through their antennas (no power divider needed, scales to 4+ channels)
2. `perform_two_element_cal()` handles the entire flow: prompts operator, captures IQ into memory, estimates and prints the USB delay, writes `cal.json` (lag, phase, fc_hz, fs_hz, timestamp)
3. `load_calibration()` + `apply_calibration()` load the JSON and apply corrections to subsequent captures without re-estimating

### Calibration signal requirements

A pure CW tone **cannot** be used for integer delay estimation: its cross-correlation magnitude is flat across all lags (no unique peak). The calibration signal must have time structure. A repeating chirp is the correct choice because:
- It gives a sharp sinc-like cross-correlation peak (delay resolution ≈ 1/BW)
- It matches the LoRa chirp modulation used as the target signal

**GNU Radio chirp setup (HackRF transmit):**
- `Signal Source` (Float, Sawtooth, freq=**25 Hz**, amplitude=125000, offset=0)  ← chirp period must be >> max_lag window (20k samples = 8.3 ms); 25 Hz → 96k samples = 40 ms
- `VCO` (sensitivity=6.2832, samp_rate=2,400,000) → complex chirp sweeping ±125 kHz
- `osmocom Sink` at 868.0 MHz, gain=40 dB, samp_rate=2,400,000

**Do NOT use:**
- Pure CW — flat cross-correlation, no peak
- Pulsed CW — cross-correlation peak exists but the off-period phase is random, biasing any phase estimation
- Sawtooth > ~50 Hz — chirp period shorter than max_lag causes multiple replica peaks; algorithm picks wrong one

### Why calibrate-once-store-forever doesn't work

The integer delay and phase offset are non-deterministic at each USB/PLL startup. Stored values from a previous session are invalid. The over-the-air calibration avoids hardware recabling and takes a single short capture to complete.

## `beamform_ready.py` API

### Signal conditioning
- `load_c64(path)` — load raw complex64 IQ file
- `remove_dc(x)` — subtract mean
- `normalize_rms(x)` — RMS normalise

### Offset estimation
- `estimate_integer_delay(x_ref, x, max_lag)` — cross-correlation peak on pre-processed IQ; positive lag = x delayed vs x_ref
- `find_USB_delay(x1_raw, x2_raw, max_lag)` — preprocesses raw IQ then calls `estimate_integer_delay`; public entry point for USB jitter estimation
- `estimate_const_phase(x_ref, x)` — mean phase of x relative to x_ref over the supplied window

### Correction application
- `apply_integer_delay(x, lag)` — shift x to align to reference
- `correct_const_phase(x, theta)` — rotate x by -theta

### Calibration pipeline
- `calibrate_and_save(x1_raw, x2_raw, fs_hz, fc_hz, cal_path)` — estimate USB delay + phase, write `cal.json`
- `load_calibration(cal_path)` — load cal dict from JSON
- `apply_calibration(x1_raw, x2_raw, cal)` — apply saved lag + phase; returns `(x1, x2)` normalised and corrected

### Hardware entry points
- `perform_two_element_cal(cal_path, fc_hz, fs_hz, gain_db, duration_s, ppm)` — full calibration in one call: operator prompt → simultaneous capture → USB delay print → `calibrate_and_save`
- `perform_two_element_DF(cal_path, fs_hz, gain_db, ppm, chunk_s, history_s)` — live direction finding: loads cal, streams IQ, estimates AoA per chunk, updates polar + history plot until window is closed

### cal.json schema
```json
{
  "lag_samples": <int>,
  "phase_rad":   <float>,
  "fc_hz":       <float>,
  "fs_hz":       <float>,
  "timestamp":   "<ISO 8601 UTC>"
}
```

## Direction Finding

### AoA formula (half-wavelength spacing)

For d = λ/2 element spacing and a plane wave arriving at angle θ from broadside:

```
Δφ = 2π · d · sin(θ) / λ = π · sin(θ)
θ  = arcsin(Δφ / π)
```

After `apply_calibration` removes the fixed session offsets, the residual inter-element phase `angle(mean(x1 * conj(x2)))` is purely geometric — it maps directly to θ.  θ = 0° is broadside; ±90° is end-fire. Directional antennas constrain valid arrivals to the front hemisphere (±90°).

### Live plot layout
- **Left**: half-polar (±90°), 0° at top, needle + numeric readout
- **Right**: rolling time-history of AoA estimates

## Dependencies

- `numpy` — array/numerical operations
- `scipy.signal` — `fftconvolve` for cross-correlation
- `matplotlib` — verification plots and live DF display
- `pyrtlsdr` (`rtlsdr.RtlSdr`) — RTL-SDR device control
- `threading` — concurrent capture from multiple devices

## File Descriptions

- `beamform_ready.py` — core library: signal conditioning, USB delay estimation, calibration pipeline, live DF
- `two-element-DF.py` — top-level script: calls `perform_two_element_cal` then `perform_two_element_DF`
- `two_captures.py` — standalone capture utility: saves raw `.c64` IQ files + `meta.txt` for offline analysis
- `verify-sync.py` — offline verification: runs calibration on a saved capture directory, prints step-by-step coherence, plots 4-panel verification (spectra, pre-correction xcorr, phase difference, post-correction xcorr)
- `test_pipeline_synthetic.py` — unit test: generates synthetic chirp with known lag + phase, runs pipeline, verifies estimates match truth and plots results
- `plot_iq_time.py` — utility: time-domain and spectral plots of saved `.c64` files

## Known Issues and Fixes Applied

- **`[R82XX] PLL not locked!`**: Appears on capture startup with the external SI5351 clock. Can be ignored — captures and calibration are verified valid (tone visible in spectrum).
- **`freq_correction = 0` crashes**: The RTL-SDR driver rejects `set_freq_correction(0)` with `LIBUSB_ERROR_INVALID_PARAM`. Fixed by only calling `set_freq_correction` when ppm is non-zero.
- **numpy types not JSON-serializable**: `calibrate_and_save()` produced numpy `float32`/`int64` values that `json.dump` rejected. Fixed by casting `lag`, `phase`, `fc_hz`, `fs_hz` to native Python `int`/`float`.
- **USB buffer exhaustion with two SDRs**: Two simultaneous async readers exceed the default `usbfs_memory_mb` limit. Fix: `sudo sh -c 'echo 0 > /sys/module/usbcore/parameters/usbfs_memory_mb'`
- **CFO estimation destroys alignment on low-SNR chirp**: Originally estimated via `np.polyfit` on `np.unwrap(np.angle(z))`. With RTL-SDR receiving a weak chirp (per-sample SNR ≈ −4 dB), `np.unwrap` fails constantly, polyfit fits garbage, and the resulting spurious CFO applied over 5 s sweeps 233 full rotations (coherence dropped from 0.28 → 0.008). Fixed by removing CFO estimation entirely — `estimate_cfo_hz` and `correct_cfo` deleted from codebase. SI5351 shared clock guarantees true CFO ≈ 0 Hz.
- **Phase estimate biased by chirp beat over full signal**: Estimating phase via `np.angle(np.mean(z))` over the full 12M-sample capture is suppressed by the chirp beat (fractional-sample lag creates a slowly oscillating phase difference that partially cancels in the mean). Fixed by estimating from a short 4 ms window (`est_n = int(0.004 * fs_hz)`) starting just past the zero-pad boundary — beat rotates < 0.1 rad in 4 ms, sinc suppression < 0.2%.
- **verify-sync.py plots the zero-padded region**: `apply_integer_delay` prepends zeros to x2 when lag < 0. Fixed by starting all panels at `abs(lag) + 200` samples (`valid_start`).

## Calibration Signal History

- **CW tone (original)**: Failed — flat cross-correlation, no peak
- **Pulsed CW**: Partial — gave cross-correlation peak but random off-period phase biases phase estimation
- **Repeating chirp, 500 Hz sawtooth**: Failed — chirp period (4800 samples) shorter than max_lag window (20k), producing 8+ replica peaks; algorithm selected wrong one
- **Repeating chirp, 25 Hz sawtooth (current)**: Working — single clean peak, integer delay ~788–792 samples (~0.33 ms), phase corrected to within ~1°

## Validated Calibration Results (868.1 MHz, 2.4 MSPS)

- HackRF transmitting LFM chirp (±125 kHz sweep, 25 Hz repeat) at 868.0 MHz
- Integer delay: ~792 samples (~0.33 ms) — typical USB scheduling jitter
- CFO: not estimated; SI5351 shared clock guarantees ~0 Hz
- Phase offset: ~−1° residual after correction (estimated from 4 ms window)
- **Verification**: post-correction cross-correlation peaks at lag=0 ✓ (Panel 4 of verify-sync.py)

## Target Signal

The end goal is direction finding and beamforming of **LoRa** transmissions at 868 MHz. LoRa uses chirp spread spectrum (CSS) — a repeating LFM chirp — which is why the chirp calibration signal is a natural fit. The calibration pipeline and AoA corrections developed here apply directly to LoRa signal processing.

## Development Notes

- Calibration pipeline (lag + phase) is **functionally complete and verified** — post-correction cross-correlation peaks at lag=0, mean phase residual < 1°
- Two-element live direction finding is **implemented** — `perform_two_element_DF` streams IQ, estimates AoA per 100 ms chunk, displays half-polar needle + rolling history
- The project will scale to four channels once the two-channel proof-of-concept is validated in the field
- Portfolio context: demonstrates hands-on RF hardware integration, DSP algorithm implementation, and system-level thinking
