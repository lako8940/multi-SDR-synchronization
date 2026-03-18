"""
two-element-DF.py:
This program performs direction finding with two elements
"""
from beamform_ready import (
    perform_two_element_cal, perform_two_element_DF
)

CAL_PATH = "captures/calibration/cal.json"

perform_two_element_cal(cal_path=CAL_PATH)
perform_two_element_DF(cal_path=CAL_PATH)