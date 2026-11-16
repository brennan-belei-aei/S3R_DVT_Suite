# import pandas as pd
# import numpy as np
# import matplotlib.pyplot as plt
# from scipy.signal import butter, filtfilt, hilbert
# from tkinter import Tk, filedialog
#
# # ==========================================
# # SELECT FILE
# # ==========================================
#
# root = Tk()
# root.withdraw()
#
# csv_path = filedialog.askopenfilename(
#     title="Select CSV file",
#     filetypes=[("CSV Files", "*.csv")]
# )
#
# if not csv_path:
#     raise SystemExit("No file selected.")
#
# # ==========================================
# # LOAD DATA
# # ==========================================
#
# df = pd.read_csv(csv_path)
#
# time_raw = pd.to_numeric(df.iloc[14:, 0], errors="coerce").to_numpy()
# voltage_raw = pd.to_numeric(df.iloc[14:, 1], errors="coerce").to_numpy()
#
# valid = ~(np.isnan(time_raw) | np.isnan(voltage_raw))
#
# time = time_raw[valid]
# voltage = voltage_raw[valid]
#
# # ==========================================
# # SAMPLE RATE
# # ==========================================
#
# fs = 1.0 / np.mean(np.diff(time))
#
# print(f"Sample Rate = {fs:,.0f} Hz")
#
# # ==========================================
# # BUTTERWORTH ENVELOPE
# # ==========================================
#
# cutoff_hz = 600
#
# b, a = butter(
#     4,
#     cutoff_hz,
#     btype="low",
#     fs=fs
# )
#
# butter_env = filtfilt(
#     b,
#     a,
#     np.abs(voltage)
# )
#
# # ==========================================
# # HILBERT ENVELOPE
# # ==========================================
#
# hilbert_env = np.abs(
#     hilbert(voltage)
# )
#
# # Optional smoothing
# hilbert_env = filtfilt(
#     b,
#     a,
#     hilbert_env
# )
#
# # ==========================================
# # RMS ENVELOPE
# # ==========================================
#
# window_us = 100  # adjust
#
# window_samples = max(
#     5,
#     int(fs * window_us * 1e-6)
# )
#
# kernel = np.ones(window_samples) / window_samples
#
# rms_env = np.sqrt(
#     np.convolve(
#         voltage**2,
#         kernel,
#         mode="same"
#     )
# )
#
# def find_fall_time(time, signal, averaging_points=500):
#     """
#     Find 90%-10% fall time from a filtered/envelope signal.
#
#     Returns:
#         v90, v10, idx90, idx10, t90, t10, fall_time
#     """
#
#     v_initial = np.mean(signal[:averaging_points])
#     v_final = np.mean(signal[-averaging_points:])
#
#     v90 = v_final + 0.90 * (v_initial - v_final)
#     v10 = v_final + 0.10 * (v_initial - v_final)
#
#     crossing_90 = np.where(
#         (signal[:-1] > v90) &
#         (signal[1:] <= v90)
#     )[0]
#
#     if len(crossing_90) == 0:
#         raise ValueError("No 90% crossing found")
#
#     idx90 = crossing_90[0] + 1
#
#     crossing_10 = np.where(
#         (signal[idx90:-1] > v10) &
#         (signal[idx90 + 1:] <= v10)
#     )[0]
#
#     if len(crossing_10) == 0:
#         raise ValueError("No 10% crossing found")
#
#     idx10 = idx90 + crossing_10[0] + 1
#
#     t90 = time[idx90]
#     t10 = time[idx10]
#
#     fall_time = t10 - t90
#
#     return (
#         v90,
#         v10,
#         idx90,
#         idx10,
#         t90,
#         t10,
#         fall_time
#     )
#
# # ==========================================
# # FIND FALL TIMES
# # ==========================================
#
# results = {}
#
# for name, sig in {
#     "Butter": butter_env,
#     "Hilbert": hilbert_env,
#     "RMS": rms_env
# }.items():
#
#     try:
#
#         results[name] = find_fall_time(
#             time,
#             sig
#         )
#
#         print(
#             f"{name}: "
#             f"Fall Time = {results[name][-1]*1e3:.3f} ms"
#         )
#
#     except*Exception as e:
#
#         print(f"{name}: {e}")
#
#
# # ==========================================
# # PLOT
# # ==========================================
#
# plt.figure(figsize=(14, 8))
#
# plt.plot(
#     time,
#     voltage,
#     color="lightgray",
#     linewidth=0.5,
#     label="Raw Waveform"
# )
#
# plt.plot(
#     time,
#     np.abs(voltage),
#     color="orange",
#     alpha=0.5,
#     linewidth=1,
#     label="Abs(Waveform)"
# )
#
# plt.plot(
#     time,
#     butter_env,
#     linewidth=2,
#     label=f"Butterworth ({cutoff_hz} Hz)"
# )
#
# plt.plot(
#     time,
#     hilbert_env,
#     linewidth=2,
#     label="Hilbert Envelope"
# )
#
# plt.plot(
#     time,
#     rms_env,
#     linewidth=2,
#     label=f"RMS ({window_samples} samples)"
# )
#
# colors = {
#     "Butter": "red",
#     "Hilbert": "green",
#     "RMS": "magenta"
# }
#
# for name, result in results.items():
#
#     (
#         t90,
#         v10,
#         idx90,
#         idx10,
#         v90,
#         t10,
#         fall_time
#     ) = result
#
#     color = colors[name]
#
#     plt.axvline(
#         t90,
#         color=color,
#         linestyle="--",
#         alpha=0.8
#     )
#
#     plt.axvline(
#         t10,
#         color=color,
#         linestyle=":"
#     )
#
#     plt.plot(
#         t90,
#         v90,
#         'o',
#         color=color
#     )
#
#     plt.plot(
#         t10,
#         v10,
#         's',
#         color=color
#     )
#
#     plt.annotate(
#         f"{name}\n{fall_time*1e3:.2f} ms",
#         (t10, v10),
#         xytext=(10, 10),
#         textcoords='offset points',
#         color=color
#     )
#
# plt.title(csv_path.split("/")[-1])
# plt.xlabel("Time (s)")
# plt.ylabel("Voltage (V)")
# plt.grid(True)
# plt.legend()
#
# plt.tight_layout()
# plt.show()

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt, hilbert
from tkinter import Tk, filedialog

# ==========================================================
# FALL TIME DETECTION
# ==========================================================

def find_fall_time(time, signal, averaging_points=500):
    """
    Determine 90%-10% fall time using actual initial and final
    signal levels.

    Returns:
        v90
        v10
        idx90
        idx10
        t90
        t10
        fall_time
    """

    if len(signal) < averaging_points * 2:
        averaging_points = max(10, len(signal) // 10)

    v_initial = np.mean(signal[:averaging_points])
    v_final = np.mean(signal[-averaging_points:])

    v90 = v_final + 0.90 * (v_initial - v_final)
    v10 = v_final + 0.10 * (v_initial - v_final)

    crossing_90 = np.where(
        (signal[:-1] > v90) &
        (signal[1:] <= v90)
    )[0]

    if len(crossing_90) == 0:
        raise ValueError("No downward 90% crossing found")

    idx90 = crossing_90[0] + 1

    crossing_10 = np.where(
        (signal[idx90:-1] > v10) &
        (signal[idx90 + 1:] <= v10)
    )[0]

    if len(crossing_10) == 0:
        raise ValueError("No downward 10% crossing found")

    idx10 = idx90 + crossing_10[0] + 1

    t90 = time[idx90]
    t10 = time[idx10]

    fall_time = t10 - t90

    return (
        v90,
        v10,
        idx90,
        idx10,
        t90,
        t10,
        fall_time
    )

# ==========================================================
# SELECT CSV
# ==========================================================

root = Tk()
root.withdraw()

csv_path = filedialog.askopenfilename(
    title="Select CSV File",
    filetypes=[("CSV Files", "*.csv")]
)

if not csv_path:
    raise SystemExit("No file selected.")

# ==========================================================
# LOAD CSV
# ==========================================================

df = pd.read_csv(csv_path)

time_raw = pd.to_numeric(
    df.iloc[14:, 0],
    errors="coerce"
).to_numpy()

voltage_raw = pd.to_numeric(
    df.iloc[14:, 1],
    errors="coerce"
).to_numpy()

valid = ~(
    np.isnan(time_raw) |
    np.isnan(voltage_raw)
)

time = time_raw[valid]
voltage = voltage_raw[valid]

print(f"Loaded {len(time)} samples")

# ==========================================================
# SAMPLE RATE
# ==========================================================

fs = 1.0 / np.mean(np.diff(time))

print(f"Sample Rate = {fs:,.0f} Hz")

# ==========================================================
# BUTTERWORTH ENVELOPE
# ==========================================================

cutoff_hz = 600

b, a = butter(
    4,
    cutoff_hz,
    btype="low",
    fs=fs
)

butter_env = filtfilt(
    b,
    a,
    np.abs(voltage)
)

# ==========================================================
# HILBERT ENVELOPE
# ==========================================================

hilbert_env = np.abs(
    hilbert(voltage)
)

hilbert_env = filtfilt(
    b,
    a,
    hilbert_env
)

# ==========================================================
# RMS ENVELOPE
# ==========================================================

window_us = 100

window_samples = max(
    5,
    int(fs * window_us * 1e-6)
)

kernel = np.ones(window_samples) / window_samples

rms_env = np.sqrt(
    np.convolve(
        voltage**2,
        kernel,
        mode="same"
    )
)

# ==========================================================
# RUN FALL-TIME ANALYSIS ON EACH METHOD
# ==========================================================

signals = {
    "Butterworth": butter_env,
    "Hilbert": hilbert_env,
    "RMS": rms_env
}

results = {}

print("\n==============================")
print("FALL TIME RESULTS")
print("==============================")

for name, sig in signals.items():

    try:

        result = find_fall_time(
            time,
            sig
        )

        results[name] = result

        (
            v90,
            v10,
            idx90,
            idx10,
            t90,
            t10,
            fall_time
        ) = result

        print()
        print(name)
        print(f"90% Time  : {t90:.9e} s")
        print(f"10% Time  : {t10:.9e} s")
        print(f"Fall Time : {fall_time * 1e3:.3f} ms")

    except Exception as e:

        print()
        print(f"{name} FAILED")
        print(str(e))

# ==========================================================
# PLOT RESULTS
# ==========================================================

plt.figure(figsize=(16, 9))

# Raw waveform

plt.plot(
    time,
    voltage,
    color="lightgray",
    linewidth=0.4,
    label="Raw Waveform"
)

# Envelopes

plt.plot(
    time,
    butter_env,
    color="red",
    linewidth=2,
    label=f"Butterworth ({cutoff_hz} Hz)"
)

plt.plot(
    time,
    hilbert_env,
    color="green",
    linewidth=2,
    label="Hilbert Envelope"
)

plt.plot(
    time,
    rms_env,
    color="magenta",
    linewidth=2,
    label=f"RMS Envelope ({window_samples} samples)"
)

# Cursor Colors

colors = {
    "Butterworth": "red",
    "Hilbert": "green",
    "RMS": "magenta"
}

# Plot detections

for name, result in results.items():

    (
        v90,
        v10,
        idx90,
        idx10,
        t90,
        t10,
        fall_time
    ) = result

    signal = signals[name]

    color = colors[name]

    # 90% Threshold

    plt.axhline(
        y=v90,
        color=color,
        linestyle=":",
        alpha=0.3
    )

    # 10% Threshold

    plt.axhline(
        y=v10,
        color=color,
        linestyle="--",
        alpha=0.3
    )

    # 90% Cursor

    plt.axvline(
        x=t90,
        color=color,
        linestyle=":"
    )

    # 10% Cursor

    plt.axvline(
        x=t10,
        color=color,
        linestyle="--"
    )

    # Actual Crossing Markers

    plt.plot(
        t90,
        signal[idx90],
        marker="o",
        markersize=8,
        color=color
    )

    plt.plot(
        t10,
        signal[idx10],
        marker="s",
        markersize=8,
        color=color
    )

    # Annotation

    plt.annotate(
        (
            f"{name}\n"
            f"{fall_time * 1e3:.3f} ms"
        ),
        (
            t10,
            signal[idx10]
        ),
        xytext=(10, 10),
        textcoords="offset points",
        color=color,
        fontsize=9
    )

# ==========================================================
# FORMAT PLOT
# ==========================================================

plt.title(
    "Envelope Comparison with 90%-10% Cursor Detection"
)

plt.xlabel("Time (s)")
plt.ylabel("Voltage (V)")

plt.grid(True)
plt.legend()

plt.tight_layout
plt.show()