# Project Context

## Overview

Two-RTL-SDR synchronization proof-of-concept, part of a larger four-channel beamforming system. This repo serves as a GitHub portfolio piece targeting RF/DSP engineering roles.

## Full System Architecture

```
Custom Patch Antennas → Clock-Coherent RTL-SDRs → Custom Python Processing → Beamforming Output
```

- **Antennas**: Custom patch antennas (designed separately)
- **Receivers**: RTL-SDR dongles sharing a common external clock via SI5351 clock generator
- **Signal source**: HackRF One (stable reference transmitter for calibration/testing)
- **Processing**: Python scripts for IQ capture, offset calibration, and beamforming

## This Repo's Scope (Two-Radio Proof-of-Concept)

1. **Simultaneous IQ capture** from two clock-coherent RTL-SDRs
2. **Determine fixed offsets** (time/phase/frequency) between the two receivers using a known signal source (HackRF One)
3. **Apply corrections** to make captures beamforming-ready
4. **Rudimentary beamforming** demonstration to validate the concept

## Hardware Setup

- 2x RTL-SDR dongles with external clock injection (SI5351)
- 1x HackRF One as stable signal source / calibration transmitter
- SI5351 provides coherent clock to both RTL-SDRs (replaces their individual oscillators)

## Key Technical Details

- RTL-SDR device enumeration uses serial numbers for consistent device mapping
- Threading is used for simultaneous multi-device capture
- Signal processing chain: IQ capture → resampling/filtering → offset estimation → correction → beamforming

## Calibration Architecture

The shared SI5351 clock provides **frequency coherence** (identical sample rates, no drift) but NOT phase coherence or sample-aligned startup. Each capture session has non-deterministic offsets due to:

- **Integer sample delay**: USB bulk transfer start times differ per dongle (OS scheduling, USB bus arbitration). Changes every capture.
- **CFO**: Zero with shared SI5351 clock — not estimated or applied.
- **Phase offset**: R820T PLL locks to an arbitrary initial phase on each power-up/retune. Changes every session.

### Calibration workflow

Calibration must be performed **once per session** (after tuning, before beamforming captures). It does NOT need to be repeated per-capture as long as the SDR devices remain open and tuned.

1. HackRF One transmits a **repeating LFM chirp** over the air — all receivers capture simultaneously through their antennas (no power divider needed, scales to 4+ channels)
2. `calibrate_and_save()` estimates offsets and writes `cal.json` (lag, CFO, phase, plus metadata: fc_hz, fs_hz, timestamp)
3. `load_calibration()` + `apply_calibration()` load the JSON and apply corrections to subsequent captures without re-estimating

### Calibration signal requirements

A pure CW tone **cannot** be used for integer delay estimation: its cross-correlation magnitude is flat across all lags (no unique peak). The calibration signal must have time structure. A repeating chirp is the correct choice because:
- It gives a sharp sinc-like cross-correlation peak (delay resolution ≈ 1/BW)
- It matches the LoRa chirp modulation used as the beamforming target signal

**GNU Radio chirp setup (HackRF transmit):**
- `Signal Source` (Float, Sawtooth, freq=**25 Hz**, amplitude=125000, offset=0)  ← chirp period must be >> max_lag window (20k samples = 8.3 ms); 25 Hz → 96k samples = 40 ms
- `VCO` (sensitivity=6.2832, samp_rate=2,400,000) → complex chirp sweeping ±125 kHz
- `osmocom Sink` at 868.0 MHz, gain=40 dB, samp_rate=2,400,000

**Do NOT use:**
- Pure CW — ambiguous cross-correlation
- Pulsed CW — breaks `estimate_cfo_hz` (random off-period phase corrupts `np.unwrap`)
- Sawtooth > ~50 Hz — chirp period shorter than max_lag causes multiple replica peaks; algorithm picks wrong one

### Why calibrate-once-store-forever doesn't work

The integer delay and phase offset are non-deterministic at each USB/PLL startup. Stored values from a previous session are invalid. The over-the-air calibration avoids hardware recabling and takes a single short capture to complete.

## Dependencies

- `numpy` - array/numerical operations
- `scipy.signal` - resampling (`resample_poly`), FIR filter design (`firwin`), filtering (`lfilter`, `bilinear`)
- `matplotlib` - visualization of IQ data, spectra, beamforming results
- `pyrtlsdr` (`rtlsdr.RtlSdr`) - RTL-SDR device control
- `threading` - concurrent capture from multiple devices

## File Descriptions

- `two-rtl-IQ-capture.py` - Main script: device enumeration, simultaneous IQ capture, and processing
- `two-captures-chatGPT.py` - Alternate capture script: dynamic serial enumeration, threaded async capture with barrier synchronization, saves raw `.c64` IQ files and `meta.txt`
- `beamform_ready.py` - Calibration and correction library: offset estimation (integer delay, CFO, phase), JSON cal save/load, and apply-only correction path
- `verify-sync.py` - Runs full calibration pipeline on a capture directory, saves `cal.json`, loads it back, applies corrections, and plots verification (4 panels: raw spectra, pre-correction cross-correlation, phase difference with smoothed trend, post-correction cross-correlation peaking at lag=0)

## Known Issues and Fixes Applied

- **`[R82XX] PLL not locked!`**: Appears on capture startup with the external SI5351 clock. Can be ignored — captures and calibration are verified valid (tone visible in spectrum).
- **`freq_correction = 0` crashes**: The RTL-SDR driver rejects `set_freq_correction(0)` with `LIBUSB_ERROR_INVALID_PARAM`. Fixed by only setting `freq_correction` when ppm is non-zero.
- **numpy types not JSON-serializable**: `calibrate_and_save()` produced numpy `float32`/`int64` values that `json.dump` rejected. Fixed by casting `lag`, `cfo`, `phase`, `fc_hz`, `fs_hz` to native Python `int`/`float` in `beamform_ready.py`.
- **USB buffer exhaustion with two SDRs**: Two simultaneous async readers exceed the default `usbfs_memory_mb` limit. Fix: `sudo sh -c 'echo 0 > /sys/module/usbcore/parameters/usbfs_memory_mb'`
- **CFO estimation destroys alignment on low-SNR chirp**: `calibrate_and_save` originally estimated CFO via `np.polyfit` on `np.unwrap(np.angle(z))`. With RTL-SDR receiving a weak chirp (per-sample SNR ≈ −4 dB), the instantaneous phase has ~64° RMS noise — `np.unwrap` fails constantly, polyfit fits garbage, and the resulting "CFO" of ~−47 Hz applied over 5 s sweeps 233 full rotations, destroying phase coherence (coherence dropped from 0.28 → 0.008). Fixed by removing CFO estimation entirely: SI5351 shared clock guarantees true CFO ≈ 0 Hz.
- **Phase estimate biased by chirp beat over full signal**: After removing CFO, estimating phase via `np.angle(np.mean(z))` over the full 12M-sample capture is suppressed by the chirp beat (fractional sample lag creates a slowly oscillating phase difference that partially cancels in the mean). Fixed by estimating phase from a short 4 ms window (`est_n = int(0.004 * fs_hz)`) starting just past the zero-pad boundary — beat rotates < 0.1 rad in 4 ms, so sinc suppression < 0.2%.
- **verify-sync.py plots the zero-padded region**: `apply_integer_delay` prepends zeros to x2 when lag < 0. The phase and time-domain panels originally started at sample 0, falling inside the padding. Fixed by starting both panels at `abs(lag) + 200` samples (`valid_start`).

## Calibration Signal History

- **CW tone (original)**: Failed — flat cross-correlation, no peak
- **Pulsed CW**: Partial — gave cross-correlation peak but broke `estimate_cfo_hz` due to random off-period phase in `np.unwrap`; CFO measured as −350 Hz instead of ~0
- **Repeating chirp, 500 Hz sawtooth**: Failed — chirp period (4800 samples) shorter than max_lag window (20k), producing 8+ replica peaks; algorithm selected wrong one
- **Repeating chirp, 25 Hz sawtooth (current)**: Working — single clean peak, integer delay ~788–792 samples (~0.33 ms), CFO not estimated (shared clock), phase corrected to within ~1°

## Validated Calibration Results (868.1 MHz, 2.4 MSPS)

- HackRF transmitting LFM chirp (±125 kHz sweep, 25 Hz repeat) at 868.0 MHz
- Integer delay: ~792 samples (~0.33 ms) — typical USB scheduling jitter
- CFO: not estimated; SI5351 shared clock guarantees ~0 Hz
- Phase offset: ~−1° residual after correction (estimated from 4 ms window)
- **Verification**: post-correction cross-correlation peaks at lag=0 ✓ (proven by Panel 4 of verify-sync.py)

## Target Signal

The end goal is beamforming of **LoRa** transmissions at 868 MHz. LoRa uses chirp spread spectrum (CSS) — a repeating LFM chirp — which is why the chirp calibration signal is a natural fit. The calibration pipeline and beamforming corrections developed here will apply directly to LoRa signal processing.

## Development Notes

- Calibration pipeline (lag + phase) is **functionally complete and verified** — post-correction cross-correlation peaks at lag=0, mean phase residual < 1°
- The project will scale to four channels once the two-channel proof-of-concept is validated
- Portfolio context: demonstrates hands-on RF hardware integration, DSP algorithm implementation, and system-level thinking
