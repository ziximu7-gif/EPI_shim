#Mu# download from https://github.com/imr-framework/pypulseq/blob/6be8dc91/examples/scripts/write_epi_se.py change to whole brain
import math
import numpy as np
import pypulseq as pp
import os
import matplotlib.pyplot as plt


def main(
    polarity: int = 1,
    plot: bool = True,
    write_seq: bool = False,
    seq_filename: str = "epi_se.seq",
    # --- imaging ---
    fov: float = 256e-3,
    Nx: int = 32,
    Ny: int = 32,
    slice_thickness: float = 6e-3,
    slice_gap: float = 0.0e-3,
    num_slices: int = 20,
    TE: float = 60e-3,
    TR_vol: float = 2.0,  # 一个volume的TR
    # --- coil switching ---
    num_coils: int = 8,
    coil_settle_delay: float = 10e-3,
):
    # ======
    # SETUP
    # ======
    system = pp.Opts(
        max_grad=32,
        grad_unit="mT/m",
        max_slew=130,
        slew_unit="T/m/s",
        rf_ringdown_time=20e-6,
        rf_dead_time=100e-6,
        adc_dead_time=20e-6,
    )
    seq = pp.Sequence(system)

    # ======
    # RF + SLICE SELECT GRAD (90°)
    # ======
    rf90, gz90, _ = pp.make_sinc_pulse(
        flip_angle=np.pi / 2,
        system=system,
        duration=3e-3,
        slice_thickness=slice_thickness,
        apodization=0.5,
        time_bw_product=4,
        return_gz=True,
        delay=system.rf_dead_time,
        use="excitation",
    )

    # ======
    # RF + SLICE SELECT GRAD (180°)  << 改成 slice-selective 
    # ======
    rf180, gz180, _ = pp.make_sinc_pulse(
        flip_angle=np.pi,
        system=system,
        duration=3e-3,  
        slice_thickness=slice_thickness,
        apodization=0.5,
        time_bw_product=4,
        return_gz=True,
        delay=system.rf_dead_time,
        use="refocusing",
    )

    # ======
    # READOUT + ADC
    # ======
    delta_k = 1 / fov
    k_width = Nx * delta_k
    readout_time = 3.2e-4
    gx = pp.make_trapezoid(channel="x", system=system, flat_area=k_width, flat_time=readout_time)
    adc = pp.make_adc(num_samples=Nx, system=system, duration=gx.flat_time, delay=gx.rise_time)

    # Prephasers
    pre_time = 8e-4
    gz90_reph = pp.make_trapezoid(channel="z", system=system, area=-gz90.area / 2, duration=pre_time)
    gx_pre = pp.make_trapezoid(channel="x", system=system, area=gx.area / 2 - delta_k / 2, duration=pre_time)
    gy_pre = pp.make_trapezoid(channel="y", system=system, area=polarity * Ny / 2 * delta_k, duration=pre_time)

    # EPI phase blip
    dur = math.ceil(2 * math.sqrt(delta_k / system.max_slew) / 10e-6) * 10e-6
    gy_blip = pp.make_trapezoid(channel="y", system=system, area=polarity * delta_k, duration=dur)

    # Crushers / spoilers around 180 
    gz_spoil = pp.make_trapezoid(channel="z", system=system, area=gz90.area * 2, duration=3 * pre_time)

    # ======
    # TE timing (按 slice 的 SE 结构算)
    # ======
    duration_to_center = (Nx / 2 + 0.5) * pp.calc_duration(gx) + Ny / 2 * pp.calc_duration(gy_blip)

    rf90_center = rf90.delay + pp.calc_rf_center(rf90)[0]
    rf180_center = rf180.delay + pp.calc_rf_center(rf180)[0]

    # delay_TE1：90结束到180前的 delay（包含prephaser、spoiler等）
    delay_TE1 = (
        TE / 2
        - pp.calc_duration(gz90)  # 90 slice-select block时长
        + rf90_center
        - pre_time
        - pp.calc_duration(gz_spoil)
        - rf180_center
    )
    # delay_TE2：180后到readout开始前的 delay
    delay_TE2 = (
        TE / 2
        - pp.calc_duration(gz180)
        + rf180_center
        - pp.calc_duration(gz_spoil)
        - duration_to_center
    )

    if delay_TE1 < 0 or delay_TE2 < 0:
        raise ValueError(f"TE too short for current parameters: delay_TE1={delay_TE1}, delay_TE2={delay_TE2}")

    # ======
    # SLICE POSITIONS (centered, no gap by default)
    # ======
    slice_step = slice_thickness + slice_gap
    slice_positions = (np.arange(num_slices) - (num_slices - 1) / 2.0) * slice_step

    # γ/(2π) in Hz/T
    gamma_bar = 42.576e6  # Hz/T

    # ======
    # CONSTRUCT SEQUENCE: baseline + each coil
    # ======
    num_reps = num_coils + 1  # rep0 baseline(0), rep1.. = coils
    trig = pp.make_digital_output_pulse(channel="ext1", duration=100e-6)

    #set slice freq offsets (mT/m -> T/m)
    def set_slice_offset(z_m: float):
        # f = gamma * Gz(T/m) * z(m)
        gz_Tpm_90 = gz90.amplitude * 1e-3
        freq = gamma_bar * gz_Tpm_90 * z_m
        rf90.freq_offset = freq
        rf180.freq_offset = freq

    # estimate volume time to enforce TR_vol (rough but useful)
    line_time = pp.calc_duration(gx, adc) + pp.calc_duration(gy_blip)
    readout_block_time = Ny * line_time
    single_slice_time_est = (
        pp.calc_duration(rf90, gz90)
        + pp.calc_duration(gx_pre, gy_pre, gz90_reph)
        + delay_TE1
        + pp.calc_duration(gz_spoil)
        + pp.calc_duration(rf180, gz180)
        + pp.calc_duration(gz_spoil)
        + delay_TE2
        + readout_block_time
    )
    vol_time_est = pp.calc_duration(trig) + coil_settle_delay + num_slices * single_slice_time_est
    tr_pad = max(0.0, TR_vol - vol_time_est)

    for r in range(num_reps):
        seq.add_block(trig)
        seq.add_block(pp.make_delay(coil_settle_delay))

        for z in slice_positions:
            set_slice_offset(float(z))

            seq.add_block(rf90, gz90)
            seq.add_block(gx_pre, gy_pre, gz90_reph)
            seq.add_block(pp.make_delay(delay_TE1))
            seq.add_block(gz_spoil)
            seq.add_block(rf180, gz180)
            seq.add_block(gz_spoil)
            seq.add_block(pp.make_delay(delay_TE2))

            gx_current_amplitude = gx.amplitude
            for _ in range(Ny):
                seq.add_block(gx, adc)
                seq.add_block(gy_blip)
                gx.amplitude = -gx.amplitude
            gx.amplitude = gx_current_amplitude

        # volume TR padding
        if tr_pad > 0:
            seq.add_block(pp.make_delay(tr_pad))

    ok, error_report = seq.check_timing()
    if not ok:
        print("Timing check failed!")
        print(error_report)
    else:
        print("Timing check passed successfully")
        print(f"Estimated vol_time={vol_time_est:.3f}s, TR_vol={TR_vol:.3f}s, TR_pad={tr_pad:.3f}s")

    if plot:
        seq.plot(plot_now=False)

    if write_seq:
        seq.write(seq_filename)

    return seq


if __name__ == "__main__":
    os.chdir(r"c:/MuzixiFiles/MRI/ECC_Project/EPI_shim")

    # AP
    main(
        polarity=1,
        seq_filename="epi_se_AP_fullbrain.seq",
        plot=True,
        write_seq=True,
        num_slices=20,
        slice_thickness=6e-3,
        slice_gap=0.0e-3,
        TR_vol=2.0,
        num_coils=8,
    )

    # PA
    main(
        polarity=-1,
        seq_filename="epi_se_PA_fullbrain.seq",
        plot=True,
        write_seq=True,
        num_slices=20,
        slice_thickness=6e-3,
        slice_gap=0.0e-3,
        TR_vol=2.0,
        num_coils=8,
    )

    plt.show()