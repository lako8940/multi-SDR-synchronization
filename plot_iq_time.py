#!/usr/bin/env python3
"""Plot time domain representation of an IQ file (.c64 complex64 format)."""

import numpy as np
import matplotlib.pyplot as plt
import argparse
from pathlib import Path


def load_iq(filepath):
    """Load IQ data from a .c64 file (complex64 format)."""
    return np.fromfile(filepath, dtype=np.complex64)


def plot_iq_time_domain(iq_data, fs_hz=2.4e6, start_sample=0, num_samples=None, title=None):
    """Plot I/Q components and magnitude in time domain."""
    if num_samples is not None:
        iq_data = iq_data[start_sample:start_sample + num_samples]
    else:
        iq_data = iq_data[start_sample:]

    t_ms = np.arange(len(iq_data)) / fs_hz * 1000  # time in milliseconds

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)

    # I component
    axes[0].plot(t_ms, iq_data.real, linewidth=0.5)
    axes[0].set_ylabel('I (In-phase)')
    axes[0].set_title(title or 'IQ Time Domain')
    axes[0].grid(True, alpha=0.3)

    # Q component
    axes[1].plot(t_ms, iq_data.imag, linewidth=0.5, color='orange')
    axes[1].set_ylabel('Q (Quadrature)')
    axes[1].grid(True, alpha=0.3)

    # Magnitude
    axes[2].plot(t_ms, np.abs(iq_data), linewidth=0.5, color='green')
    axes[2].set_ylabel('Magnitude')
    axes[2].set_xlabel('Time (ms)')
    axes[2].grid(True, alpha=0.3)

    plt.tight_layout()
    return fig


def main():
    parser = argparse.ArgumentParser(description='Plot IQ file in time domain')
    parser.add_argument('iq_file', type=str, help='Path to .c64 IQ file')
    parser.add_argument('--fs', type=float, default=2.4e6, help='Sample rate in Hz (default: 2.4e6)')
    parser.add_argument('--start', type=int, default=0, help='Start sample index')
    parser.add_argument('--num', type=int, default=None, help='Number of samples to plot (default: all)')
    parser.add_argument('--save', type=str, default=None, help='Save plot to file instead of displaying')
    args = parser.parse_args()

    filepath = Path(args.iq_file)
    if not filepath.exists():
        print(f"Error: File not found: {filepath}")
        return 1

    print(f"Loading {filepath}...")
    iq_data = load_iq(filepath)
    print(f"Loaded {len(iq_data)} samples ({len(iq_data)/args.fs:.3f} seconds at {args.fs/1e6:.2f} MSPS)")

    plot_iq_time_domain(
        iq_data,
        fs_hz=args.fs,
        start_sample=args.start,
        num_samples=args.num,
        title=filepath.name
    )

    if args.save:
        plt.savefig(args.save, dpi=150)
        print(f"Saved plot to {args.save}")
    else:
        plt.show()

    return 0


if __name__ == '__main__':
    exit(main())
