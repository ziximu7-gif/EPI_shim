"""Offline regression tests for EPI_shim (no serial port / no MRI hardware).

Place this file at EPI_shim/code/test_shim_pipeline.py and run from
repository root:
    python -m unittest discover -s code -p test_shim_pipeline.py -v

The test executes the REAL optimizer and calibration code extracted from
CoilSliceCurrentCalculation.ipynb, rather than a rewritten copy. It also tests
the real CSV loader and DAC encoder. With the current unnormalized notebook,
the calibration tests are EXPECTED TO FAIL until the Hz/A fix is applied.
"""

import ast
import contextlib
import csv
import io
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import lsq_linear


def locate_root():
    override = os.environ.get("EPI_SHIM_ROOT")
    if override:
        candidates = [Path(override).expanduser().resolve()]
    else:
        here = Path(__file__).resolve().parent
        candidates = [here, *here.parents]
    for directory in candidates:
        if (directory / "Global_parameter.json").is_file() and (
            directory / "code" / "CoilSliceCurrentCalculation.ipynb"
        ).is_file():
            return directory
    raise RuntimeError(
        "EPI_shim root not found. Put this file in EPI_shim/code/ "
        "or set the EPI_SHIM_ROOT environment variable."
    )


ROOT = locate_root()
CODE = ROOT / "code"
sys.path.insert(0, str(CODE))

from dacControl import make_dac_cmd  # noqa: E402; actual repository code

with (ROOT / "Global_parameter.json").open(encoding="utf-8") as f:
    CONFIG = json.load(f)
with (CODE / "CoilSliceCurrentCalculation.ipynb").open(encoding="utf-8") as f:
    NOTEBOOK = json.load(f)


def notebook_code_cell(index):
    cell = NOTEBOOK["cells"][index]
    if cell.get("cell_type") != "code":
        raise ValueError(f"Cell {index} is not code")
    src = cell["source"]
    return "".join(src) if isinstance(src, list) else src


def compile_statements(nodes, label):
    tree = ast.Module(body=nodes, type_ignores=[])
    return compile(ast.fix_missing_locations(tree), label, "exec")


def extract_optimizer():
    """Load the real opt_core_shim_py definition, without running notebook I/O."""
    for idx in range(len(NOTEBOOK["cells"])):
        if NOTEBOOK["cells"][idx].get("cell_type") != "code":
            continue
        tree = ast.parse(notebook_code_cell(idx))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "opt_core_shim_py":
                env = {"np": np, "lsq_linear": lsq_linear}
                exec(compile_statements([node], "notebook_optimizer"), env)
                return env["opt_core_shim_py"]
    raise AssertionError("Notebook has no opt_core_shim_py()")


def extract_calibration_segment():
    """Compile actual notebook fieldmap->basis statements from base_data load onward.

    This deliberately catches the old `coil_data - base_data` mistake.
    The test will continue to use the real code after the notebook is edited.
    """
    for idx in range(len(NOTEBOOK["cells"])):
        if NOTEBOOK["cells"][idx].get("cell_type") != "code":
            continue
        tree = ast.parse(notebook_code_cell(idx))
        for n, node in enumerate(tree.body):
            if not isinstance(node, ast.Assign):
                continue
            target_names = [
                item.id
                for target in node.targets
                for item in ast.walk(target)
                if isinstance(item, ast.Name)
            ]
            if "base_data" in target_names and any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "load_nii"
                for child in ast.walk(node.value)
            ):
                return compile_statements(tree.body[n:], "notebook_calibration")
    raise AssertionError("Notebook base_data = load_nii(...) section not found")


OPTIMIZE = extract_optimizer()
CALIBRATE = extract_calibration_segment()


def ground_truth(shape=(6, 5, 4), seed=12):
    rng = np.random.default_rng(seed)
    basis_hz_per_a = rng.normal(0, 15, size=(8, *shape))
    return basis_hz_per_a


def simulate_calibration(basis_hz_per_a, delta_current_a=None):
    """Feed synthetic NIfTI arrays to the REAL calibration code."""
    cfg = json.loads(json.dumps(CONFIG))
    if delta_current_a is None:
        delta_current_a = (
            float(cfg["current"]["basis_scan_A"])
            - float(cfg["current"]["reference_A"])
        )
    assert delta_current_a != 0
    base_session = cfg["shim"]["base_session"]
    coil_sessions = [
        f"ses-{int(x):02d}" for x in cfg["shim"]["sessions"]
        if f"ses-{int(x):02d}" != base_session
    ]
    assert len(coil_sessions) == 8, "Tests expect 8 calibration scans"
    rng = np.random.default_rng(2026)
    baseline = rng.normal(0, 10, basis_hz_per_a.shape[1:])
    images = {base_session: baseline}
    for i, session in enumerate(coil_sessions):
        images[session] = baseline + delta_current_a * basis_hz_per_a[i]

    def fake_load_nii(path):
        session = Path(path).parent.name
        return images[session].copy(), np.eye(4)

    env = {
        "os": os,
        "np": np,
        "_cfg": cfg,
        "base_session": base_session,
        "coil_sessions": coil_sessions,
        "data_dir": "/synthetic_data",
        "b0map_name": cfg["data"]["b0map_masked_file"],
        "base_path": f"/synthetic_data/{base_session}/{cfg['data']['b0map_masked_file']}",
        "load_nii": fake_load_nii,
    }
    with contextlib.redirect_stdout(io.StringIO()):
        exec(CALIBRATE, env)
    return env["b1_z"], baseline


def optimizer_param(mode):
    return {
        "shim_mode": mode,
        "coilN": 8,
        "shimCLL": np.full(8, -2.0),
        "shimCUL": np.full(8, 2.4),
    }


class ShimPipelineTests(unittest.TestCase):
    def test_01_basis_hz_per_ampere(self):
        """A +0.5 A measurement must yield a Hz/A profile, NOT a Hz difference."""
        expected_basis = ground_truth()
        actual_basis, _ = simulate_calibration(expected_basis)
        np.testing.assert_allclose(
            actual_basis, expected_basis, atol=1e-8, rtol=1e-8,
            err_msg="Basis normalization missing: divide delta B0 [Hz] by delta I [A]",
        )

    def test_02_global_calibration_to_optimizer(self):
        """End-to-end: synthetic calibration -> actual optimizer -> true currents."""
        expected_basis = ground_truth(seed=33)
        basis_from_notebook, _ = simulate_calibration(expected_basis)
        true_currents_a = np.array([0.35, -0.25, 0.12, -0.4,
                                    0.20, 0.10, -0.30, 0.15])
        b0 = -np.tensordot(true_currents_a, expected_basis, axes=(0, 0))
        shimmed, fit = OPTIMIZE(b0, basis_from_notebook, optimizer_param("global"))
        np.testing.assert_allclose(
            fit, true_currents_a, atol=1e-5, rtol=1e-5,
            err_msg="Global recovered current is wrong; check basis scaling and optimization",
        )
        np.testing.assert_allclose(shimmed, 0, atol=1e-5)

    def test_03_global_optimizer_with_correct_basis(self):
        """Separately verify real optimizer independent of calibration."""
        basis = ground_truth(seed=40)
        true = np.array([0.21, -0.16, 0.25, -0.40,
                         0.35, 0.05, -0.31, 0.22])
        b0 = -np.tensordot(true, basis, axes=(0, 0))
        b0[0, 0, 0] = np.nan
        shimmed, fit = OPTIMIZE(b0, basis, optimizer_param("global"))
        np.testing.assert_allclose(fit, true, atol=1e-5)
        self.assertTrue(np.isnan(shimmed[0, 0, 0]))
        np.testing.assert_allclose(shimmed[1:, :, :], 0, atol=1e-5)

    def test_04_slice_wise_orientation_and_currents(self):
        basis = ground_truth(shape=(6, 5, 4), seed=51)
        axes = {"slice_wise_tra": 2, "slice_wise_cor": 1, "slice_wise_sag": 0}
        for mode, axis in axes.items():
            with self.subTest(mode=mode):
                n_slices = basis.shape[axis + 1]
                # Distinct signed currents for each slice, all comfortably in bounds.
                currents = np.array([
                    [0.07 * ((ch % 3) - 1) + 0.02 * idx * ((-1) ** ch)
                     for idx in range(n_slices)]
                    for ch in range(8)
                ])
                b0 = np.zeros(basis.shape[1:])
                for idx in range(n_slices):
                    sl = [slice(None)] * 3
                    sl[axis] = idx
                    sl = tuple(sl)
                    b0[sl] = -np.tensordot(currents[:, idx], basis[(slice(None),) + sl], axes=(0, 0))
                shimmed, fit = OPTIMIZE(b0, basis, optimizer_param(mode))
                np.testing.assert_allclose(fit, currents, atol=1e-4)
                np.testing.assert_allclose(shimmed, 0, atol=1e-4)

    def test_05_optimizer_current_limits(self):
        basis = ground_truth(seed=83)
        impossible = np.array([3.5, -2.8, 0.1, -0.2, 0.3, 0.2, 0.1, -0.1])
        b0 = -np.tensordot(impossible, basis, axes=(0, 0))
        _, fit = OPTIMIZE(b0, basis, optimizer_param("global"))
        self.assertTrue(np.all(fit <= 2.4 + 1e-8))
        self.assertTrue(np.all(fit >= -2.0 - 1e-8))
        self.assertGreater(np.linalg.norm(fit - impossible), 0.1)

    def test_06_csv_A_to_mA_and_signed_dac(self):
        """Run real CSV loader without importing any serial/hardware dependencies."""
        source = (CODE / "CoilSliceControl.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "load_shimming_data")
        cfg = json.loads(json.dumps(CONFIG))
        cfg["shim"]["mode"] = "global"
        cfg["control"]["csv_current_unit"] = "A"
        env = {"get_setting": lambda: cfg, "csv": csv, "math": math}
        exec(compile_statements([fn], "real_CSV_loader"), env)
        loader = env["load_shimming_data"]
        desired_a = np.array([0.25, -0.5, 0.0, 1.1, -1.2, 0.001, -0.001, 0.75])
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "currents.csv"
            pd.DataFrame(desired_a).to_csv(path, index=False)
            currents = loader(str(path))
        self.assertEqual(currents, [[250, -500, 0, 1100, -1200, 1, -1, 750]])
        for channel, current_ma in enumerate(currents[0], start=1):
            cmd = make_dac_cmd(channel, current_ma)
            self.assertEqual(len(cmd), 11)
            self.assertEqual(cmd[:4], b"BDAC")
            self.assertEqual(cmd[4:5], str(channel).encode("ascii"))
            self.assertEqual(cmd[-2:], b"OV")
            decoded = int.from_bytes(cmd[5:9], "little", signed=True)
            self.assertEqual(decoded, current_ma)

    def test_07_dac_overflow_rejected(self):
        """Uses actual current limits from Global_parameter.json."""
        if CONFIG["current"].get("on_overflow", "raise") != "raise":
            self.skipTest("This regression test requires on_overflow=raise")
        limit_pos = round(float(CONFIG["current"]["limits"]["positive_A"]) * 1000)
        limit_neg = round(float(CONFIG["current"]["limits"]["negative_A"]) * 1000)
        with self.assertRaises(ValueError):
            make_dac_cmd(1, limit_pos + 1)
        with self.assertRaises(ValueError):
            make_dac_cmd(1, -limit_neg - 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
