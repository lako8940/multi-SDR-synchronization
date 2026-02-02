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

## Dependencies

- `numpy` - array/numerical operations
- `scipy.signal` - resampling (`resample_poly`), FIR filter design (`firwin`), filtering (`lfilter`, `bilinear`)
- `matplotlib` - visualization of IQ data, spectra, beamforming results
- `pyrtlsdr` (`rtlsdr.RtlSdr`) - RTL-SDR device control
- `threading` - concurrent capture from multiple devices

## File Descriptions

- `two-rtl-IQ-capture.py` - Main script: device enumeration, simultaneous IQ capture, and processing

## Development Notes

- This is an early-stage repo; capture and synchronization logic is being built out
- The project will scale to four channels once the two-channel proof-of-concept is validated
- Portfolio context: demonstrates hands-on RF hardware integration, DSP algorithm implementation, and system-level thinking
