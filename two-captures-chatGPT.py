import time
import threading
from pathlib import Path
import numpy as np
from rtlsdr import RtlSdr

def capture_two_rtls(out_dir="captures/run001",
                     fc_hz=868_100_000,
                     fs_hz=2_400_000,
                     gain_db=30.0,
                     duration_s=5.0,
                     ppm=0):

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    serial_numbers = sorted(RtlSdr.get_device_serial_addresses())
    device_indices = [RtlSdr.get_device_index_by_serial(s) for s in serial_numbers]

    sdrs = []
    for idx in device_indices:
        s = RtlSdr(device_index=idx)
        s.sample_rate = fs_hz
        s.center_freq = fc_hz
        s.gain = gain_db
        s.freq_correction = ppm
        sdrs.append(s)

    barrier = threading.Barrier(3)  # 2 worker threads + main
    stop_event = threading.Event()

    def worker(name, sdr, out_path):
        f = open(out_path, "wb")

        def cb(samples, _ctx):
            np.asarray(samples, dtype=np.complex64).tofile(f)

        barrier.wait()
        try:
            sdr.read_samples_async(cb, num_samples=256*1024)
        finally:
            try: sdr.cancel_read_async()
            except: pass
            f.flush(); f.close()
            try: sdr.close()
            except: pass
        print(f"{name} wrote {out_path}")

    paths = []
    threads = []
    for k, (idx, sdr) in enumerate(zip(device_indices, sdrs)):
        p = out_dir / f"ch{k}_rtl{idx}.c64"
        paths.append(p)
        t = threading.Thread(target=worker, args=(f"CH{k}", sdr, p), daemon=True)
        t.start()
        threads.append(t)

    barrier.wait()
    time.sleep(duration_s)
    stop_event.set()

    for s in sdrs:
        try: s.cancel_read_async()
        except: pass

    for t in threads:
        t.join(timeout=5)

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
