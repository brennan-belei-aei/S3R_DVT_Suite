"""
PMNT_Rise_Fall_Time_Collection_GUI.py

Collects FastDAQ waveforms for:

1. Rise Time
   2000W -> Power Limit

2. Fall Time
   Power Limit -> 2000W

Outputs:
    Rise_Test.csv
    Fall_Test.csv
    Rise_Fall_Run_Settings.csv
"""

import csv
import datetime
import os
import time
from pathlib import Path

import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import AECommandsUniversal


# ==========================================================
# DEFAULT SETTINGS
# ==========================================================

GENERATOR_IP = "PMNT0451C5"

INITIAL_POWER = 2000

STANDARD_DELAY = 2.0
SETPOINT_SETTLE_TIME = 0.5
POST_TRIGGER_DELAY = 0.050

HALO_MULTIPLIER = 10

FIXED_FREQUENCY_MODE = 0
GENERATOR_FREQUENCY = 13560
REGULATION_MODE = 7

SAMPLE_PERIOD = 5015e-9

DEFAULT_OUTPUT_PATH = str(Path.home())


# ==========================================================
# FASTDAQ COMMANDS
# ==========================================================

FASTDAQ_SAMPLE_INTERVAL_CMD = [
    118,
    '8:1',
    '1:1',
    55,
    0,
    4,
    0,
    64,
    13,
    3,
    0
]

FASTDAQ_START_CMD = [
    118,
    '4:1',
    '1:1',
    56,
    0,
    1,
    0
]

FASTDAQ_STOP_CMD = [
    118,
    '4:1',
    '1:1',
    56,
    0,
    4,
    0
]


def build_fastdaq_read_cmd(block_number):

    return [
        248,
        '6:1',
        '1:680',
        57,
        0,
        1,
        0,
        block_number,
        0
    ]


# ==========================================================
# GUI
# ==========================================================

def get_user_settings():

    root = tk.Tk()
    root.title("Rise / Fall Time Collection Setup")
    root.geometry("700x350")

    result = {"accepted": False}

    gen_ip = tk.StringVar(value=GENERATOR_IP)
    start_power = tk.StringVar(value=str(INITIAL_POWER))
    output_path = tk.StringVar(value=DEFAULT_OUTPUT_PATH)

    frame = ttk.Frame(root, padding=15)
    frame.pack(fill="both", expand=True)

    ttk.Label(
        frame,
        text="Rise / Fall Time Collection",
        font=("Segoe UI", 14, "bold")
    ).grid(row=0, column=0, columnspan=3, sticky="w")

    ttk.Label(frame, text="Generator IP").grid(
        row=1,
        column=0,
        sticky="w",
        pady=10
    )

    ttk.Entry(
        frame,
        textvariable=gen_ip,
        width=40
    ).grid(row=1, column=1, sticky="ew")

    ttk.Label(frame, text="Low Power Setpoint (W)").grid(
        row=2,
        column=0,
        sticky="w",
        pady=10
    )

    ttk.Entry(
        frame,
        textvariable=start_power,
        width=20
    ).grid(row=2, column=1, sticky="w")

    ttk.Label(frame, text="Output Folder").grid(
        row=3,
        column=0,
        sticky="w",
        pady=10
    )

    ttk.Entry(
        frame,
        textvariable=output_path,
        width=50
    ).grid(row=3, column=1, sticky="ew")

    def browse():

        folder = filedialog.askdirectory(
            parent=root,
            mustexist=True
        )

        if folder:
            output_path.set(folder)

    ttk.Button(
        frame,
        text="Browse",
        command=browse
    ).grid(row=3, column=2, padx=10)

    def start():

        try:

            result["generator_ip"] = (
                gen_ip.get().strip()
            )

            result["initial_power"] = int(
                start_power.get().strip()
            )

            result["output_folder"] = (
                output_path.get().strip()
            )

            result["accepted"] = True

            root.destroy()

        except Exception as e:

            messagebox.showerror(
                "Invalid Settings",
                str(e)
            )

    button_frame = ttk.Frame(frame)
    button_frame.grid(
        row=4,
        column=0,
        columnspan=3,
        pady=25
    )

    ttk.Button(
        button_frame,
        text="Cancel",
        command=root.destroy
    ).pack(side="right", padx=5)

    ttk.Button(
        button_frame,
        text="Start Collection",
        command=start
    ).pack(side="right")

    root.mainloop()

    if not result["accepted"]:
        raise SystemExit()

    return result


# ==========================================================
# OUTPUT FOLDER
# ==========================================================

def create_run_folder(base_folder, generator_name):

    timestamp = datetime.datetime.now().strftime(
        "%Y-%m-%d_%H-%M-%S"
    )

    folder_name = (
        f"{timestamp}_Rise_Fall_Time_{generator_name}"
    )

    folder_path = os.path.join(
        base_folder,
        folder_name
    )

    os.makedirs(folder_path)

    return folder_path


# ==========================================================
# FASTDAQ CAPTURE
# ==========================================================

def read_fastdaq_capture(Egen):

    samples = []

    for block in range(24):

        response = Egen.genericCMD(
            build_fastdaq_read_cmd(block)
        )

        samples.extend(response[2])

    power = np.array(
        samples,
        dtype=float
    ) / HALO_MULTIPLIER

    time_axis = (
        np.arange(len(power))
        * SAMPLE_PERIOD
    )

    return time_axis, power


def save_waveform_csv(
        filename,
        time_axis,
        power):

    with open(
        filename,
        "w",
        newline=""
    ) as fh:

        writer = csv.writer(fh)

        writer.writerow([
            "SampleIndex",
            "TimeSec",
            "DeliveredPower"
        ])

        for i in range(len(power)):

            writer.writerow([
                i,
                time_axis[i],
                power[i]
            ])


# ==========================================================
# TRANSITION CAPTURE
# ==========================================================

def capture_transition(
        Egen,
        start_power,
        end_power,
        split_cw=False):

    print(
        f"\nCapturing transition:"
        f" {start_power}W -> {end_power}W"
    )

    Egen.DLP_setpoint(
        start_power * HALO_MULTIPLIER,
        start_power * HALO_MULTIPLIER
    )

    if split_cw:
        Egen.setSplitCWEnable()

    time.sleep(
        SETPOINT_SETTLE_TIME
    )

    Egen.genericCMD(
        FASTDAQ_SAMPLE_INTERVAL_CMD
    )

    Egen.genericCMD(
        FASTDAQ_START_CMD
    )

    time.sleep(0.050)

    Egen.DLP_setpoint(
        end_power * HALO_MULTIPLIER,
        end_power * HALO_MULTIPLIER
    )

    time.sleep(
        POST_TRIGGER_DELAY
    )

    time_axis, power = (
        read_fastdaq_capture(Egen)
    )

    Egen.genericCMD(
        FASTDAQ_STOP_CMD
    )

    return time_axis, power


# ==========================================================
# SETTINGS FILE
# ==========================================================

def save_settings_file(
        output_folder,
        serial_number,
        power_limit,
        initial_power,
        generator_ip):

    settings_path = os.path.join(
        output_folder,
        "Rise_Fall_Run_Settings.csv"
    )

    with open(
        settings_path,
        "w",
        newline=""
    ) as fh:

        writer = csv.writer(fh)

        writer.writerow([
            "setting",
            "value"
        ])

        writer.writerow([
            "generator_ip",
            generator_ip
        ])

        writer.writerow([
            "serial_number",
            serial_number
        ])

        writer.writerow([
            "power_limit",
            power_limit
        ])

        writer.writerow([
            "initial_power",
            initial_power
        ])

        writer.writerow([
            "sample_period",
            SAMPLE_PERIOD
        ])

        writer.writerow([
            "collection_timestamp",
            datetime.datetime.now().isoformat()
        ])


# ==========================================================
# MAIN
# ==========================================================

def main(split_cw=False):
    """Run the Quasi-CW collection; split_cw=True is the Split-CW variant."""

    config = get_user_settings()

    Egen = None

    try:

        Egen = AECommands_forFastDAQ.generator(
            config["generator_ip"]
        )

        time.sleep(
            STANDARD_DELAY
        )

        Egen.setFixedFrequencyMode(
            FIXED_FREQUENCY_MODE
        )

        Egen.setStartFreq(
            GENERATOR_FREQUENCY
        )

        Egen.setGenFreq(
            GENERATOR_FREQUENCY
        )

        Egen.setRegulationMode(
            REGULATION_MODE
        )

        serial_number = (
            Egen.serialNumber()
        )

        power_limit = int(
            Egen.reportUserPowerLimit()
        )

        output_folder = (
            create_run_folder(
                config["output_folder"],
                config["generator_ip"]
            )
        )

        save_settings_file(
            output_folder,
            serial_number,
            power_limit,
            config["initial_power"],
            config["generator_ip"]
        )

        print(
            f"Serial Number: "
            f"{serial_number}"
        )

        print(
            f"Power Limit: "
            f"{power_limit} W"
        )

        Egen.RF_ON()

        time.sleep(1.0)

        # RISE

        rise_time, rise_power = capture_transition(
            Egen,
            config["initial_power"],
            power_limit,
            split_cw
        )

        save_waveform_csv(
            os.path.join(
                output_folder,
                "Rise_Test.csv"
            ),
            rise_time,
            rise_power
        )

        time.sleep(1.0)

        # FALL

        fall_time, fall_power = capture_transition(
            Egen,
            power_limit,
            config["initial_power"],
            split_cw
        )

        save_waveform_csv(
            os.path.join(
                output_folder,
                "Fall_Test.csv"
            ),
            fall_time,
            fall_power
        )

        Egen.RF_OFF()

        if split_cw:
            Egen.setSplitCWDisable()

        print("\nCollection Complete")

        print(
            f"Data saved to:\n"
            f"{output_folder}"
        )

    finally:

        if Egen is not None:

            try:
                Egen.RF_OFF()
            except:
                pass

            try:
                Egen.close()
            except:
                pass


if __name__ == "__main__":
    main()