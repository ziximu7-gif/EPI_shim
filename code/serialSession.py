from contextlib import contextmanager
import serial

from dacControl import reset_all_currents


@contextmanager
def coil_session(cfg):
    """创建恒流源串口会话，退出时固定尝试归零。"""

    with serial.Serial(
        port=cfg["serial"]["port"],
        baudrate=cfg["serial"]["baudrate"],
        timeout=cfg["serial"]["timeout_s"],
        write_timeout=1.0,
    ) as ser:

        ser.reset_input_buffer()

        try:
            yield ser
        finally:
            reset_all_currents(ser)
