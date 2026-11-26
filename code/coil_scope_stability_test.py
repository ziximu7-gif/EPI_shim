"""30-minute coil serial-feedback + Tek MDO4104-6 differential voltage test.

Run on Windows from the repository root:
  uv run python code/coil_scope_stability_test.py ...

Two earth-referenced probes are permitted ONLY if their grounds share the SAME
verified safe circuit reference. Never put ground clips on opposite coil terminals.
For floating outputs use a properly rated differential probe. When --signal coil,
voltage is NOT converted to an independent current measurement because coil
resistance varies with temperature and inductive voltage depends on dI/dt.

Requires existing code/current_stability_test.py, code/config.py, and
pyserial, pyvisa and an installed VISA backend (e.g. TekVISA/NI-VISA).
Independent current/thermal protection and physical operator supervision are
mandatory. Software shutdown is NOT an emergency-off mechanism.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import serial

from config import get_setting
from current_stability_test import (
    FrameReader,
    decode_feedback,
    make_bench_command,
    send_command,
    zero_all,
)


def timestamp_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_tek_number(response: str) -> float:
    """Accept numeric or header-prefixed Tek SCPI measurement responses."""
    match = re.search(r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*$", response.strip())
    if match is None:
        raise ValueError(f"Unrecognized Tektronix numeric response: {response!r}")
    value = float(match.group(1))
    if not math.isfinite(value) or abs(value) >= 1e30:
        raise ValueError(f"Invalid / unavailable Tektronix measurement: {response!r}")
    return value


def setup_scope(scope, plus_channel: str, minus_channel: str) -> None:
    """Show CH+ and CH-, build synchronous math waveform CH+ - CH-."""
    if plus_channel == minus_channel:
        raise ValueError("Scope positive and negative channels must be different")
    scope.write(f"SELECT:{plus_channel} ON")
    scope.write(f"SELECT:{minus_channel} ON")
    scope.write("MATH:TYPE DUAL")
    scope.write(f'MATH:DEFINE "{plus_channel}-{minus_channel}"')
    scope.write("SELECT:MATH ON")
    # Three independently recorded means: positive input, negative input,
    # and the mathematical difference sampled in the SAME scope acquisition.
    for slot, source in ((1, plus_channel), (2, minus_channel), (3, "MATH")):
        scope.write(f"MEASUREMENT:MEAS{slot}:SOURCE1 {source}")
        scope.write(f"MEASUREMENT:MEAS{slot}:TYPE MEAN")
        scope.write(f"MEASUREMENT:MEAS{slot}:STATE ON")
    reply = scope.query("MATH:DEFINE?").strip().upper().replace(" ", "")
    expected = f"{plus_channel}-{minus_channel}"
    if expected not in reply:
        raise RuntimeError(f"Scope MATH expression not confirmed: {reply!r}, expected {expected}")


def read_scope_voltages(scope) -> tuple[float, float, float]:
    """Return V+ vs reference, V- vs reference, and waveform mean(V+ - V-)."""
    return tuple(
        parse_tek_number(scope.query(f"MEASUREMENT:MEAS{slot}:VALUE?"))
        for slot in (1, 2, 3)
    )


class ScopeSampler(threading.Thread):
    def __init__(self, scope, csv_path: Path, t0: float, duration_s: float,
                 interval_s: float, signal: str, shunt_ohms: float | None,
                 stop: threading.Event):
        super().__init__(daemon=True)
        self.scope = scope
        self.csv_path = csv_path
        self.t0 = t0
        self.duration_s = duration_s
        self.interval_s = interval_s
        self.signal = signal
        self.shunt_ohms = shunt_ohms
        self.stop = stop
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            with self.csv_path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp_utc", "elapsed_s", "scope_plus_voltage_V",
                                 "scope_minus_voltage_V", "scope_voltage_V",
                                 "mean_plus_minus_minus_V", "shunt_current_mA", "signal_kind"])
                f.flush()
                next_time = self.t0
                while not self.stop.is_set():
                    now = time.monotonic()
                    if now >= self.t0 + self.duration_s:
                        break
                    if now < next_time:
                        if self.stop.wait(min(next_time - now, 0.2)):
                            break
                        continue
                    v_plus, v_minus, voltage = read_scope_voltages(self.scope)
                    measured_at = time.monotonic()
                    derived_ma = (1000 * voltage / self.shunt_ohms
                                  if self.signal == "shunt" else "")
                    writer.writerow([timestamp_utc(), f"{measured_at - self.t0:.6f}",
                                     f"{v_plus:.9g}", f"{v_minus:.9g}",
                                     f"{voltage:.9g}", f"{v_plus-v_minus:.9g}",
                                     f"{derived_ma:.6f}" if derived_ma != "" else "",
                                     self.signal])
                    f.flush()
                    next_time += self.interval_s
                    if measured_at > next_time:
                        next_time = measured_at + self.interval_s
        except Exception as exc:
            self.error = exc
            self.stop.set()


def summarize_and_plot(output: Path, test_channel: int) -> None:
    with (output / "feedback.csv").open(encoding="utf-8-sig", newline="") as f:
        feedback = list(csv.DictReader(f))
    with (output / "scope.csv").open(encoding="utf-8-sig", newline="") as f:
        scope = list(csv.DictReader(f))
    if not feedback or not scope:
        return
    fb_values = [float(r[f"current_CH{test_channel}_mA"]) for r in feedback]
    voltages = [float(r["scope_voltage_V"]) for r in scope]
    vplus = [float(r["scope_plus_voltage_V"]) for r in scope]
    vminus = [float(r["scope_minus_voltage_V"]) for r in scope]
    shunt_vals = [float(r["shunt_current_mA"]) for r in scope if r["shunt_current_mA"]]

    def stats(values):
        return {"mean": statistics.mean(values),
                "std": statistics.pstdev(values),
                "min": min(values), "max": max(values),
                "peak_to_peak": max(values) - min(values),
                "last_minus_first": values[-1] - values[0],
                "n": len(values)}

    summary = {"feedback_mA": stats(fb_values),
               "scope_plus_voltage_V": stats(vplus),
               "scope_minus_voltage_V": stats(vminus),
               "scope_diff_voltage_V": stats(voltages)}
    aligned_fb = None
    shunt_errors = None
    if shunt_vals:
        import numpy as np
        summary["shunt_current_mA"] = stats(shunt_vals)
        fb_t_s = np.array([float(r["elapsed_s"]) for r in feedback])
        sc_t_s = np.array([float(r["elapsed_s"]) for r in scope])
        # Linear interpolation is used for display/alignment, not true simultaneous sampling.
        aligned_fb = np.interp(sc_t_s, fb_t_s, fb_values)
        shunt_errors = np.asarray(shunt_vals) - aligned_fb
        summary["shunt_minus_feedback_mA"] = stats(shunt_errors.tolist())
        with (output / "aligned_comparison.csv").open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["elapsed_s", "scope_shunt_current_mA",
                             "interpolated_serial_feedback_mA", "shunt_minus_feedback_mA"])
            for t, actual, fb, err in zip(sc_t_s, shunt_vals, aligned_fb, shunt_errors):
                writer.writerow([f"{t:.6f}", f"{actual:.6f}", f"{fb:.6f}", f"{err:.6f}"])
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    fb_t = [float(r["elapsed_s"]) / 60 for r in feedback]
    sc_t = [float(r["elapsed_s"]) / 60 for r in scope]
    fig, axs = plt.subplots(5 if shunt_vals else 3, 1, figsize=(11, 12),
                            constrained_layout=True, sharex=True)
    axs[0].plot(fb_t, fb_values, linewidth=0.8)
    axs[0].set_ylabel(f"CH{test_channel} feedback (mA)")
    axs[1].plot(sc_t, vplus, linewidth=0.8, label="CH+ vs ground")
    axs[1].plot(sc_t, vminus, linewidth=0.8, label="CH- vs ground")
    axs[1].set_ylabel("Individual means (V)")
    axs[1].legend()
    axs[2].plot(sc_t, voltages, linewidth=0.8)
    axs[2].set_ylabel("MATH: V+ - V- (V)")
    if shunt_vals:
        axs[3].plot(sc_t, shunt_vals, linewidth=0.8, label="Shunt-derived")
        axs[3].plot(sc_t, aligned_fb, linewidth=0.8, label="Interpolated feedback")
        axs[3].set_ylabel("Current (mA)")
        axs[3].legend()
        axs[4].plot(sc_t, shunt_errors, linewidth=0.8)
        axs[4].set_ylabel("Shunt - feedback (mA)")
    for ax in axs:
        ax.grid(alpha=0.3)
    axs[-1].set_xlabel("Elapsed time (min)")
    fig.suptitle("Single-coil bench stability: serial feedback + Tektronix scope")
    fig.savefig(output / "voltage_current_stability.png", dpi=170)
    plt.close(fig)


def run(args) -> None:
    if not (1 <= args.channel <= 8):
        raise ValueError("--channel must be 1..8")
    if not 0 < args.minutes <= 60:
        raise ValueError("--minutes must be between 0 and 60")
    if not 0.2 <= args.scope_interval_s <= 30:
        raise ValueError("--scope-interval-s must be 0.2..30")
    if args.signal == "shunt" and not (args.shunt_ohms is not None and
                                       math.isfinite(args.shunt_ohms) and
                                       args.shunt_ohms > 0):
        raise ValueError("--signal shunt requires a calibrated positive --shunt-ohms")
    if args.signal == "coil" and args.shunt_ohms is not None:
        raise ValueError("Do not use --shunt-ohms with coil terminal voltage")
    if args.scope_plus_channel == args.scope_minus_channel:
        raise ValueError("--scope-plus-channel and --scope-minus-channel must differ")
    if not args.confirm_external_protection:
        raise ValueError("Hardware protection and continuous supervision required: "
                         "pass --confirm-external-protection after checking them")

    cfg = get_setting()
    port = args.port or str(cfg["serial"]["port"])
    baud = int(cfg["serial"]["baudrate"])
    lower = -round(float(cfg["current"]["limits"]["negative_A"]) * 1000)
    upper = round(float(cfg["current"]["limits"]["positive_A"]) * 1000)
    ns = SimpleNamespace(mode="single", channel=args.channel, permit_single_2500=False)
    # Reject 2500-mA special bench override; honor current production limits.
    if args.current_ma < lower or args.current_ma > upper or args.current_ma == 0:
        raise ValueError(f"Current {args.current_ma} mA outside approved config limits "
                         f"[{lower}, {upper}] mA, or equals zero")
    targets = [0] * 8
    targets[args.channel - 1] = args.current_ma
    commands = [make_bench_command(i, mA, lower, upper, ns)
                for i, mA in enumerate(targets, 1)]
    output = args.output / (datetime.now().strftime("%Y%m%d_%H%M%S") +
                            f"_CH{args.channel}_scope")
    output.mkdir(parents=True, exist_ok=False)
    metadata = {"port": port, "baud": baud, "channel": args.channel,
                "target_mA": args.current_ma, "scope_resource": args.scope_resource,
                "scope_plus_channel": args.scope_plus_channel,
                "scope_minus_channel": args.scope_minus_channel,
                "scope_math": f"{args.scope_plus_channel}-{args.scope_minus_channel}",
                "signal": args.signal,
                "shunt_ohms": args.shunt_ohms, "duration_minutes": args.minutes,
                "scope_interval_s": args.scope_interval_s,
                "scope_measurement": "MEAS1=positive mean, MEAS2=negative mean, MEAS3=MATH difference mean",
                "risk_notice": "Software reset is not an independent safety interlock",
                "temperature_feedback": "candidate only; CH5/CH8 known unreliable"}
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Data output: {output}")
    print(f"Coil channel CH{args.channel}: {args.current_ma} mA for {args.minutes} minutes")
    print(f"Tektronix {args.scope_plus_channel} - {args.scope_minus_channel}: {args.signal} differential voltage; ", end="")
    print("independent current from calibrated shunt" if args.signal == "shunt"
          else "NOT an independently measured current")
    print("CRITICAL: Earth-grounded probe ground clips must NOT go to opposite coil terminals.")
    print("Use matching grounded references verified safe by the hardware engineer, or a rated differential probe.")
    if input("After external thermal/current protection and probe checks, type RUN: ").strip() != "RUN":
        print("Cancelled, no instrument control performed")
        return

    # Import here so the rest of the code can be inspected or compiled without PyVISA.
    import pyvisa
    rm = pyvisa.ResourceManager()
    scope = None
    sampler = None
    stop = threading.Event()
    try:
        scope = rm.open_resource(args.scope_resource)
        scope.timeout = 5000
        scope.write_termination = "\n"
        scope.read_termination = "\n"
        idn = scope.query("*IDN?").strip()
        print("Connected scope:", idn)
        if "TEKTRONIX" not in idn.upper() or "MDO4104" not in idn.upper():
            raise RuntimeError("Unexpected scope model. Verify VISA resource and IDN")
        setup_scope(scope, args.scope_plus_channel, args.scope_minus_channel)
        readings = None
        for _ in range(10):
            time.sleep(0.5)
            try:
                readings = read_scope_voltages(scope)
                break
            except ValueError:
                # The scope may not yet have calculated the first MEAN values.
                continue
        if readings is None:
            raise RuntimeError("Scope cannot obtain CH+, CH-, and MATH means; check acquisition/trigger")
        print(f"Scope preliminary: V+={readings[0]:.6g} V, "
              f"V-={readings[1]:.6g} V, MATH difference={readings[2]:.6g} V")
        print("Confirm correct scope probe attenuation, input range, and AUTO trigger.")

        with serial.Serial(port=port, baudrate=baud, timeout=0.1, write_timeout=1) as ser:
            run_error = None
            try:
                zero_all(ser, lower, upper, ns)
                ser.reset_input_buffer()
                reader = FrameReader(ser)
                for _ in range(3):
                    initial = decode_feedback(reader.read_frame(timeout_s=3))
                    if any(abs(value) > 5 for value in initial.current_ma):
                        raise RuntimeError("Zero feedback precheck failed: " +
                                           str(initial.current_ma))
                send_command(ser, commands[args.channel - 1])
                t0 = time.monotonic()
                duration = args.minutes * 60.0
                sampler = ScopeSampler(scope, output / "scope.csv", t0, duration,
                                       args.scope_interval_s, args.signal,
                                       args.shunt_ohms, stop)
                sampler.start()
                last_log = -1e30
                bad_current_frames = 0
                last_report = -1e30
                with (output / "feedback.csv").open("w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(["timestamp_utc", "elapsed_s",
                                     *[f"target_CH{i}_mA" for i in range(1, 9)],
                                     *[f"current_CH{i}_mA" for i in range(1, 9)],
                                     *[f"temp_candidate_CH{i}_C" for i in range(1, 9)]])
                    f.flush()
                    while time.monotonic() < t0 + duration:
                        if sampler.error:
                            raise RuntimeError("Scope sampling failed") from sampler.error
                        feedback = decode_feedback(reader.read_frame(timeout_s=2))
                        now = time.monotonic()
                        elapsed = now - t0
                        if any(abs(actual - setpoint) > args.max_error_ma
                               for actual, setpoint in zip(feedback.current_ma, targets)):
                            bad_current_frames += 1
                            if bad_current_frames >= 5:
                                raise RuntimeError("Five consecutive feedback frames "
                                                   "exceeded current deviation threshold")
                        else:
                            bad_current_frames = 0
                        # Supplemental candidate-temperature checks for non-excluded channels.
                        for ch, temp in enumerate(feedback.temperature_candidate_c, 1):
                            if ch not in (5, 8) and (not math.isfinite(temp) or
                                                     temp >= args.temp_limit_c):
                                raise RuntimeError(f"CH{ch}: candidate temperature {temp} C")
                        if elapsed - last_log >= args.feedback_interval_s:
                            writer.writerow([timestamp_utc(), f"{elapsed:.6f}",
                                             *targets, *feedback.current_ma,
                                             *[f"{x:.2f}" for x in feedback.temperature_candidate_c]])
                            f.flush()
                            last_log = elapsed
                        if elapsed - last_report >= 30:
                            print(f"{elapsed/60:.1f} min: serial CH{args.channel}="
                                  f"{feedback.current_ma[args.channel - 1]} mA")
                            last_report = elapsed
                if sampler.error:
                    raise RuntimeError("Scope sampling failed") from sampler.error
            except BaseException as exc:
                run_error = exc
                raise
            finally:
                stop.set()
                if sampler is not None:
                    sampler.join(timeout=8)
                print("Attempting all-channel zero + fresh serial feedback verification...")
                try:
                    zero_all(ser, lower, upper, ns)
                    time.sleep(0.3)
                    ser.reset_input_buffer()
                    reader = FrameReader(ser)
                    for _ in range(3):
                        fb = decode_feedback(reader.read_frame(timeout_s=3))
                        if any(abs(v) > 5 for v in fb.current_ma):
                            raise RuntimeError("Post-test zero feedback not confirmed: " +
                                               str(fb.current_ma))
                    print("Readback near zero; verify independently at hardware")
                except Exception as exc:
                    print("CRITICAL: ZERO COMMAND OR FEEDBACK FAILED:", exc, file=sys.stderr)
                    print("Use approved hardware emergency shutdown; "
                          "never rely on serial cleanup alone", file=sys.stderr)
                    raise
                if sampler is not None and sampler.error is not None and run_error is None:
                    raise RuntimeError("Scope sampler error after loop") from sampler.error
    finally:
        stop.set()
        if scope is not None:
            scope.close()
        rm.close()
        if (output / "feedback.csv").exists() and (output / "scope.csv").exists():
            try:
                summarize_and_plot(output, args.channel)
            except Exception as exc:
                print(f"Analysis error; raw CSV retained: {exc}", file=sys.stderr)
    print("Completed:", output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope-resource", required=True,
                        help="PyVISA USB...::INSTR or TCPIP... resource")
    parser.add_argument("--scope-plus-channel", default="CH1", choices=["CH1", "CH2", "CH3", "CH4"],
                        help="Oscilloscope input probing positive terminal (default CH1)")
    parser.add_argument("--scope-minus-channel", default="CH2", choices=["CH1", "CH2", "CH3", "CH4"],
                        help="Oscilloscope input probing negative terminal (default CH2)")
    parser.add_argument("--port", help="Serial COM port, default config serial.port")
    parser.add_argument("--channel", type=int, required=True, help="Coil control channel 1..8")
    parser.add_argument("--current-ma", type=int, required=True,
                        help="Test setpoint in mA, within JSON production limits")
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument("--signal", choices=["coil", "shunt"], default="coil",
                        help="coil: log differential coil voltage; shunt: calibrated resistor differential voltage")
    parser.add_argument("--shunt-ohms", type=float, help="Calibrated shunt resistance (only with --signal shunt)")
    parser.add_argument("--scope-interval-s", type=float, default=1.0)
    parser.add_argument("--feedback-interval-s", type=float, default=0.5)
    parser.add_argument("--max-error-ma", type=int, default=100)
    parser.add_argument("--temp-limit-c", type=float, default=28.0)
    parser.add_argument("--output", type=Path, default=Path("stability_results"))
    parser.add_argument("--confirm-external-protection", action="store_true")
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        if not 0.1 <= args.feedback_interval_s <= 10:
            raise ValueError("feedback interval must be 0.1..10 s")
        if not 1 <= args.max_error_ma <= 100:
            raise ValueError("max current error must be 1..100 mA")
        if not 20 <= args.temp_limit_c <= 30:
            raise ValueError("temperature cutoff must be 20..30 C")
        run(args)
        return 0
    except KeyboardInterrupt:
        print("Interrupted by operator", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"TEST FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
