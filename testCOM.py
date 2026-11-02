# 测试串口是否连接
import time
import serial

def iter_frames(ser):
    buf = bytearray()
    while True:
        buf += ser.read(4096)
        while True:
            try:
                s = buf.index(ord("B"))
            except ValueError:
                buf.clear()
                break
            e = buf.find(b"OV", s + 1)
            if e < 0:
                if s > 0:
                    del buf[:s]
                break
            frame = bytes(buf[s:e+2])
            del buf[:e+2]
            yield frame

PORT="COM11"
BAUD=115200

with serial.Serial(PORT, BAUD, timeout=0.2) as ser:
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
        bytes_from_frames += len(f)   # 每帧长度（你这里通常是 41）

    dt = time.perf_counter() - t0
    print(f"frames={n}, seconds={dt:.3f}, rate={n/dt:.2f} Hz")
    print(f"bytes_from_frames={bytes_from_frames}, bytes_per_sec={bytes_from_frames/dt:.1f}")
    print("first=", first)
    print("last =", last)