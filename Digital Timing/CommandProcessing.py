import csv
import os
import time
from datetime import datetime
import AECommandsSerialTimingAdapter

# ---------------------------
# Command Code Mapping (AEBus command numbers used by AECommandsUniversal)
# ---------------------------
COMMAND_CODES = {
    "reportPowerSupplyType": 128,
    "reportPowerSupplySize": 129,
    "reportSoftwarePartNumber": 130,
    "reportCEXOffset": 132,
    "reportTuningTimeoutValue": 138,
    "reportCommWatchDogTimer": 139,
    "reportMinimumTuningFrequency": 144,
    "reportMaximumTuningFrequency": 145,
    "reportTuningStartFrequency": 146,
    "reportGeneratorActualFrequency": 147,
    "reportFrequencyMode": 148,
    "reportSetpointRampingConfiguration": 151,
    "reportRegulationMode": 154,
    "reportActiveControlMode": 155,
    "reportRetuningThreshold": 158,
    "reportTuningTime": 159,
    "reportTuneDelay": 160,
    "reportFixedFrequency": 161,
    "reportProcessStatus": 162,
    "reportSetpointandRegulationMode": 164,
    "reportForwardPower": 165,
    "reportReflectedPower": 166,
    "reportDeliveredPower": 167,
    "reportExternalFeedback": 168,
    "reportUserExternalFeedbackLimit": 171,
    "reportRecipeParameters": 188,
    "reportRecipeStatus": 189,
    "reportOpenCloseLoopOperation": 192,
    "reportPulsingFrequency": 193,
    "reportPulsingDutyCycle": 196,
    "reportSoftwareRevisionLevel": 198,
    "reportArcEvents": 199,
    "reportUnitOnEvents": 201,
    "reportOutputOnEvents": 202,
    "reportOvertempEvents": 203,
    "reportSystemControl": 204,
    "reportUnitRunTime": 205,
    "reportTotalEnergyOutput": 206,
    "reportMeasurementCorrectionFactors": 208,
    "reportSerialAddressandBaudRate": 212,
    "reportMaskedFaultCode": 214,
    "reportCondensedGeneratorSnapshot": 219,
    "reportPINNumber": 221,
    "reportGeneratorSnapshotData": 222
}
# ---------------------------
# Configuration — edit these
# ---------------------------
PLATFORM  = "refreshed 3k"             # "legacy" or "refreshed"
COMM_TYPE = "serial"                # "serial" or "ethernet"
IP_ADDRESS = "PMNT012837"           # used when COMM_TYPE == "ethernet" for legacy
COM_PORT   = "COM5"                 # used when COMM_TYPE == "serial"

ITERATION = 25

filenum    = 1
os.makedirs("logs", exist_ok=True)
while os.path.exists("logs/log_%d.csv" % filenum):
    filenum += 1
CSV_PATH   = "logs/log_%d.csv" % filenum

# if "legacy" in PLATFORM:                # legacy unit
#     if COMM_TYPE == "ethernet":
#         IP_ADDRESS = "PMNT03DFC0"       # legacy IP address
#     else:
#         COM_PORT   = "COM12"             # used when COMM_TYPE == "serial"
# elif "refreshed" in PLATFORM:           # refreshed unit
#     if COMM_TYPE == "ethernet":
#         IP_ADDRESS = "RFGN07C807"       # refreshed IP address
#     else:
#         COM_PORT   = "COM11"             # used when COMM_TYPE == "serial"

# ---------------------------
# Logging helper
# ---------------------------
def run_command(gen, cmd_name, cmd_fn, writer, *args, **kwargs):
    """Run one command, capture latency, write one CSV row."""
    latency_str = "ERROR"
    count = 0
    cum_lat = 0
    cmd_code = COMMAND_CODES.get(cmd_name, "UNKNOWN")
    try:
        for _ in range(ITERATION):
            if cmd_name == "reportPINNumber":
                gen.PINNumber = None  # Reset PINNumber before each call
            cmd_fn(*args, **kwargs)
            count += 1
            lat = gen.gen.latency_ms
            print(f"[DEBUG] Latency raw value: {lat}")
            cum_lat = cum_lat + lat
        latency_str = f"{cum_lat/(count):.3f}" if cum_lat != 0 else "N/A"
    except Exception as e:
        latency_str = f"ERROR: {e}"
        cum_lat = 0
        print(f"[ERROR] {cmd_name}: {e}")

    # if "ERROR" not in latency_str:
    latency_str = f"{latency_str}"
    writer.writerow([
        datetime.now().isoformat(),
        PLATFORM,
        COMM_TYPE,
        cmd_name,
        cmd_code,
        latency_str,
        count
    ])
    print(f"{cmd_name:<40} {latency_str}")

# ---------------------------
# Logging helper
# ---------------------------
MAX_POWER = 5000
MAX_POWER_10 = MAX_POWER*10


regulation = [6,7,9]
# freq = [13000, 13500, 13560, 14000, 14200]
freq = [1800, 1985, 2170]
def run_set_command(gen, samples):
    setpoint = 0
    mode = (0 & 0x01)
    # gen.setPulsingConfiguration(8, 1)
    # gen.setPulsingConfiguration(6, 1)

    for s in range(samples):
        mode = (~mode & 0x01)
        setpoint = min(setpoint + 100, MAX_POWER_10)
        gen.setActiveControlMode(2)
        gen.setPowerSetpoint(setpoint)
        gen.setPulsingConfiguration(8, 1)
        gen.rfON()
        gen.setFixedFrequency(freq[s % len(freq)])
        gen.setRegulationMode(regulation[s % len(regulation)])
        gen.rfOFF()
        gen.setUserPowerLimit(MAX_POWER)
        ## gen.setSetpointRampingConfiguration(6, 0, 1, 10, 100, 0, 0)
        gen.setCEXOffset(0)
        #########################
        gen.setTuningTimeoutValue(10)
        # gen.setMinimumTuningFrequency(13000)
        gen.setMinimumTuningFrequency(1800)
        # gen.setMaximumTuningFrequency(14200)
        gen.setMaximumTuningFrequency(2170)
        gen.setTuningStartFrequency(freq[s % len(freq)])
        gen.setFrequencyMode(mode)
        gen.setRetuningThreshold(100)
        gen.setTuneDelay(1)
        # gen.setFixedFrequency(13560)
        gen.setFixedFrequency(1985)
        gen.setSystemControl(5, mode)
        gen.setPulsingFrequency(1000)
        gen.setPulsingDutyCycle(50)
        gen.setUnlockedSubcommand(1, 10)
        gen.setUnlockedSubcommand(2, 20)
        gen.setUnlockedSubcommand(3, 1)
        gen.setUnlockedSubcommand(8, 20)
        # gen.setUnlockedSubcommand(16, mode)
        # Belei Note: the line above was causing a CSR fault, so I just commented it out for now and ran the test without it. 
        gen.setUnlockedSubcommand(52, 1, mode)
        ###############
        gen.setActiveControlMode(4)

codes = [17, 18, 21, 24, 25, 26, 27, 28, 29, 31, 32, 34, 35, 38, 39, 40]
# ---------------------------
# Run Scripts
# ---------------------------
if __name__ == "__main__":
    if COMM_TYPE == "serial":
        gen = AECommandsSerialTimingAdapter.generator(0, COM_PORT)
    else:
        gen = AECommandsSerialTimingAdapter.generator(IP_ADDRESS, None)


    # for i in range(857):
    #     gen.setLockedSubCommand(0, 49, 0, 1, codes[i % len(codes)])
    #     gen.setLockedSubCommand(0, 49, 0, 0, codes[i % len(codes)])


    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Timestamp", "Platform", "Comm_Type", "CommandName", "Command", "Latency_ms", "Samples"])


        run_set_command(gen, ITERATION)
        # --- add / remove commands here ---
        run_command(gen, "reportPINNumber", gen.reportPINNumber, writer) #221
        run_command(gen, "reportPowerSupplyType", gen.reportPowerSupplyType, writer) #128
        run_command(gen, "reportPowerSupplySize", gen.reportPowerSupplySize, writer) #129
        run_command(gen, "reportSoftwarePartNumber", gen.reportSoftwarePartNumber, writer, 0) #130-0
        run_command(gen, "reportCEXOffset", gen.reportCEXOffset, writer) #132
        run_command(gen, "reportTuningTimeoutValue", gen.reportTuningTimeoutValue, writer) #138
        run_command(gen, "reportCommWatchDogTimer", gen.reportCommWatchDogTimer, writer) #139
        run_command(gen, "reportHostTimeout", gen.reportHostTimeout, writer) #140
        run_command(gen, "reportMinimumTuningFrequency", gen.reportMinimumTuningFrequency, writer) #144
        run_command(gen, "reportMaximumTuningFrequency", gen.reportMaximumTuningFrequency, writer) #145
        run_command(gen, "reportTuningStartFrequency", gen.reportTuningStartFrequency, writer) #146
        run_command(gen, "reportGeneratorActualFrequency", gen.reportGeneratorActualFrequency, writer) #147
        run_command(gen, "reportFrequencyMode", gen.reportFrequencyMode, writer) #148
        run_command(gen, "reportActiveControlMode", gen.reportActiveControlMode, writer) #155
        run_command(gen, "reportRetuningThreshold", gen.reportRetuningThreshold, writer) #158
        run_command(gen, "reportTuningTime", gen.reportTuningTime, writer) #159
        run_command(gen, "reportTuneDelay", gen.reportTuneDelay, writer) #160
        run_command(gen, "reportFixedFrequency", gen.reportFixedFrequency, writer) #161
        run_command(gen, "reportProcessStatus", gen.reportProcessStatus, writer) #162
        run_command(gen, "reportSetpointandRegulationMode", gen.reportSetpointandRegulationMode, writer) #164
        run_command(gen, "reportForwardPower", gen.reportForwardPower, writer) #165
        run_command(gen, "reportReflectedPower", gen.reportReflectedPower, writer) #166
        run_command(gen, "reportDeliveredPower", gen.reportDeliveredPower, writer) #167
        run_command(gen, "reportUserPowerLimit", gen.reportUserPowerLimit, writer) #169
        run_command(gen, "reportUserReflectedLimit", gen.reportUserReflectedLimit, writer) #170
        run_command(gen, "reportUserExternalFeedbackLimit", gen.reportUserExternalFeedbackLimit, writer) #171
        run_command(gen, "reportPulsingConfiguration", gen.reportPulsingConfiguration, writer, 1) #172
        run_command(gen, "reportRecipeParameters", gen.reportRecipeParameters, writer, 1,1,1,1) #188
        run_command(gen, "reportRecipeStatus", gen.reportRecipeStatus, writer) #189
        run_command(gen, "reportPulsingFrequency", gen.reportPulsingFrequency, writer) #193
        run_command(gen, "reportPulsingDutyCycle", gen.reportPulsingDutyCycle, writer) #196
        run_command(gen, "reportUnitOnEvents", gen.reportUnitOnEvents, writer) #201
        run_command(gen, "reportOutputOnEvents", gen.reportOutputOnEvents, writer) #202
        run_command(gen, "reportOvertempEvents", gen.reportOvertempEvents, writer) #203
        run_command(gen, "reportSystemControl", gen.reportSystemControl, writer, 0, 0, 0) #204
        run_command(gen, "reportUnitRunTime", gen.reportUnitRunTime, writer) #205
        run_command(gen, "ReportTotalEnergyOutput", gen.reportTotalEnergyOutput, writer) #206

 
        # ----------------------------------

    print(f"\nLatency log saved to {CSV_PATH}")
    gen.close()
