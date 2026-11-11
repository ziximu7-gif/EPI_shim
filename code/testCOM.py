# 测试串口是否连接
import os
import sys
import time
import serial

from config import get_setting
from serialMonitor import iter_frames

_cfg = get_setting()

PORT = _cfg["serial"]["port"]
BAUD = _cfg["serial"]["baudrate"]

with serial.Serial(PORT, BAUD, timeout=_cfg["serial"]["timeout_s"]) as ser:
    ser.reset_input_buffer()
    time.sleep(0.2)
    ser.reset_input_buffer()

    frames = iter_frames(ser)

    duration = 3.0
    t0 = time.perf_counter()
    n = 0
    bytes_from_frames = 0
    first = None
    last = None

    while time.perf_counter() - t0 < duration:
        f = next(frames)              # 唯一的数据入口
        if first is None:
            first = f
        last = f
        n += 1
        bytes_from_frames += len(f)   # 每帧长度

    dt = time.perf_counter() - t0
    print(f"frames={n}, seconds={dt:.3f}, rate={n/dt:.2f} Hz")
    print(f"bytes_from_frames={bytes_from_frames}, bytes_per_sec={bytes_from_frames/dt:.1f}")
    print("first=", first)
    print("last =", last)