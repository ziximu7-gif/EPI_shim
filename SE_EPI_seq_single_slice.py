#Mu# download from https://github.com/imr-framework/pypulseq/blob/6be8dc91/examples/scripts/write_epi_se.py
import math
import matplotlib.pyplot as plt
import numpy as np

import pypulseq as pp
import os

def main(polarity: bool=1, plot: bool = True, write_seq: bool = False, seq_filename: str = 'epi_se_pypulseq.seq'):
    # ======
    # SETUP
    # ======
    fov = 256e-3  # Define FOV and resolution
    Nx = 32
    Ny = 32

    # Set system limits
    system = pp.Opts(
        max_grad=32,
        grad_unit='mT/m',
        max_slew=130,
        slew_unit='T/m/s',
        rf_ringdown_time=20e-6,
        rf_dead_time=100e-6,
        adc_dead_time=20e-6,
    )

    seq = pp.Sequence(system)  # Create a new sequence object

    # ======
    # CREATE EVENTS
    # ======
    # Create 90 degree slice selection pulse and gradient
    rf, gz, _ = pp.make_sinc_pulse(
        flip_angle=np.pi / 2,
        system=system,
        duration=3e-3,
        slice_thickness=6e-3,
        apodization=0.5,
        time_bw_product=4,
        return_gz=True,
        delay=system.rf_dead_time,
    )

    # Define other gradients and ADC events
    delta_k = 1 / fov
    k_width = Nx * delta_k
    readout_time = 3.2e-4
    gx = pp.make_trapezoid(channel='x', system=system, flat_area=k_width, flat_time=readout_time)
    adc = pp.make_adc(num_samples=Nx, system=system, duration=gx.flat_time, delay=gx.rise_time)

    # Pre-phasing gradients
    pre_time = 8e-4
    gz_reph = pp.make_trapezoid(channel='z', system=system, area=-gz.area / 2, duration=pre_time)
    # Do not need minus for in-plane prephasers because of the spin-echo (position reflection in k-space)
    gx_pre = pp.make_trapezoid(channel='x', system=system, area=gx.area / 2 - delta_k / 2, duration=pre_time)
    gy_pre = pp.make_trapezoid(channel='y', system=system, area=polarity * Ny / 2 * delta_k, duration=pre_time)

    # Phase blip in shortest possible time
    dur = math.ceil(2 * math.sqrt(delta_k / system.max_slew) / 10e-6) * 10e-6
    gy = pp.make_trapezoid(channel='y', system=system, area=polarity * delta_k, duration=dur)

    # Refocusing pulse with spoiling gradients
    rf180 = pp.make_block_pulse(
        flip_angle=np.pi,
        delay=system.rf_dead_time,
        system=system,
        duration=500e-6,
        use='refocusing',
    )

    gz_spoil = pp.make_trapezoid(channel='z', system=system, area=gz.area * 2, duration=3 * pre_time)

    # Calculate delay time
    TE = 60e-3
    duration_to_center = (Nx / 2 + 0.5) * pp.calc_duration(gx) + Ny / 2 * pp.calc_duration(gy)
    rf_center_incl_delay = rf.delay + pp.calc_rf_center(rf)[0]
    rf180_center_incl_delay = rf180.delay + pp.calc_rf_center(rf180)[0]
    delay_TE1 = (
        TE / 2
        - pp.calc_duration(gz)
        + rf_center_incl_delay
        - pre_time
        - pp.calc_duration(gz_spoil)
        - rf180_center_incl_delay
    )
    delay_TE2 = (
        TE / 2 - pp.calc_duration(rf180) + rf180_center_incl_delay - pp.calc_duration(gz_spoil) - duration_to_center
    )
    #Mu# ========
    # ======
    # CONSTRUCT SEQUENCE
    # ======
    # Define sequence blocks
    #seq.add_block(rf, gz)
    #seq.add_block(gx_pre, gy_pre, gz_reph)
    #seq.add_block(pp.make_delay(delay_TE1))
    #seq.add_block(gz_spoil)
    #seq.add_block(rf180)
    #seq.add_block(gz_spoil)
    #seq.add_block(pp.make_delay(delay_TE2))
    #for _ in range(Ny):
    #    seq.add_block(gx, adc)  # Read one line of k-space
    #    seq.add_block(gy)  # Phase blip
    #    gx.amplitude = -gx.amplitude  # Reverse polarity of read gradient
    #seq.add_block(pp.make_delay(1e-4))
    # ======
    #Mu#==========

    #Mu#========== change to this block
    # CONSTRUCT SEQUENCE
    # ======
    num_coils = 8  # 匀场线圈通道数
    num_reps = num_coils + 1  # 多扫一个 Repetition 作为基准(Baseline)
    trig = pp.make_digital_output_pulse(channel='ext1', duration=100e-6) #TTL 10ms

    for r in range(num_reps):
        # 每一个 Repetition 开始前，先触发 Trigger 切换线圈
        seq.add_block(trig)
        seq.add_block(pp.make_delay(10e-3))

        seq.add_block(rf, gz)
        seq.add_block(gx_pre, gy_pre, gz_reph)
        seq.add_block(pp.make_delay(delay_TE1))
        seq.add_block(gz_spoil)
        seq.add_block(rf180)
        seq.add_block(gz_spoil)
        seq.add_block(pp.make_delay(delay_TE2))
    
        gx_current_amplitude = gx.amplitude 
        for _ in range(Ny):
            seq.add_block(gx, adc)
            seq.add_block(gy)
            gx.amplitude = -gx.amplitude  # 翻转极性用于下一行
        gx.amplitude = gx_current_amplitude # 恢复原始极性，准备下一个 Repetition
    
        # TR
        seq.add_block(pp.make_delay(1e-4))
    #Mu#=======

    ok, error_report = seq.check_timing()
    if ok:
        print('Timing check passed successfully')
    else:
        print('Timing check failed! Error listing follows:')
        print(error_report)

    # ======
    # VISUALIZATION
    # ======
    if plot:
        seq.plot(plot_now=False)

    # =========
    # WRITE .SEQ
    # =========
    if write_seq:
        seq.write(seq_filename)

    return seq


if __name__ == '__main__':
    os.chdir(r'c:/MuzixiFiles/MRI/ECC_Project/EPI_shim')
    main(polarity=1, seq_filename='epi_se_AP.seq', plot=True, write_seq=True)
    main(polarity=-1, seq_filename='epi_se_PA.seq', plot=True, write_seq=True)
    plt.show()
