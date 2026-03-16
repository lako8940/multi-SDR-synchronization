"""
two-captures.py — Simultaneous IQ capture from two RTL-SDR dongles.

The core challenge: we need both dongles to start recording at the same time.
If one starts 1 ms before the other, that's 2,400 samples of offset right from
the start — before any signal even arrives. We use threading + a Barrier to get
both dongles as close to simultaneous as possible.

HOW THREADING WORKS (for newcomers)
─────────────────────────────────────
Normally Python runs one instruction at a time, in order.  Threading lets you
spawn additional "worker" threads that run concurrently — the OS rapidly switches
between them, so they appear to run at the same time.

Think of it like a restaurant kitchen:
  - The head chef (main thread) preps and coordinates.
  - Two line cooks (worker threads) each handle their own station simultaneously.
  - The head chef doesn't plate the dish until BOTH cooks have finished their prep.

Here we use two synchronization primitives from the `threading` module:

  Barrier(n)  — A meeting point for n threads.  Each thread calls barrier.wait()
                and blocks there until ALL n threads have arrived.  Then they all
                unblock simultaneously.  Used here to fire both SDR readers at the
                same instant.

  Event()     — A shared flag (initially False).  Any thread can set it True with
                stop_event.set().  Other threads can check it with
                stop_event.is_set().  Used here so the main thread can signal
                workers to stop after the capture duration has elapsed.

Execution timeline:
  t=0   main thread opens both SDRs, starts worker threads
  t≈0   worker 0 hits barrier.wait() and blocks
  t≈0   worker 1 hits barrier.wait() and blocks
  t≈0   main thread hits barrier.wait() — all 3 parties present → barrier releases
  t≈0   worker 0 calls sdr.read_samples_async (starts DMA transfer on SDR 0)
  t≈0   worker 1 calls sdr.read_samples_async (starts DMA transfer on SDR 1)
  t=5s  main thread wakes from time.sleep(duration_s), calls cancel_read_async()
  t≈5s  both workers exit their read loops, flush files, and close devices
  t≈5s  main thread joins (waits for) each worker to confirm clean exit
"""

import time
import threading
from pathlib import Path
import numpy as np
from rtlsdr import RtlSdr


def capture_two_rtls(out_dir="captures/run001",
                     fc_hz=868_100_000,   # center frequency: 868.1 MHz (LoRa band)
                     fs_hz=2_400_000,     # sample rate: 2.4 MSPS
                     gain_db=30,          # RF gain in dB
                     duration_s=5.0,      # capture length in seconds
                     ppm=0):              # frequency correction (ppm); 0 = no correction
    """
    Open both RTL-SDR dongles, configure them identically, and capture IQ samples
    simultaneously for `duration_s` seconds.  Writes one .c64 binary file per
    channel plus a meta.txt with capture parameters.

    Output files:
        <out_dir>/ch0_rtl<idx>.c64  — raw complex64 IQ samples for channel 0
        <out_dir>/ch1_rtl<idx>.c64  — raw complex64 IQ samples for channel 1
        <out_dir>/meta.txt          — dict with fc, fs, gain, duration, device indices

    The .c64 format is a flat binary array of np.complex64 values (pairs of
    float32: I then Q).  Load with: np.fromfile(path, dtype=np.complex64)
    """

    # ── Output directory ──────────────────────────────────────────────────────
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)  # create captures/run001/ if missing

    # ── Device discovery ──────────────────────────────────────────────────────
    # RTL-SDR dongles are identified by a serial number burned into flash.
    # Sorting the serial numbers gives a consistent CH0/CH1 assignment across
    # runs — without this, the OS can enumerate the same physical dongle as
    # device 0 or device 1 unpredictably depending on USB enumeration order.
    serial_numbers = sorted(RtlSdr.get_device_serial_addresses())
    device_indices = [RtlSdr.get_device_index_by_serial(s) for s in serial_numbers]

    # ── Open and configure both SDRs ──────────────────────────────────────────
    # Both dongles are configured identically: same frequency, same sample rate,
    # same gain.  The freq_correction (PPM trim) is skipped when ppm=0 because
    # set_freq_correction(0) causes a LIBUSB_ERROR_INVALID_PARAM crash in the
    # RTL-SDR driver.  With the shared SI5351 clock, the true PPM error is ~0
    # anyway, so this is safe to omit.
    sdrs = []
    for idx in device_indices:
        s = RtlSdr(device_index=idx)
        s.sample_rate = fs_hz
        s.center_freq = fc_hz
        s.gain = gain_db
        if ppm:
            s.freq_correction = ppm
        sdrs.append(s)

    # ── Synchronization primitives ────────────────────────────────────────────
    # Barrier(3): one slot for each worker thread (CH0, CH1) + one for main.
    # All three must call barrier.wait() before any of them can proceed.
    # This is what synchronizes the start of both SDR read loops.
    barrier = threading.Barrier(3)

    # Event: a simple shared flag, initially False.
    # Main thread calls stop_event.set() after duration_s to signal workers to stop.
    # (In this script, stop is actually driven by cancel_read_async rather than
    # polling stop_event, but the event is kept for future use / readability.)
    stop_event = threading.Event()

    # ── Worker function (runs once per thread) ────────────────────────────────
    def worker(name, sdr, out_path):
        """
        Each worker:
          1. Opens the output file for binary writing.
          2. Defines a callback that fires every time the SDR fills a USB buffer.
          3. Waits at the barrier until all threads (and main) are ready.
          4. Starts async reading — this blocks the worker until cancelled.
          5. On exit (via cancel), flushes and closes the file, then closes the SDR.

        The callback pattern (read_samples_async + cb) is the RTL-SDR library's
        preferred way to stream continuous data: the C library calls `cb` from
        within the worker thread every time a 256k-sample USB buffer is ready.
        This avoids Python-level polling and keeps up with the 2.4 MSPS data rate.
        """
        f = open(out_path, "wb")  # binary write; will accumulate all captured samples

        def cb(samples, _ctx):
            # `samples` is a numpy array of complex64 from the SDR library.
            # Cast explicitly to complex64 to guarantee dtype, then dump raw bytes
            # to the file.  This is called every ~107 ms (256k samples / 2.4 MSPS).
            np.asarray(samples, dtype=np.complex64).tofile(f)

        # Block here until all threads + main have reached their barrier.wait().
        # This is the key synchronization step — ensures both SDRs start reading
        # as close to simultaneously as possible.
        barrier.wait()

        try:
            # Start continuous async streaming.  This call BLOCKS the worker thread
            # until sdr.cancel_read_async() is called from another thread (main).
            sdr.read_samples_async(cb, num_samples=256*1024)
        finally:
            # Cleanup runs whether we exited normally or via an exception.
            # cancel_read_async() is called defensively in case it wasn't already.
            try: sdr.cancel_read_async()
            except: pass
            f.flush(); f.close()   # ensure all buffered data is written to disk
            try: sdr.close()       # release USB device handle
            except: pass

        print(f"{name} wrote {out_path}")

    # ── Start worker threads ──────────────────────────────────────────────────
    # For each SDR, we create a Thread targeting our worker function and pass
    # the channel name, sdr object, and output path as arguments.
    # daemon=True means these threads will be forcibly killed if the main thread
    # exits unexpectedly (e.g., KeyboardInterrupt), preventing zombie processes.
    paths = []
    threads = []
    for k, (idx, sdr) in enumerate(zip(device_indices, sdrs)):
        p = out_dir / f"ch{k}_rtl{idx}.c64"
        paths.append(p)
        t = threading.Thread(target=worker, args=(f"CH{k}", sdr, p), daemon=True)
        t.start()   # spawns the thread; worker immediately runs and hits barrier.wait()
        threads.append(t)

    # ── Main thread: synchronize start, then sleep for capture duration ───────
    # Main reaches its barrier slot here.  The moment this executes, all three
    # parties have arrived → barrier releases → both workers proceed to
    # read_samples_async at (nearly) the same instant.
    barrier.wait()

    # Sleep while the workers stream IQ data into their files.
    # The GIL (Global Interpreter Lock) is released during time.sleep() and during
    # the C-level USB callbacks, so the worker threads run freely during this time.
    time.sleep(duration_s)

    # Signal workers to stop by cancelling the async read on each SDR.
    # This unblocks read_samples_async inside each worker, causing the try/finally
    # cleanup to run.
    stop_event.set()
    for s in sdrs:
        try: s.cancel_read_async()
        except: pass

    # ── Wait for all workers to finish ────────────────────────────────────────
    # t.join() blocks until that thread exits.  timeout=5 s is a safety net —
    # if a worker hangs (e.g., USB stall), we don't wait forever.
    for t in threads:
        t.join(timeout=5)

    # ── Write metadata ────────────────────────────────────────────────────────
    # Save capture parameters alongside the IQ files so verify-sync.py and other
    # scripts can reconstruct fs, fc, etc. without hardcoding them.
    meta = {
        "fc_hz": fc_hz,
        "fs_hz_requested": fs_hz,
        "gain_db": gain_db,
        "ppm": ppm,
        "device_indices": list(device_indices),
        "duration_s": duration_s
    }
    (out_dir / "meta.txt").write_text(str(meta))
    print("Done:", meta)


if __name__ == "__main__":
    capture_two_rtls()