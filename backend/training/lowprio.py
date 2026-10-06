"""Run a training script at below-normal CPU priority so the live server stays responsive.
Usage: python training/lowprio.py training/train_detector.py <args...>"""
import ctypes
import runpy
import sys

if sys.platform == "win32":
    k = ctypes.windll.kernel32
    k.SetPriorityClass(k.GetCurrentProcess(), 0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
