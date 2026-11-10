"""恒流源串口会话：打开、监控、退出归零。"""
from contextlib import contextmanager
import serial
from dacControl import reset_all_currents
from serialMonitor import SerialMonitor

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
        mon = SerialMonitor(ser)
        mon.start()
        try:
            yield ser
        finally:
            try:
                reset_all_currents(ser)
            finally:
                mon.stop()
