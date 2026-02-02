import numpy as np
from scipy.signal import resample_poly, firwin, bilinear, lfilter
import matplotlib.pyplot as plt
from rtlsdr import RtlSdr
import time
import threading
from pathlib import Path

def open_rtls():
    """
    opens all RTL-SDR devices currently attached to session
    
    Args:
        none
    Returns:
        array of RTL-SDR objects in "sdrs"
    """
    # Get a list of detected device serial numbers (str)
    serial_numbers = RtlSdr.get_device_serial_addresses()
    # Put serial numbers in correct order
    serial_numbers = np.sort(serial_numbers)
    print(serial_numbers)

    # Get list of device numbers
    device_numbers = []
    for serial in serial_numbers:
        # Find the device index for a given serial number
        device_numbers.append(RtlSdr.get_device_index_by_serial(serial))
    # Open RTLs
    sdrs = []
    for device in device_numbers:
        sdrs.append(RtlSdr(device_index=device))
    print(sdrs)
    return sdrs

def close_rtls(sdrs):
    for rtl in sdrs:
        rtl.close()

def capture_two_rtls():
    """
    captures IQ from two RTL-SDRs
    """
    
    sdrs = open_rtls()

sdr = []
sdr = open_rtls()
close_rtls(sdr)

