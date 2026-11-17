import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
import suite_common.bootstrap  # noqa: F401
from suite_common.bench import bench_default
from suite_common.inputs import format_number_list, parse_number_list
from suite_common.output_folders import create_unique_test_output_folder

from suite_instruments.factory import InstrumentFactory
import csv
import os
from time import sleep, time
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ==============================
# COMMS SETUP
# ==============================
genIP = bench_default("generator")
lynxPort = bench_default("lynx")

# Instrument objects are created only after the GUI is accepted.
lynx = None
Egen = None
lynxSerial = None
unitSerial = None

# ==============================
# POWER SWEEP CONFIG
# ==============================
POWER_SWEEP_W = list(range(50, 3001, 50))  # 500 → 2500 W
POWER_SETTLE_S = 0.4
HALO_MODE = 10

# Raw DC-section ADC scaling retained from the original measurement equations.
DC_VOLTAGE_FULL_SCALE_V = 201.4
DC_CURRENT_FULL_SCALE_A = 40.8
ADC_FULL_SCALE_COUNTS = 4096.0

# ==============================
# STABILITY CONFIG
# ==============================
stabiliseTolPercent = 0.1
stabiliseTimeoutS = 180
stableSampleTimeS = 0.25
minTimeWaitS = 2
stabiliseTol = stabiliseTolPercent / 100.0

# ==============================
# FREQUENCY CONFIG
# ==============================
freqsMHz = [12882, 13560, 14238]
NUMBER_OF_CYCLES = 1

# ==============================
# OUTPUT FILE SETUP
# ==============================
STANDALONE_DEFAULT_BASE_PATH = str(Path.home())
basePath = (
    os.environ.get('PARAMOUNT_COLLECTION_PARENT_FOLDER', STANDALONE_DEFAULT_BASE_PATH).strip()
    or STANDALONE_DEFAULT_BASE_PATH
)
TEST_FOLDER_NAME = 'DC Operating Point'
outPath = None
outFname = None
fh = None
writer = None

# ==========================================================
# GRAPHICAL DC OPERATING-POINT CONFIGURATION
# ==========================================================
def validate_gui_global_definitions():
    required_names = (
        "genIP", "lynxPort", "POWER_SWEEP_W", "POWER_SETTLE_S",
        "HALO_MODE", "stabiliseTolPercent", "stabiliseTimeoutS",
        "stableSampleTimeS", "minTimeWaitS", "freqsMHz", "NUMBER_OF_CYCLES", "basePath",
        "TEST_FOLDER_NAME",
    )
    missing = [name for name in required_names if name not in globals()]
    if missing:
        raise RuntimeError(
            "Required DC operating-point GUI settings are not defined: "
            + ", ".join(missing)
        )


def configure_dc_operating_point_with_gui():
    """Configure this run while preserving the original sweep and measurement engine."""
    global genIP, lynxPort, POWER_SWEEP_W, POWER_SETTLE_S, HALO_MODE
    global stabiliseTolPercent, stabiliseTimeoutS, stableSampleTimeS
    global minTimeWaitS, stabiliseTol, freqsMHz, NUMBER_OF_CYCLES, basePath
    global TEST_FOLDER_NAME

    root = tk.Tk()
    root.title("Paramount DC Operating Point Setup")
    root.geometry("930x650")
    root.minsize(810, 580)

    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except tk.TclError:
        pass
    style.configure("Header.TLabel", font=("Segoe UI", 15, "bold"))
    style.configure("Section.TLabel", font=("Segoe UI", 10, "bold"))
    style.configure("Start.TButton", font=("Segoe UI", 11, "bold"), padding=(14, 8))

    outer = ttk.Frame(root, padding=14)
    outer.pack(fill="both", expand=True)
    ttk.Label(
        outer,
        text="Paramount DC Operating Point",
        style="Header.TLabel",
    ).pack(anchor="w")
    ttk.Label(
        outer,
        text=(
            "Configure the DUT, reference meter, frequency/power sweep, delivered-power "
            "stability criteria, and output location for this run."
        ),
        wraplength=870,
    ).pack(anchor="w", pady=(2, 10))

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True)
    instruments_tab = ttk.Frame(notebook, padding=12)
    sweep_tab = ttk.Frame(notebook, padding=12)
    stability_tab = ttk.Frame(notebook, padding=12)
    output_tab = ttk.Frame(notebook, padding=12)
    notebook.add(instruments_tab, text="Instruments")
    notebook.add(sweep_tab, text="Sweep")
    notebook.add(stability_tab, text="Stability")
    notebook.add(output_tab, text="Output")

    variables = {}

    def add_entry(parent, row, label, key, default, help_text="", width=38):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=(0, 10), pady=5
        )
        variable = tk.StringVar(value=str(default))
        variables[key] = variable
        ttk.Entry(parent, textvariable=variable, width=width).grid(
            row=row, column=1, sticky="ew", pady=5
        )
        if help_text:
            ttk.Label(
                parent, text=help_text, foreground="#555555", wraplength=350
            ).grid(row=row, column=2, sticky="w", padx=(10, 0), pady=5)

    add_entry(instruments_tab, 0, "DUT IP / hostname", "gen_ip", genIP, width=50)
    add_entry(
        instruments_tab, 1, "Lynx hostname / port", "lynx_port", lynxPort,
        "Used for delivered RF power and meter serial-number readback.", width=50
    )
    ttk.Label(
        instruments_tab,
        text=(
            "The GUI completes before either instrument is opened. The DUT is forced "
            "RF Off immediately after connection, matching the original safety behavior."
        ),
        foreground="#555555",
        wraplength=760,
    ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(14, 0))

    add_entry(
        sweep_tab, 0, "RF frequencies (kHz)", "frequencies",
        format_number_list(freqsMHz), "Comma-separated whole numbers."
    )
    add_entry(
        sweep_tab, 1, "Power sweep setpoints (W)", "power_sweep",
        format_number_list(POWER_SWEEP_W), "Comma-separated whole setpoints."
    )
    add_entry(sweep_tab, 2, "Power-setpoint settle time (s)", "power_settle", POWER_SETTLE_S)
    add_entry(sweep_tab, 3, "HALO mode multiplier", "halo_mode", HALO_MODE)
    add_entry(
        sweep_tab, 4, "Number of collection cycles", "cycle_count", NUMBER_OF_CYCLES,
        "Runs the complete frequency and power sweep this many times in the same CSV."
    )
    planned = tk.StringVar()
    ttk.Label(sweep_tab, textvariable=planned, style="Section.TLabel").grid(
        row=5, column=0, columnspan=3, sticky="w", pady=(14, 0)
    )

    add_entry(
        stability_tab, 0, "Tolerance (%)", "tol_percent", stabiliseTolPercent,
        "Relative delivered-power tolerance."
    )
    add_entry(stability_tab, 1, "Timeout (s)", "timeout", stabiliseTimeoutS)
    add_entry(
        stability_tab, 2, "Sample interval (s)", "sample_interval", stableSampleTimeS
    )
    add_entry(
        stability_tab, 3, "Minimum wait before stable exit (s)", "minimum_wait", minTimeWaitS
    )
    ttk.Label(
        stability_tab,
        text=(
            "The existing stabilization test remains unchanged: equality or relative "
            "change within tolerance, subject to timeout and minimum-wait rules."
        ),
        foreground="#555555",
        wraplength=760,
    ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(14, 0))

    output_parent = tk.StringVar(value=basePath)
    ttk.Label(output_tab, text="Parent output folder").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=5
    )
    ttk.Entry(output_tab, textvariable=output_parent, width=58).grid(
        row=0, column=1, sticky="ew", pady=5
    )
    output_tab.columnconfigure(1, weight=1)

    def browse_output_parent():
        initial = output_parent.get().strip()
        if not os.path.isdir(initial):
            initial = str(Path.home())
        selected = filedialog.askdirectory(
            title="Select parent folder for DC Operating Point data",
            initialdir=initial,
            parent=root,
            mustexist=True,
        )
        if selected:
            output_parent.set(selected)

    ttk.Button(output_tab, text="Browse...", command=browse_output_parent).grid(
        row=0, column=2, padx=(10, 0), pady=5
    )
    ttk.Label(
        output_tab,
        text=(
            "The script will create a run folder inside the selected parent using: "
            "YYYY-MM-DD_HH-MM-SS - DC Operating Point - Generator Name. If an exact "
            "same-second folder already exists, Run 2, Run 3, and so on are used."
        ),
        foreground="#555555",
        wraplength=760,
    ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 0))

    result = {"accepted": False}

    def float_value(key, label, minimum=None):
        try:
            value = float(variables[key].get().strip())
        except ValueError as error:
            raise ValueError(f"{label} must be numeric.") from error
        if not value == value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{label} must be finite.")
        if minimum is not None and value < minimum:
            raise ValueError(f"{label} must be at least {minimum}.")
        return value

    def update_preview(*_):
        try:
            frequency_count = len(parse_number_list(
                variables["frequencies"].get(), "RF frequencies"
            ))
            setpoint_count = len(parse_number_list(
                variables["power_sweep"].get(), "Power sweep"
            ))
            cycle_count = int(variables["cycle_count"].get().strip())
            if cycle_count < 1:
                raise ValueError("Number of collection cycles must be positive.")
            planned.set(
                f"Planned measurement rows: {frequency_count * setpoint_count * cycle_count:,}"
            )
        except Exception:
            planned.set("Planned measurement rows: check entered values")

    def apply_and_start():
        nonlocal result
        global genIP, lynxPort, POWER_SWEEP_W, POWER_SETTLE_S, HALO_MODE
        global stabiliseTolPercent, stabiliseTimeoutS, stableSampleTimeS
        global minTimeWaitS, stabiliseTol, freqsMHz, NUMBER_OF_CYCLES, basePath
        global TEST_FOLDER_NAME
        try:
            selected_gen = variables["gen_ip"].get().strip()
            selected_lynx = variables["lynx_port"].get().strip()
            if not selected_gen or not selected_lynx:
                raise ValueError("DUT and Lynx addresses cannot be blank.")
            selected_frequencies = parse_number_list(
                variables["frequencies"].get(), "RF frequencies", integer_only=True
            )
            selected_sweep = parse_number_list(
                variables["power_sweep"].get(), "Power sweep", integer_only=True
            )
            if any(value <= 0 for value in selected_frequencies):
                raise ValueError("RF frequencies must be greater than zero.")
            if any(value < 0 for value in selected_sweep):
                raise ValueError("Power setpoints cannot be negative.")
            selected_parent = output_parent.get().strip()
            if not selected_parent:
                raise ValueError("Select a parent output folder.")
            parent_directory = Path(selected_parent).resolve()
            if not parent_directory.exists() or not parent_directory.is_dir():
                raise ValueError("The selected parent output folder does not exist.")

            genIP = selected_gen
            lynxPort = selected_lynx
            freqsMHz = selected_frequencies
            POWER_SWEEP_W = selected_sweep
            try:
                selected_cycle_count = int(variables["cycle_count"].get().strip())
            except ValueError as error:
                raise ValueError("Number of collection cycles must be a whole number.") from error
            if selected_cycle_count < 1:
                raise ValueError("Number of collection cycles must be at least 1.")
            NUMBER_OF_CYCLES = selected_cycle_count
            POWER_SETTLE_S = float_value("power_settle", "Power settle time", 0)
            selected_halo_mode = float_value("halo_mode", "HALO mode", 0.000001)
            if not selected_halo_mode.is_integer():
                raise ValueError("HALO mode multiplier must be a whole number.")
            HALO_MODE = int(selected_halo_mode)
            stabiliseTolPercent = float_value("tol_percent", "Tolerance", 0)
            stabiliseTimeoutS = float_value("timeout", "Timeout", 0.000001)
            stableSampleTimeS = float_value(
                "sample_interval", "Sample interval", 0.000001
            )
            minTimeWaitS = float_value("minimum_wait", "Minimum wait", 0)
            stabiliseTol = stabiliseTolPercent / 100.0
            basePath = str(parent_directory)

            result["accepted"] = True
            root.destroy()
        except Exception as error:
            messagebox.showerror(
                "Invalid DC operating-point settings", str(error), parent=root
            )

    for variable in variables.values():
        variable.trace_add("write", update_preview)
    update_preview()

    buttons = ttk.Frame(outer)
    buttons.pack(fill="x", pady=(12, 0))
    ttk.Button(buttons, text="Cancel", command=root.destroy).pack(
        side="right", padx=(8, 0)
    )
    ttk.Button(
        buttons,
        text="Start Test",
        style="Start.TButton",
        command=apply_and_start,
    ).pack(side="right")

    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    if not result["accepted"]:
        raise SystemExit("DC operating-point test cancelled before instrument connection.")


def initialize_instruments_and_output():
    """Perform the original module-level connection and CSV setup after the GUI."""
    global lynx, Egen, lynxSerial, unitSerial
    global outPath, outFname, fh, writer

    factory = InstrumentFactory({"lynx": lynxPort, "generator": genIP})
    lynx = factory.create("lynx")
    lynxSerial = lynx.reportSerialNumber()

    Egen = factory.create("generator")
    unitSerial = Egen.reportSerialNumber()
    Egen.rfOFF()

    outPath = str(
        create_unique_test_output_folder(
            basePath,
            TEST_FOLDER_NAME,
            genIP,
        )
    )
    print(f"Test data folder: {outPath}")

    outFname = os.path.join(
        outPath,
        f"{freqsMHz[0]}_{unitSerial}_{lynxSerial}_"
        f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.csv"
    )
    fh = open(outFname, "wt")
    writer = csv.writer(fh, lineterminator="\n")
    writer.writerow([
        # Legacy columns retained first for compatibility.
        "freq_Hz",
        "power_setpoint_W",
        "DCVoltage_V",
        "DCCurrent_A",
        "pDelivered_W",
        "pDissipation_W",
        "stabilize_time_s",
        # Run and condition metadata.
        "measurement_timestamp",
        "generator_hostname",
        "generator_serial",
        "lynx_hostname",
        "lynx_serial",
        "halo_mode_multiplier",
        "freq_kHz",
        "generator_command_setpoint",
        # Raw ADC data and explicit scaling metadata.
        "dc_voltage_adc_counts",
        "dc_current_adc_counts",
        "dc_adc_response",
        "dc_voltage_full_scale_V",
        "dc_current_full_scale_A",
        "adc_full_scale_counts",
        "dc_input_power_W",
        # Complete Lynx readUnit222 measurement record.
        "lynx_forward_power_W",
        "lynx_reflected_power_W",
        "lynx_delivered_power_W",
        "lynx_resistance_ohm",
        "lynx_reactance_ohm",
        "lynx_voltage_V",
        "lynx_current_A",
        "lynx_response",
        # Derived cross-checks for later standalone analysis.
        "calculated_power_loss_W",
        "calculated_efficiency_percent",
        "dissipation_difference_W",
        # Stabilization diagnostics.
        "stabilization_timed_out",
        "stabilization_sample_count",
        "previous_stabilization_power_W",
        "final_stabilization_power_W",
        "final_relative_change_percent",
        "stabilization_tolerance_percent",
        "stabilization_timeout_s",
        "stabilization_minimum_wait_s",
        "measurement_valid",
        "measurement_status",
    ])
    with open(os.path.join(outPath, "DC_Operating_Point_Run_Settings.csv"), "w", newline="", encoding="utf-8") as settings_handle:
        settings_writer = csv.writer(settings_handle)
        settings_writer.writerow(["setting", "value"])
        settings_writer.writerows([
            ["number_of_collection_cycles", NUMBER_OF_CYCLES],
            ["rf_frequencies_kHz", repr(freqsMHz)],
            ["power_sweep_setpoints_W", repr(POWER_SWEEP_W)],
            ["power_settle_s", POWER_SETTLE_S],
            ["halo_mode_multiplier", HALO_MODE],
        ])


# ==============================
# HELPERS
# ==============================
def readPowerMeter():
    """Delivered RF power from Lynx (W)"""
    return lynx.readUnit222()[2]


def read_lynx_measurement_record():
    """Return one complete Lynx record padded to the known readUnit222 schema."""
    response = list(lynx.readUnit222())
    padded = response + [float("nan")] * max(0, 7 - len(response))
    return response, padded[:7]

validate_gui_global_definitions()
configure_dc_operating_point_with_gui()
initialize_instruments_and_output()

# ==============================
# START TEST
# ==============================
print("Press ENTER to begin testing")
sys.stdin.readline()

startTime = time()

try:
    for cycle_number in range(1, NUMBER_OF_CYCLES + 1):
        print(f'Starting DC Operating Point collection cycle {cycle_number} of {NUMBER_OF_CYCLES}')
        for freqMHz in freqsMHz:

            # Configure instruments
            Egen.setFixedFrequency(int(freqMHz))
            lynx.setFreq(freqMHz)

            Egen.setActiveControlMode(2)
            Egen.setRegulationMode(6)
            Egen.setFrequencyMode(0)
            Egen.setPulsingFrequency(0)

            Egen.setPowerSetpoint(0)
            Egen.rfON()

            # ==============================
            # POWER SETPOINT SWEEP
            # ==============================
            for setpoint in POWER_SWEEP_W:
                print(f"Setting power to {setpoint} W")
                generator_command_setpoint = int(setpoint * HALO_MODE)
                Egen.setPowerSetpoint(generator_command_setpoint)
                sleep(POWER_SETTLE_S)

                tic = time()
                stable = False
                stabilization_timed_out = False
                stabilization_sample_count = 0
                previous_stabilization_power = float("nan")
                final_stabilization_power = float("nan")
                final_relative_change_percent = float("nan")
                prevPdel = 1e6
                sampleTime = 0

                # ---- Stabilize Delivered Power ----
                while True:
                    if (time() - sampleTime) > stableSampleTimeS:
                        pDel = readPowerMeter()
                        sampleTime = time()
                        stabilization_sample_count += 1
                        previous_stabilization_power = prevPdel
                        final_stabilization_power = pDel
                        if pDel != 0:
                            final_relative_change_percent = (
                                abs(pDel - prevPdel) / abs(pDel) * 100.0
                            )

                        if pDel == prevPdel or (
                            pDel != 0 and abs(pDel - prevPdel) / pDel <= stabiliseTol
                        ):
                            stable = True

                        prevPdel = pDel

                    if time() - tic > stabiliseTimeoutS:
                        stabilization_timed_out = True
                        print("WARNING: Stabilization timeout")
                        break

                    if stable and (time() - tic) > minTimeWaitS:
                        break

                stabilize_time = time() - tic

                # ---- Measurements ----
                measurement_timestamp = datetime.now().isoformat(timespec="milliseconds")
                # generator_command_setpoint was converted to int before transmission.

                DCinfo = Egen.reportSPIBusADCData(4)  # pull DC Section ADC data
                dc_voltage_adc_counts = DCinfo[0]
                dc_current_adc_counts = DCinfo[2]
                DCCurrent = (
                    dc_current_adc_counts * DC_CURRENT_FULL_SCALE_A / ADC_FULL_SCALE_COUNTS
                )
                DCVoltage = (
                    dc_voltage_adc_counts * DC_VOLTAGE_FULL_SCALE_V / ADC_FULL_SCALE_COUNTS
                )
                dc_input_power = DCVoltage * DCCurrent

                lynx_response, lynx_values = read_lynx_measurement_record()
                (
                    lynx_forward_power,
                    lynx_reflected_power,
                    pDelivered,
                    lynx_resistance,
                    lynx_reactance,
                    lynx_voltage,
                    lynx_current,
                ) = lynx_values

                pDissipation = Egen.reportPowerDisspiation()
                calculated_power_loss = dc_input_power - pDelivered
                calculated_efficiency_percent = (
                    pDelivered / dc_input_power * 100.0
                    if dc_input_power not in (0, 0.0)
                    else float("nan")
                )
                dissipation_difference = pDissipation - calculated_power_loss
                measurement_status = (
                    "Stabilization Timeout" if stabilization_timed_out else "Stable"
                )

                # ---- Log analysis-ready CSV row ----
                writer.writerow([
                    # Legacy columns.
                    freqMHz * 1e6,
                    setpoint,
                    DCVoltage,
                    DCCurrent,
                    pDelivered,
                    pDissipation,
                    stabilize_time,
                    # Run and condition metadata.
                    measurement_timestamp,
                    genIP,
                    unitSerial,
                    lynxPort,
                    lynxSerial,
                    HALO_MODE,
                    freqMHz,
                    generator_command_setpoint,
                    # Raw DC data and scaling.
                    dc_voltage_adc_counts,
                    dc_current_adc_counts,
                    repr(DCinfo),
                    DC_VOLTAGE_FULL_SCALE_V,
                    DC_CURRENT_FULL_SCALE_A,
                    ADC_FULL_SCALE_COUNTS,
                    dc_input_power,
                    # Complete Lynx record.
                    lynx_forward_power,
                    lynx_reflected_power,
                    pDelivered,
                    lynx_resistance,
                    lynx_reactance,
                    lynx_voltage,
                    lynx_current,
                    repr(lynx_response),
                    # Derived cross-checks.
                    calculated_power_loss,
                    calculated_efficiency_percent,
                    dissipation_difference,
                    # Stabilization diagnostics.
                    stabilization_timed_out,
                    stabilization_sample_count,
                    previous_stabilization_power,
                    final_stabilization_power,
                    final_relative_change_percent,
                    stabiliseTolPercent,
                    stabiliseTimeoutS,
                    minTimeWaitS,
                    True,
                    measurement_status,
                ])
                fh.flush()
                os.fsync(fh)

                print(
                    f"Pset={setpoint:4d} W | "
                    f"Vdc={DCVoltage:6.2f} V | "
                    f"Idc={DCCurrent:6.2f} A | "
                    f"P_diss={pDissipation:7.1f} W | "
                    f"Eff={calculated_efficiency_percent:6.2f}% | "
                    f"Status={measurement_status}"
                )

            Egen.rfOFF()

        print(f'Completed DC Operating Point collection cycle {cycle_number} of {NUMBER_OF_CYCLES}')
finally:
    print("Shutting down safely...")
    Egen.rfOFF()
    Egen.setPowerSetpoint(0)
    fh.flush()
    os.fsync(fh)
    fh.close()
    elapsed_min = (time() - startTime) / 60
    print(f"DONE — total time {elapsed_min:.1f} min")
