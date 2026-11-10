import csv
import math

from config import get_setting, paths
from dacControl import make_dac_cmd, send_dac_cmd
from serialSession import coil_session


class SerialMonitor:
    def __init__(self, ser: serial.Serial):
        self.ser = ser
        self.frames = iter_frames(ser)
        self.stop_evt = threading.Event()
        self.flush_evt = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_evt.set()
        self.thread.join(timeout=1.0)

    def flush(self):
        self.ser.reset_input_buffer()
        self.flush_evt.set()

    def _run(self):
        while not self.stop_evt.is_set():
            try:
                frame = next(self.frames)
            except Exception as e:
                print("MONITOR ERROR:", e)
                return
            if self.flush_evt.is_set():
                self.flush_evt.clear()
                continue
            st = parse_status_frame(frame)
            ts = time.perf_counter()
            if st is None:
                print(f"{ts:.6f} RX RAW: {frame!r}")
            else:
                print(f"{ts:.6f} RX STATUS: ch={st.ch} status={st.status}")



def load_shimming_data(file_path: str):
    cfg = get_setting()
    mode = cfg["shim"]["mode"]
    unit = str(cfg["control"]["csv_current_unit"]).upper()
    if unit not in ("A", "MA"):
        raise ValueError(f"Unsupported CSV current unit: {unit}")
    scale = 1000.0 if unit == "A" else 1.0
    with open(file_path, "r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if len(rows) != 9:
        raise ValueError(
            f"Expected header + 8 coil rows, got {len(rows)} rows"
        )
    data_rows = rows[1:]
    n_cols = len(data_rows[0])
    if n_cols == 0 or any(len(row) != n_cols for row in data_rows):
        raise ValueError("CSV column counts are inconsistent")
    slice_modes = (
        "slice_wise_tra",
        "slice_wise_cor",
        "slice_wise_sag",
    )
    if mode == "global":
        expected_cols = 1
    elif mode in slice_modes:
        expected_cols = int(cfg["control"]["slice_count"])
    else:
        raise ValueError(f"Unsupported shim mode: {mode}")
    if n_cols != expected_cols:
        raise ValueError(
            f"Mode {mode} expects {expected_cols} columns, "
            f"but CSV contains {n_cols}"
        )
    layers_data = []
    for col in range(n_cols):
        currents = []
        for ch in range(8):
            value = float(data_rows[ch][col])
            if not math.isfinite(value):
                raise ValueError(
                    f"Invalid current: ch{ch+1}, column {col+1}"
                )
            currents.append(round(value * scale))
        layers_data.append(currents)
    return layers_data

def apply_currents(ser, currents):
    if len(currents) != 8:
        raise ValueError("Exactly 8 channel currents are required")
    for channel, current_mA in enumerate(currents, start=1):
        cmd = send_dac_cmd(ser, channel, current_mA)
        actual_mA = int.from_bytes(
            cmd[5:9], byteorder="little", signed=True
        )
        print(
            f"CH{channel}: requested={current_mA} mA, "
            f"sent={actual_mA} mA, "
            f"HEX={cmd.hex(' ').upper()}"
        )
    ser.flush()

def main():
    cfg = get_setting()

    mode = cfg["shim"]["mode"]

    csv_file = str(paths(cfg).shimming_csv())

    shimming_layers = load_shimming_data(csv_file)

    for currents in shimming_layers:
        for channel, val in enumerate(currents, start=1):
            make_dac_cmd(channel, val)

    print(f"Shim mode: {mode}")
    print(f"Loaded {len(shimming_layers)} current sets")
    
    with coil_session(cfg) as ser:
        if mode == "global":
            print("Global shim mode")
            input("Press ENTER to apply global currents...")
            apply_currents(ser, shimming_layers[0])
            input(
                "Global currents are active. "
                "Press ENTER after scan to finish..."
            )
        else:
            total = len(shimming_layers)
            for idx, currents in enumerate(shimming_layers):
                print(f"Slice {idx+1}/{total}")
                input("Press ENTER to apply slice currents...")
                apply_currents(ser, currents)

if __name__ == "__main__":
    main()
