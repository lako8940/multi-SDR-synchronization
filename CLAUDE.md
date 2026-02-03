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
- **CFO**: Near-zero with shared clock, but estimated and stored for completeness.
- **Phase offset**: R820T PLL locks to an arbitrary initial phase on each power-up/retune. Changes every session.

### Calibration workflow

Calibration must be performed **once per session** (after tuning, before beamforming captures). It does NOT need to be repeated per-capture as long as the SDR devices remain open and tuned.

1. HackRF One transmits a CW calibration tone **over the air** — all receivers capture it simultaneously through their antennas (no power divider needed, scales to 4+ channels)
2. `calibrate_and_save()` estimates offsets and writes `cal.json` (lag, CFO, phase, plus metadata: fc_hz, fs_hz, timestamp)
3. `load_calibration()` + `apply_calibration()` load the JSON and apply corrections to subsequent captures without re-estimating

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
- `verify-sync.py` - Runs full calibration pipeline on a capture directory, saves `cal.json`, loads it back, applies corrections, and plots before/after verification

## Known Issues and Fixes Applied

- **`[R82XX] PLL not locked!`**: Appears on capture startup with the external SI5351 clock. Can be ignored — captures and calibration are verified valid (tone visible in spectrum).
- **`freq_correction = 0` crashes**: The RTL-SDR driver rejects `set_freq_correction(0)` with `LIBUSB_ERROR_INVALID_PARAM`. Fixed by only setting `freq_correction` when ppm is non-zero.
- **numpy types not JSON-serializable**: `calibrate_and_save()` produced numpy `float32`/`int64` values that `json.dump` rejected. Fixed by casting `lag`, `cfo`, `phase`, `fc_hz`, `fs_hz` to native Python `int`/`float` in `beamform_ready.py`.
- **USB buffer exhaustion with two SDRs**: Two simultaneous async readers exceed the default `usbfs_memory_mb` limit. Fix: `sudo sh -c 'echo 0 > /sys/module/usbcore/parameters/usbfs_memory_mb'`

## Validated Calibration Results (868.1 MHz, 2.4 MSPS)

- HackRF transmitting CW at 868.0 MHz confirmed via spectrum plot (tone at -0.1 MHz from 868.1 MHz center)
- Integer delay: ~2113 samples (~0.88 ms) — typical USB scheduling jitter
- CFO: ~0.005 Hz — effectively zero, confirming shared clock coherence
- Phase offset: ~0.04 rad (~2.4 deg) — arbitrary PLL lock phase, as expected

## Development Notes

- This is an early-stage repo; capture and synchronization logic is being built out
- The project will scale to four channels once the two-channel proof-of-concept is validated
- Portfolio context: demonstrates hands-on RF hardware integration, DSP algorithm implementation, and system-level thinking
