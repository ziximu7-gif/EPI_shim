"""Windows eight-channel current-source bench stability test.

One high-current test per execution. Outputs raw CSV, per-channel summary,
and three PNG figures. Requires an operator and independent physical safeguards.

RX protocol (inferred from user samples, not manufacturer-verified):
  53 bytes: 'B' + 8 * int32 little-endian mA +
            8 * uint16 big-endian candidate temperature / 100 +
            2 reserved bytes + 'OV'.
TX protocol: 'BDAC' + ASCII channel 1..8 + signed int32 LE mA + 'OV'.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import struct
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import serial

from config import get_setting

FRAME_LENGTH = 53
CHANNELS = 8
SINGLE_MAX_POSITIVE_MA = 2500  # authorized bench test only, NOT production limit
SINGLE_MAX_NEGATIVE_MA = -2000
ALL_MAX_ABS_MA = 2000


@dataclass(frozen=True)
class Feedback:
    current_ma: tuple[int, ...]
    temperature_candidate_c: tuple[float, ...]
    extra_hex: str


def decode_feedback(frame: bytes) -> Feedback:
    """Decode one complete, fixed-length 53-byte feedback frame."""
    if len(frame) != FRAME_LENGTH or frame[:1] != b"B" or frame[-2:] != b"OV":
        raise ValueError("Invalid 53-byte status frame")
    currents = struct.unpack("<8i", frame[1:33])
    temps = tuple(int.from_bytes(frame[33 + 2*i:35 + 2*i], "big") / 100.0
                  for i in range(CHANNELS))
    return Feedback(currents, temps, frame[49:51].hex().upper())


class FrameReader:
    """Recover fixed-length status frames from an arbitrary serial byte stream."""

    def __init__(self, ser: serial.Serial):
        self.ser = ser
        self.buf = bytearray()
        self.discarded_bytes = 0

    def read_frame(self, timeout_s: float = 2.0) -> bytes:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            while True:
                start = self.buf.find(b"B")
                if start < 0:
                    self.discarded_bytes += len(self.buf)
                    self.buf.clear()
                    break
                if start:
                    self.discarded_bytes += start
                    del self.buf[:start]
                if len(self.buf) < FRAME_LENGTH:
                    break
                if self.buf[51:53] == b"OV":
                    frame = bytes(self.buf[:FRAME_LENGTH])
                    del self.buf[:FRAME_LENGTH]
                    return frame
                # False header candidate: advance one byte, then re-sync.
                self.discarded_bytes += 1
                del self.buf[0]
            available = self.ser.in_waiting
            chunk = self.ser.read(min(max(available, 1), 4096))
            if chunk:
                self.buf.extend(chunk)
                if len(self.buf) > 65536:
                    raise RuntimeError("Serial RX buffer overflow: status cannot be trusted")
        raise TimeoutError(f"No valid 53-byte status frame within {timeout_s:.1f}s")


def build_targets(args: argparse.Namespace) -> tuple[int, ...]:
    if args.mode == "single":
        if not (1 <= args.channel <= CHANNELS):
            raise ValueError("--channel must be in 1..8")
        ma = args.current_ma
        if not (SINGLE_MAX_NEGATIVE_MA <= ma <= SINGLE_MAX_POSITIVE_MA):
            raise ValueError("Single-channel bench limit: -2000..+2500 mA")
        if ma == SINGLE_MAX_POSITIVE_MA and not args.permit_single_2500:
            raise ValueError("2500 mA requires --permit-single-2500 after hardware approval")
        targets = [0] * CHANNELS
        targets[args.channel - 1] = ma
        return tuple(targets)
    if args.mode == "all-pos":
        return (ALL_MAX_ABS_MA,) * CHANNELS
    if args.mode == "all-neg":
        return (-ALL_MAX_ABS_MA,) * CHANNELS
    if args.mode == "all-mixed":
        return tuple(ALL_MAX_ABS_MA if i % 2 == 0 else -ALL_MAX_ABS_MA
                     for i in range(CHANNELS))
    raise ValueError(f"Unknown mode: {args.mode}")


def make_bench_command(channel: int, current_ma: int,
                       configured_min_ma: int, configured_max_ma: int,
                       args: argparse.Namespace) -> bytes:
    """Keep production limits, except explicit isolated single-channel +2.5A bench case."""
    if not 1 <= channel <= CHANNELS:
        raise ValueError("Channel out of range")
    within_production = configured_min_ma <= current_ma <= configured_max_ma
    special = (args.mode == "single" and args.permit_single_2500
               and channel == args.channel and current_ma == 2500)
    if not (within_production or special):
        raise ValueError(
            f"CH{channel}: {current_ma}mA outside production config limits "
            f"[{configured_min_ma}, {configured_max_ma}]mA. "
            "Only explicitly permitted CH single +2500mA is allowed in bench mode."
        )
    return b"BDAC" + str(channel).encode("ascii") + struct.pack("<i", current_ma) + b"OV"


def send_command(ser: serial.Serial, command: bytes) -> None:
    count = ser.write(command)
    if count != len(command):
        raise IOError(f"Partial serial write: {count}/{len(command)} bytes")
    ser.flush()
    time.sleep(0.1)  # Firmware processing interval verified for switching in this setup.


def zero_all(ser: serial.Serial, min_ma: int, max_ma: int,
             args: argparse.Namespace) -> None:
    errors = []
    for channel in range(1, CHANNELS + 1):
        try:
            send_command(ser, make_bench_command(channel, 0, min_ma, max_ma, args))
        except Exception as exc:
            errors.append(f"CH{channel}: {exc}")
    if errors:
        raise RuntimeError("Some ZERO commands failed to transmit: " + "; ".join(errors))


def apply_targets(ser: serial.Serial, targets: tuple[int, ...],
                  min_ma: int, max_ma: int, args: argparse.Namespace) -> None:
    for channel, current_ma in enumerate(targets, start=1):
        send_command(ser, make_bench_command(channel, current_ma,
                                             min_ma, max_ma, args))


def new_output_dir(base: Path, mode: str, channel: int | None = None) -> Path:
    label = mode if channel is None else f"single_CH{channel}"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = base / f"{stamp}_{label}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def csv_headers() -> list[str]:
    return (["timestamp", "elapsed_s", "frame_hex", "extra_hex"]
            + [f"target_CH{i}_mA" for i in range(1, 9)]
            + [f"current_CH{i}_mA" for i in range(1, 9)]
            + [f"temp_candidate_CH{i}_C" for i in range(1, 9)])


def record_test(args: argparse.Namespace, out_dir: Path,
                targets: tuple[int, ...], config: dict) -> None:
    port = args.port or config["serial"]["port"]
    baud = int(config["serial"]["baudrate"])
    min_ma = -round(float(config["current"]["limits"]["negative_A"]) * 1000)
    max_ma = round(float(config["current"]["limits"]["positive_A"]) * 1000)
    # Validate ALL commands before opening serial / energizing any channel.
    for ch, mA in enumerate(targets, 1):
        make_bench_command(ch, mA, min_ma, max_ma, args)
    if not args.confirm_external_protection:
        raise ValueError(
            "High-current bench tests require --confirm-external-protection: "
            "verify independent thermal protection for channels with unreliable "
            "temperature readout, water flow, power limits and active supervision."
        )

    print(f"\nPort={port}, baud={baud}, targets(mA)={list(targets)}")
    print(f"Run length={args.minutes} min; software provisional temperature stop={args.temp_limit_c} C")
    print(f"Untrusted temperature channels={sorted(args.untrusted_temp_channels)}")
    response = input("Type RUN to begin ONE high-current test (anything else cancels): ").strip()
    if response != "RUN":
        raise RuntimeError("Cancelled before opening the serial port")

    csv_path = out_dir / "feedback.csv"
    metadata = {
        "mode": args.mode, "channel": args.channel if args.mode == "single" else None,
        "targets_mA": targets, "duration_minutes": args.minutes,
        "port": port, "baudrate": baud,
        "temperature_decoding": "CANDIDATE: uint16 big endian / 100; not validated",
        "untrusted_temperature_channels": sorted(args.untrusted_temp_channels),
        "software_temp_stop_C": args.temp_limit_c,
        "hardware_warning": "Never interpret serial temperature as an independent thermal safeguard",
    }
    (out_dir / "run_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    print("Opening serial port and zeroing all channels...")
    with serial.Serial(port=port, baudrate=baud, timeout=0.1, write_timeout=1) as ser:
        try:
            zero_all(ser, min_ma, max_ma, args)
            ser.reset_input_buffer()
            reader = FrameReader(ser)
            # Confirm unsolicited data are present before energizing high-current load.
            for _ in range(3):
                f = decode_feedback(reader.read_frame(timeout_s=3))
                if any(abs(v) > 5 for v in f.current_ma):
                    raise RuntimeError("Nonzero actual currents after zeroing: "
                                       + str(f.current_ma))
            print("Baseline status verified; applying test setpoints...")
            apply_targets(ser, targets, min_ma, max_ma, args)
            # Clear old buffered frames after command burst, then allow settling.
            time.sleep(1.5)
            ser.reset_input_buffer()
            reader = FrameReader(ser)
            first_time = time.monotonic()
            deadline = first_time + args.minutes * 60.0
            last_csv = -1e9
            last_print = -1e9
            errors_in_row = 0
            rows_written = 0
            last_temp = {}  # trusted channel -> (timestamp, candidate temperature)
            with csv_path.open("w", newline="", encoding="utf-8-sig") as fout:
                writer = csv.writer(fout)
                writer.writerow(csv_headers())
                fout.flush()
                while time.monotonic() < deadline:
                    frame = reader.read_frame(timeout_s=2.0)
                    fb = decode_feedback(frame)
                    now = time.monotonic()
                    elapsed = now - first_time

                    # Supplemental monitor only; MUST NOT substitute external hardware cutoff.
                    for ch, temp in enumerate(fb.temperature_candidate_c, start=1):
                        if ch in args.untrusted_temp_channels:
                            continue
                        if not math.isfinite(temp) or not (-5.0 <= temp <= 80.0):
                            raise RuntimeError(f"CH{ch}: invalid candidate temp {temp} C")
                        if temp >= args.temp_limit_c:
                            raise RuntimeError(f"CH{ch}: candidate temp {temp:.2f} C "
                                               f">= {args.temp_limit_c} C; stopping")
                        old = last_temp.get(ch)
                        if old and now - old[0] < 3.0 and abs(temp - old[1]) > 3.0:
                            raise RuntimeError(f"CH{ch}: candidate temperature jumped "
                                               f"{old[1]:.2f} to {temp:.2f} C; stopping")
                        last_temp[ch] = (now, temp)

                    if any(abs(value - target) > args.max_current_error_ma
                           for value, target in zip(fb.current_ma, targets)):
                        errors_in_row += 1
                        if errors_in_row >= 5:
                            raise RuntimeError("Repeated current deviation beyond "
                                               f"{args.max_current_error_ma}mA: "
                                               f"actual={fb.current_ma}")
                    else:
                        errors_in_row = 0

                    if elapsed - last_csv >= args.log_interval_s:
                        writer.writerow([
                            datetime.now().astimezone().isoformat(timespec="milliseconds"),
                            f"{elapsed:.3f}", frame.hex(" ").upper(), fb.extra_hex,
                            *targets, *fb.current_ma,
                            *[f"{x:.2f}" for x in fb.temperature_candidate_c]
                        ])
                        fout.flush()
                        rows_written += 1
                        last_csv = elapsed
                    if elapsed - last_print >= 30:
                        print(f"t={elapsed/60:.1f}min current(mA)={fb.current_ma} "
                              f"temp_candidate(C)={fb.temperature_candidate_c}")
                        last_print = elapsed
            print(f"Completed: {rows_written} rows -> {csv_path}")
        finally:
            print("Attempting to reset ALL channels to 0mA...")
            try:
                zero_all(ser, min_ma, max_ma, args)
                time.sleep(0.3)
                ser.reset_input_buffer()
                zero_reader = FrameReader(ser)
                # Verify several *new* feedback frames after the reset commands.
                for _ in range(3):
                    feedback = decode_feedback(zero_reader.read_frame(timeout_s=3))
                    if any(abs(value) > 5 for value in feedback.current_ma):
                        raise RuntimeError("ZERO readback failed: "
                                           f"reported currents={feedback.current_ma}")
                print("ZERO readback within +/-5mA on all channels. "
                      "Also verify independently on the hardware display.")
            except Exception as exc:
                print("CRITICAL: ZERO commands or readback failed:", exc, file=sys.stderr)
                print("Use hardware emergency shutdown; do not trust serial control!", file=sys.stderr)
                raise


def output_plots_and_stats(out_dir: Path, untrusted_temp_channels: set[int]) -> None:
    data_path = out_dir / "feedback.csv"
    if not data_path.is_file():
        return
    with data_path.open("r", newline="", encoding="utf-8-sig") as f:
        records = list(csv.DictReader(f))
    if not records:
        print("No complete feedback samples; no plots produced.")
        return

    # Machine-readable summary; includes ALL feedback values but flags unreliable temps.
    summary_path = out_dir / "summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["channel", "target_mA", "mean_mA", "std_mA", "min_mA",
                    "max_mA", "peak_to_peak_mA", "mean_error_mA",
                    "temperature_mean_candidate_C", "temperature_peak_candidate_C",
                    "temperature_trust"])
        for ch in range(1, 9):
            values = [int(row[f"current_CH{ch}_mA"]) for row in records]
            temps = [float(row[f"temp_candidate_CH{ch}_C"]) for row in records]
            target = int(records[0][f"target_CH{ch}_mA"])
            w.writerow([ch, target, round(statistics.mean(values), 4),
                        round(statistics.stdev(values), 4) if len(values) > 1 else 0,
                        min(values), max(values), max(values) - min(values),
                        round(statistics.mean(values) - target, 4),
                        round(statistics.mean(temps), 4), max(temps),
                        "UNTRUSTED" if ch in untrusted_temp_channels else "UNVERIFIED"])

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("Install matplotlib to generate PNG plots: python -m pip install matplotlib")
        print(f"CSV and summary saved in: {out_dir}")
        return

    t = [float(row["elapsed_s"]) / 60 for row in records]
    # Retain all samples in CSV; downsample only plot points for memory / speed.
    stride = max(1, len(t) // 5000)
    indices = range(0, len(t), stride)

    def create_plot(filename: str, title: str, ylabel: str, mode: str,
                    channels=None) -> None:
        if channels is None:
            channels = range(1, 9)

        fig, ax = plt.subplots(figsize=(12, 6))
        for ch in channels:
            if mode == "current":
                key = f"current_CH{ch}_mA"
                vals = [int(records[i][key]) for i in indices]
            elif mode == "error":
                key = f"current_CH{ch}_mA"
                tkey = f"target_CH{ch}_mA"
                vals = [int(records[i][key]) - int(records[i][tkey]) for i in indices]
            else:
                key = f"temp_candidate_CH{ch}_C"
                vals = [float(records[i][key]) for i in indices]
            style = "--" if mode == "temperature" and ch in untrusted_temp_channels else "-"
            suffix = " (untrusted)" if mode == "temperature" and ch in untrusted_temp_channels else ""
            ax.plot(t[::stride], vals, linewidth=1.0, linestyle=style,
                    label=f"CH{ch}{suffix}")
        ax.set(title=title, xlabel="Elapsed time (min)", ylabel=ylabel)
        ax.grid(alpha=0.25)
        ax.legend(ncol=4, fontsize=8)
        fig.tight_layout()
        fig.savefig(out_dir / filename, dpi=160)
        plt.close(fig)

    # One overview image combining all user-requested signals.
    fig, axes = plt.subplots(3, 1, figsize=(14, 11), sharex=True)
    for ch in range(1, 9):
        measured = [int(records[i][f"current_CH{ch}_mA"]) for i in indices]
        errors = [int(records[i][f"current_CH{ch}_mA"])
                  - int(records[i][f"target_CH{ch}_mA"]) for i in indices]
        temps = [float(records[i][f"temp_candidate_CH{ch}_C"]) for i in indices]
        axes[0].plot(t[::stride], measured, linewidth=0.9, label=f"CH{ch}")
        axes[1].plot(t[::stride], errors, linewidth=0.9, label=f"CH{ch}")
        axes[2].plot(t[::stride], temps, linewidth=0.9,
                     linestyle="--" if ch in untrusted_temp_channels else "-",
                     label=f"CH{ch}" + (" (untrusted)" if ch in untrusted_temp_channels else ""))
    for ax, ylabel in zip(axes, ["Feedback (mA)", "Error (mA)",
                                 "Candidate temp (deg C)"]):
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.25)
        ax.legend(ncol=8, fontsize=7, loc="upper right")
    axes[2].set_xlabel("Elapsed time (min)")
    fig.suptitle("Eight-channel current-source stability (candidate temp is NOT verified)")
    fig.tight_layout()
    fig.savefig(out_dir / "00_overview.png", dpi=150)
    plt.close(fig)

    create_plot("01_current_mA.png", "Eight-channel feedback currents", "Current (mA)", "current")
    create_plot("02_current_error_mA.png", "Feedback minus commanded current", "Current error (mA)", "error")
    create_plot("03_temp_candidate_C.png", "Candidate temperatures (NOT validated)",
                "Candidate temperature (deg C)", "temperature")
    create_plot(
        "04_temp_candidate_C_excl_CH5_CH8.png",
        "Candidate temperatures (excluding CH5 and CH8; NOT validated)",
        "Candidate temperature (deg C)",
        "temperature",
        channels=[1, 2, 3, 4, 6, 7]
    )
    # 新增：排除 CH5 的电流误差图
    create_plot(
        "05_current_error_excl_CH5.png",
        "Current error (excluding CH5)",
        "Current error (mA)",
        "error",
        channels=[1, 2, 3, 4, 6, 7, 8]
    )
    print(f"Graphs and summary saved in: {out_dir}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True,
                        choices=["single", "all-pos", "all-neg", "all-mixed"])
    parser.add_argument("--channel", type=int, default=1,
                        help="Single-channel test: 1..8")
    parser.add_argument("--current-ma", type=int, default=2400,
                        help="Single-channel test setpoint; try 2400, -2000, or approved 2500")
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument("--port", type=str, default=None, help="Override config COM port")
    parser.add_argument("--output", type=Path, default=Path("stability_results"))
    parser.add_argument("--log-interval-s", type=float, default=0.5)
    parser.add_argument("--temp-limit-c", type=float, default=28.0)
    parser.add_argument("--max-current-error-ma", type=int, default=100)
    parser.add_argument("--untrusted-temp-channels", type=str, default="5,8",
                        help="Channels with untrustworthy serial temperatures; default 5,8")
    parser.add_argument("--permit-single-2500", action="store_true",
                        help="Separate approved bench exception, CH single only")
    parser.add_argument("--confirm-external-protection", action="store_true",
                        help="Confirm independent hardware thermal/current protection and supervision")
    args = parser.parse_args(argv)
    if not (0 < args.minutes <= 1440):
        parser.error("--minutes must be >0 and <=1440")
    if not (0.1 <= args.log_interval_s <= 10.0):
        parser.error("--log-interval-s must be 0.1..10")
    if not (20 <= args.temp_limit_c <= 30):
        parser.error("--temp-limit-c must be 20..30")
    if not (1 <= args.max_current_error_ma <= 100):
        parser.error("--max-current-error-ma must be 1..100")
    try:
        args.untrusted_temp_channels = {
            int(s.strip()) for s in args.untrusted_temp_channels.split(",") if s.strip()
        }
    except ValueError:
        parser.error("--untrusted-temp-channels expects CSV channel numbers, e.g. 5,8")
    if any(ch < 1 or ch > 8 for ch in args.untrusted_temp_channels):
        parser.error("Untrusted temperature channels must be 1..8")
    targets = build_targets(args)
    config = get_setting()
    # Preflight before making an output directory.
    min_ma = -round(float(config["current"]["limits"]["negative_A"]) * 1000)
    max_ma = round(float(config["current"]["limits"]["positive_A"]) * 1000)
    for ch, mA in enumerate(targets, 1):
        make_bench_command(ch, mA, min_ma, max_ma, args)
    if not args.confirm_external_protection:
        parser.error("Requires --confirm-external-protection after checking independent protection")
    out_dir = new_output_dir(args.output, args.mode,
                             args.channel if args.mode == "single" else None)
    error = None
    try:
        record_test(args, out_dir, targets, config)
    except (Exception, KeyboardInterrupt) as exc:
        error = exc
        print(f"TEST ABORTED: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        try:
            output_plots_and_stats(out_dir, args.untrusted_temp_channels)
        except Exception as exc:
            print(f"PLOT ERROR (raw data remains): {exc}", file=sys.stderr)
    print("Output directory:", out_dir.resolve())
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
