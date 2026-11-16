"""Interlock Response Timing data collection GUI.

Measures the delay from a falling 25 V interlock signal to RF shutdown. The
BK Precision 9141 drives User Port pin 23, waveform-generator CH1 supplies the
analog setpoint, and CH2 supplies RF Enable. Scope CH1 captures the falling
interlock edge and CH2 captures the raw RF waveform. Waveforms are transferred
directly to the laptop over Ethernet after every successful acquisition.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import numpy as np
import pyvisa

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
import suite_common.bootstrap  # noqa: F401
from suite_common.bench import bench_default, visa_socket_resource
import AECommandsUniversal

TEST_NAME = "Interlock Response Timing"
TEST_ID = "INTERLOCK_RESPONSE_TIMING"
CONTRACT_NAME = "Paramount.InterlockResponseTiming"
CONTRACT_VERSION = "1.0.0"

DUT_ADDRESS_DEFAULT = bench_default("generator")
SCOPE_ADDRESS_DEFAULT = bench_default("oscilloscope")
WAVEGEN_ADDRESS_DEFAULT = bench_default("waveform_generator")
DC_SUPPLY_ADDRESS_DEFAULT = bench_default("dc_supply")

DEFAULT_RATED_POWER_W = 3000.0
DEFAULT_POWER_LEVELS_W = [500.0, 1500.0, 3000.0]
DEFAULT_CYCLES_PER_POWER = 10
MAX_CYCLES = 100
SETPOINT_FULL_SCALE_V = 10.0
SETPOINT_WG_CHANNEL = 1
RF_ENABLE_WG_CHANNEL = 2
RF_ENABLE_LOW_V = 0.0
RF_ENABLE_HIGH_V = 6.0
WAVEGEN_LOAD = "HZ"

OPERATING_FREQUENCY_KHZ = 13_560
HOST_MODE = 2
USER_PORT_MODE = 4
HALO_SETPOINT_RESOLUTION = 10
COMMAND_SETTLE_S = 0.25
SETUP_SETTLE_S = 1.0
RF_ON_SETTLE_S = 1.0
RECOVERY_S = 1.0
ACQUISITION_TIMEOUT_S = 10.0
RF_OFF_VERIFY_TIMEOUT_S = 2.0
RF_OFF_TO_INTERLOCK_HIGH_DELAY_S = 1.0
RF_OFF_CONFIRM_READS = 3

DC_SUPPLY_CHANNEL = 1
INTERLOCK_VOLTAGE_V = 25.0
DC_SUPPLY_CURRENT_LIMIT_A = 0.5
DC_SUPPLY_OUTPUT_SETTLE_S = 0.25
DC_SUPPLY_VERIFY_TOLERANCE_V = 1.0
DC_SUPPLY_READY_TIMEOUT_S = 5.0
DC_SUPPLY_POLL_INTERVAL_S = 0.05
DC_SUPPLY_CURRENT_LIMIT_MARGIN_A = 0.01

INSTRUMENT_TIMEOUT_MS = 30_000
WAVEFORM_TRANSFER_TIMEOUT_MS = 120_000
WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES = 25 * 1024 * 1024
SCOPE_TIME_PER_DIV_S = 20e-3
SCOPE_HORIZONTAL_POSITION_PERCENT = 35.0
SCOPE_RECORD_LENGTH = 100_000
SCOPE_ARM_S = 0.35
SCOPE_TRIGGER_LEVEL_V = 15.0
SCOPE_TRIGGER_SLOPE = "FALL"
SCOPE_TRIGGER_COUPLING = "NOISEREJ"
SCOPE_CONFIG = {
    "save_channels": (1, 2),
    "labels": {1: "Interlock_Falling_Edge", 2: "RF_Envelope_Falling_Edge"},
    "scale": {1: 5.0, 2: 1.0},
    "position": {1: 0.0, 2: 0.0},
    "offset": {1: 12.5, 2: 0.0},
    "termination": {1: 1_000_000.0, 2: 50.0},
}


def safe_component(value: object, fallback: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "").strip())
    return re.sub(r"\s+", " ", text).strip(" .") or fallback


def parse_power_levels(text: str) -> list[float]:
    values = [float(token) for token in re.split(r"[,;\s]+", text.strip()) if token]
    if not values: raise ValueError("Enter at least one power level.")
    if any(not math.isfinite(value) for value in values): raise ValueError("Power levels must be finite.")
    if len(set(values)) != len(values): raise ValueError("Power levels cannot contain duplicates.")
    return values


def validate_settings(rated_power_w: float, power_levels_w: list[float], cycles: int) -> None:
    if not math.isfinite(rated_power_w) or rated_power_w <= 0: raise ValueError("Rated power must be greater than zero.")
    if not 1 <= cycles <= MAX_CYCLES: raise ValueError(f"Cycles must be from 1 through {MAX_CYCLES}.")
    for power in power_levels_w:
        if not 0 < power <= rated_power_w: raise ValueError(f"Power {power:g} W is outside 0 to {rated_power_w:g} W.")


def unique_run_folder(parent: Path, dut_address: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base = parent / f"{stamp} - {TEST_NAME} - {safe_component(dut_address, 'Generator')}"
    candidate = base; run = 1
    while candidate.exists(): run += 1; candidate = parent / f"{base.name} - Run {run}"
    candidate.mkdir(parents=False, exist_ok=False)
    return candidate


@dataclass
class RunContext:
    run_dir: Path
    run_id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    configuration_rows: list[dict[str, Any]] = field(default_factory=list)
    capture_rows: list[dict[str, Any]] = field(default_factory=list)
    verification_rows: list[dict[str, Any]] = field(default_factory=list)
    event_rows: list[dict[str, Any]] = field(default_factory=list)

    def _stamp(self, row):
        return {"Timestamp": datetime.now().isoformat(timespec="milliseconds"), "Contract_Name": CONTRACT_NAME,
                "Contract_Version": CONTRACT_VERSION, "Test_ID": TEST_ID, "Run_ID": self.run_id, **row}

    def _write(self, name, rows):
        if not rows: return
        fields=[]
        for row in rows:
            for key in row:
                if key not in fields: fields.append(key)
        target=self.run_dir/name; temporary=target.with_suffix(target.suffix+".tmp")
        with temporary.open("w",newline="",encoding="utf-8-sig") as handle:
            writer=csv.DictWriter(handle,fieldnames=fields,extrasaction="ignore");writer.writeheader();writer.writerows(rows)
        os.replace(temporary,target)

    def add_configuration(self, **row): self.configuration_rows.append(self._stamp(row)); self._write("Run_Configuration.csv",self.configuration_rows)
    def add_capture(self, **row): self.capture_rows.append(self._stamp(row)); self._write("Capture_Results.csv",self.capture_rows)
    def add_verification(self, **row): self.verification_rows.append(self._stamp(row)); self._write("RF_Off_Verification.csv",self.verification_rows)
    def add_event(self,severity,event,**row): self.event_rows.append(self._stamp({"Severity":severity,"Event":event,**row}));self._write("Event_Log.csv",self.event_rows)

    def write_contract(self):
        data={"contract_name":CONTRACT_NAME,"contract_version":CONTRACT_VERSION,"test_id":TEST_ID,"test_name":TEST_NAME,
              "waveforms_are_transferred_over_ethernet":True,"waveform_location":"Local run folder",
              "waveform_channels":{"CH1":"Interlock falling edge","CH2":"Raw RF waveform"},
              "capture_key":["Run_ID","Requested_Power_W","Cycle"],
              "safety_rule":"Abort and command DUT RF OFF if RF-off verification fails after interlock removal."}
        (self.run_dir/"Output_Contract.json").write_text(json.dumps(data,indent=2),encoding="utf-8")

    def write_manifest(self,status,expected,**extra):
        data={"contract_name":CONTRACT_NAME,"contract_version":CONTRACT_VERSION,"run_id":self.run_id,"status":status,
              "updated":datetime.now().isoformat(timespec="seconds"),"expected_captures":expected,
              "recorded_captures":len(self.capture_rows),"verification_rows":len(self.verification_rows),
              "run_directory":str(self.run_dir),"files":sorted(path.name for path in self.run_dir.iterdir() if path.is_file()),**extra}
        (self.run_dir/"Run_Manifest.json").write_text(json.dumps(data,indent=2),encoding="utf-8")


class Instruments:
    def __init__(self,dut_address,scope_address,wavegen_address,supply_address):
        self.dut_address=dut_address.strip();self.scope_address=scope_address.strip();self.wavegen_address=wavegen_address.strip();self.supply_address=supply_address.strip()
        self.wavegen_fallback=visa_socket_resource(self.wavegen_address);self.supply_fallback=visa_socket_resource(self.supply_address)
        self.rm=self.scope=self.wavegen=self.supply=self.dut=None

    def _open_with_fallback(self,addresses,label):
        errors=[]
        for address in dict.fromkeys(addresses):
            candidate=None
            try:
                candidate=self.rm.open_resource(address);candidate.timeout=INSTRUMENT_TIMEOUT_MS
                if str(address).endswith("::SOCKET"): candidate.read_termination="\n";candidate.write_termination="\n"
                print(f"{label} connected using {address}: {candidate.query('*IDN?').strip()}");return candidate
            except Exception as exc:
                errors.append(f"{address}: {type(exc).__name__}: {exc}")
                try:
                    if candidate is not None:candidate.close()
                except Exception:pass
        raise RuntimeError(f"Unable to connect to {label}. "+" | ".join(errors))

    def connect(self):
        self.rm=pyvisa.ResourceManager();print(f"VISA backend: {self.rm.visalib}")
        self.scope=self.rm.open_resource(self.scope_address);self.scope.timeout=INSTRUMENT_TIMEOUT_MS
        self.scope.read_termination="\n";self.scope.write_termination="\n";self.scope.chunk_size=WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES
        print("Scope:",self.scope.query("*IDN?").strip())
        self.wavegen=self._open_with_fallback((self.wavegen_address,self.wavegen_fallback),"waveform generator")
        self.supply=self._open_with_fallback((self.supply_address,self.supply_fallback),"BK Precision 9141")
        configure_interlock_supply(self.supply)
        self.dut=AECommandsUniversal.generator(self.dut_address);print("DUT connected.")

    def close(self):
        try:
            if self.wavegen is not None:
                for channel in (SETPOINT_WG_CHANNEL,RF_ENABLE_WG_CHANNEL):
                    self.wavegen.write(f"C{channel}:BSWV OFST,0");self.wavegen.write(f"C{channel}:OUTP OFF")
        except Exception as exc:print("Wavegen cleanup warning:",exc)
        try:
            if self.supply is not None:interlock_output(self.supply,False,False)
        except Exception as exc:print("Supply cleanup warning:",exc)
        try:
            if self.dut is not None:self.dut.setActiveControlMode(HOST_MODE);time.sleep(COMMAND_SETTLE_S);self.dut.rfOFF()
        except Exception as exc:print("DUT cleanup warning:",exc)
        try:
            if self.scope is not None:self.scope.write("ACQ:STATE OFF")
        except Exception:pass
        for item in (self.scope,self.wavegen,self.supply):
            try:
                if item is not None:item.close()
            except Exception:pass
        try:
            if self.dut is not None:self.dut.close()
        except Exception:pass
        try:
            if self.rm is not None:self.rm.close()
        except Exception:pass


def supply_select_channel(supply):
    index=DC_SUPPLY_CHANNEL-1
    if index not in (0,1,2):raise ValueError("DC_SUPPLY_CHANNEL must be 1, 2, or 3.")
    supply.write(f"INST {index}")


def configure_interlock_supply(supply):
    supply_select_channel(supply);supply.write("*CLS");supply.write("OUTP:STAT OFF")
    supply.write(f"APPL {INTERLOCK_VOLTAGE_V:.6f},{DC_SUPPLY_CURRENT_LIMIT_A:.6f}");supply.query("*OPC?")
    print("BK supply programmed:",supply.query("APPL?").strip())


def interlock_output(supply,enabled,verify_voltage=True):
    supply_select_channel(supply);started=time.monotonic();supply.write(f"OUTP:STAT {'ON' if enabled else 'OFF'}");supply.query("*OPC?")
    time.sleep(DC_SUPPLY_OUTPUT_SETTLE_S);state=supply.query("OUTP:STAT?").strip().upper()
    if state not in ({"1","ON"} if enabled else {"0","OFF"}):raise RuntimeError(f"BK output verification failed: {state!r}")
    result={"Enabled":bool(enabled),"Output_State":state,"Measured_Voltage_V":None,"Measured_Current_A":None,"Ready_Time_s":time.monotonic()-started}
    if not enabled or not verify_voltage:return result
    deadline=time.monotonic()+DC_SUPPLY_READY_TIMEOUT_S
    while True:
        voltage=float(supply.query("MEAS:VOLT?").strip());current=float(supply.query("MEAS:CURR?").strip());elapsed=time.monotonic()-started
        result.update(Measured_Voltage_V=voltage,Measured_Current_A=current,Ready_Time_s=elapsed)
        if abs(voltage-INTERLOCK_VOLTAGE_V)<=DC_SUPPLY_VERIFY_TOLERANCE_V:return result
        if time.monotonic()>=deadline:
            hint="Current is near the limit; check for CC operation." if current>=DC_SUPPLY_CURRENT_LIMIT_A-DC_SUPPLY_CURRENT_LIMIT_MARGIN_A else "Check wiring and supply slew/delay settings."
            raise RuntimeError(f"BK output did not reach {INTERLOCK_VOLTAGE_V:g} V within {DC_SUPPLY_READY_TIMEOUT_S:g} s. Last: {voltage:g} V, {current:g} A. {hint}")
        time.sleep(DC_SUPPLY_POLL_INTERVAL_S)


def configure_wavegen_channel(wavegen,channel,voltage,output_on):
    wavegen.write(f"C{channel}:OUTP OFF");wavegen.write(f"C{channel}:OUTP LOAD,{WAVEGEN_LOAD}")
    wavegen.write(f"C{channel}:BSWV WVTP,DC");wavegen.write(f"C{channel}:BSWV OFST,{float(voltage):.6f}");wavegen.write(f"C{channel}:OUTP {'ON' if output_on else 'OFF'}")
    response=wavegen.query(f"C{channel}:OUTP?").strip()
    if "LOAD,HZ" not in response.upper().replace(" ",""):raise RuntimeError(f"Wavegen CH{channel} did not report High-Z load: {response!r}")


def set_wavegen_dc(wavegen,channel,voltage,output_on=True):
    wavegen.write(f"C{channel}:BSWV OFST,{float(voltage):.6f}");wavegen.write(f"C{channel}:OUTP {'ON' if output_on else 'OFF'}")


def set_host_configuration(dut,power_w):
    dut.setActiveControlMode(HOST_MODE);time.sleep(COMMAND_SETTLE_S);dut.rfOFF();dut.setFixedFrequency(OPERATING_FREQUENCY_KHZ)
    dut.setFrequencyMode(0);dut.setPulsingFrequency(0);dut.setPowerSetpoint(int(round(power_w*HALO_SETPOINT_RESOLUTION)))


def enter_user_port(dut):
    dut.setActiveControlMode(USER_PORT_MODE);time.sleep(COMMAND_SETTLE_S)
    if int(dut.reportActiveControlMode())!=USER_PORT_MODE:raise RuntimeError("DUT did not enter User Port mode.")


def wait_until(reader:Callable[[],bool],expected:bool,timeout_s:float,consecutive=1,poll_s=.05):
    deadline=time.monotonic()+timeout_s;matches=0
    while time.monotonic()<deadline:
        if bool(reader())==expected:
            matches+=1
            if matches>=consecutive:return True
        else:matches=0
        time.sleep(poll_s)
    return False


def prepare_power_point(inst,power_w,rated_power_w):
    setpoint=power_w/rated_power_w*SETPOINT_FULL_SCALE_V
    configure_wavegen_channel(inst.wavegen,SETPOINT_WG_CHANNEL,setpoint,False)
    configure_wavegen_channel(inst.wavegen,RF_ENABLE_WG_CHANNEL,RF_ENABLE_LOW_V,False)
    interlock_output(inst.supply,False,False);set_host_configuration(inst.dut,power_w);enter_user_port(inst.dut)
    set_wavegen_dc(inst.wavegen,SETPOINT_WG_CHANNEL,setpoint,True);set_wavegen_dc(inst.wavegen,RF_ENABLE_WG_CHANNEL,RF_ENABLE_LOW_V,True)
    time.sleep(SETUP_SETTLE_S);return setpoint


def termination_is_50(response):
    text=response.strip().upper().replace("OHM","")
    if "FIFTY" in text or text=="50":return True
    try:return 45<=float(text)<=55
    except ValueError:return False


def set_termination(scope,channel,value):
    commands=[f"CH{channel}:TERMINATION FIFTY",f"CH{channel}:TERMINATION 50"] if value==50 else [f"CH{channel}:TERMINATION MEG",f"CH{channel}:TERMINATION {value:g}"]
    for command in commands:
        try:
            scope.write(command);scope.query("*OPC?");response=scope.query(f"CH{channel}:TERMINATION?").strip()
            if (termination_is_50(response) if value==50 else not termination_is_50(response)):return response
        except Exception:pass
    raise RuntimeError(f"Could not verify CH{channel} termination {value:g} ohm.")


def configure_scope(scope):
    scope.write("RECALL:SETUP DEFAULT");time.sleep(1.5);scope.write("*CLS");scope.write("ACQ:STATE OFF")
    for channel in range(1,5):
        if channel in SCOPE_CONFIG["save_channels"]:
            scope.write(f"SEL:CH{channel} ON");scope.write(f"CH{channel}:COUP DC");set_termination(scope,channel,SCOPE_CONFIG["termination"][channel])
            scope.write(f"CH{channel}:SCA {SCOPE_CONFIG['scale'][channel]}");scope.write(f"CH{channel}:POS {SCOPE_CONFIG['position'][channel]}");scope.write(f"CH{channel}:OFFSET {SCOPE_CONFIG['offset'][channel]}")
        else:scope.write(f"SEL:CH{channel} OFF")
    scope.write(f"HOR:SCALE {SCOPE_TIME_PER_DIV_S}");scope.write("HORIZONTAL:DELAY:MODE OFF");scope.write(f"HORIZONTAL:POSITION {SCOPE_HORIZONTAL_POSITION_PERCENT}")
    scope.write(f"HORIZONTAL:RECORDLENGTH {SCOPE_RECORD_LENGTH}");scope.write("TRIG:A:EDGE:SOU CH1");scope.write(f"TRIG:A:LEV:CH1 {SCOPE_TRIGGER_LEVEL_V}")
    scope.write(f"TRIG:A:EDGE:SLO {SCOPE_TRIGGER_SLOPE}");scope.write(f"TRIG:A:EDGE:COUP {SCOPE_TRIGGER_COUPLING}");scope.write("ACQ:STOPA SEQ")


def arm_scope(scope):
    scope.write("*CLS");scope.write("ACQ:STOPA SEQ");scope.write("ACQ:STATE ON");time.sleep(SCOPE_ARM_S)


def wait_for_scope(scope):
    deadline=time.monotonic()+ACQUISITION_TIMEOUT_S
    while time.monotonic()<deadline:
        if scope.query("TRIG:STATE?").strip().upper() in {"SAVE","STOP"}:return True
        time.sleep(.05)
    return False


def transfer_channel(scope,channel,path):
    partial=path.with_suffix(path.suffix+".partial");original=scope.timeout
    try:
        scope.timeout=WAVEFORM_TRANSFER_TIMEOUT_MS;scope.write("ACQUIRE:STATE STOP");scope.write("*WAI");scope.write(f"DATA:SOURCE CH{channel}")
        scope.write("DATA:ENCDG RIBINARY");scope.write("DATA:WIDTH 2");count=int(float(scope.query("HORIZONTAL:RECORDLENGTH?")))
        scope.write("DATA:START 1");scope.write(f"DATA:STOP {count}")
        xincr=float(scope.query("WFMOUTPRE:XINCR?"));xzero=float(scope.query("WFMOUTPRE:XZERO?"));pt_off=float(scope.query("WFMOUTPRE:PT_OFF?"))
        ymult=float(scope.query("WFMOUTPRE:YMULT?"));yzero=float(scope.query("WFMOUTPRE:YZERO?"));yoff=float(scope.query("WFMOUTPRE:YOFF?"))
        raw=np.asarray(scope.query_binary_values("CURVE?",datatype="h",is_big_endian=True,container=np.array,expect_termination=False),dtype=float)
        if raw.size==0:raise RuntimeError(f"No waveform points returned for CH{channel}.")
        index=np.arange(raw.size,dtype=float);times=xzero+(index-pt_off)*xincr;values=(raw-yoff)*ymult+yzero
        np.savetxt(partial,np.column_stack((times,values)),delimiter=",",header=f"Time (s),CH{channel} (V)",comments="",fmt="%.12e");partial.replace(path)
        return {"Path":str(path),"Points":int(raw.size),"Sample_Interval_s":xincr}
    except Exception:
        if partial.exists():partial.unlink()
        raise
    finally:scope.timeout=original


def capture_interlock_fall(inst,ctx,power_w,setpoint_v,cycle):
    descriptor=f"{power_w:g}W_Cycle{cycle}_Interlock_FALL";configure_scope(inst.scope);arm_scope(inst.scope)
    stimulus=interlock_output(inst.supply,False,False);captured=wait_for_scope(inst.scope);final=inst.scope.query("TRIG:STATE?").strip().upper();files={}
    if captured:
        for channel in SCOPE_CONFIG["save_channels"]:
            stamp=datetime.now().strftime("%Y%m%d_%H%M%S_%f");path=ctx.run_dir/f"{TEST_ID}_{descriptor}_CH{channel}_{stamp}.csv"
            files[channel]=transfer_channel(inst.scope,channel,path)
    ctx.add_capture(Capture_ID=descriptor,Requested_Power_W=power_w,Analog_Setpoint_V=setpoint_v,Cycle=cycle,Triggered=captured,
                    Trigger_Level_V=SCOPE_TRIGGER_LEVEL_V,Trigger_Slope=SCOPE_TRIGGER_SLOPE,Trigger_Coupling=SCOPE_TRIGGER_COUPLING,
                    Final_Trigger_State=final,Stimulus_Output_State=stimulus.get("Output_State"),CH1_Label=SCOPE_CONFIG["labels"][1],CH2_Label=SCOPE_CONFIG["labels"][2],
                    CH1_File=files.get(1,{}).get("Path"),CH2_File=files.get(2,{}).get("Path"),CH1_Points=files.get(1,{}).get("Points"),CH2_Points=files.get(2,{}).get("Points"))
    return captured


def run_collection(parent,dut_address,scope_address,wavegen_address,supply_address,rated,powers,cycles,progress):
    validate_settings(rated,powers,cycles);run_dir=unique_run_folder(parent,dut_address);ctx=RunContext(run_dir);ctx.write_contract();expected=len(powers)*cycles
    ctx.add_configuration(Configuration="Run",DUT_Address=dut_address,Scope_Address=scope_address,Wavegen_Address=wavegen_address,DC_Supply_Address=supply_address,
                          Rated_Power_W=rated,Power_Levels_W=str(powers),Cycles_Per_Power=cycles,Wavegen_Load=WAVEGEN_LOAD,Interlock_Voltage_V=INTERLOCK_VOLTAGE_V,
                          Interlock_Current_Limit_A=DC_SUPPLY_CURRENT_LIMIT_A,RF_Off_To_Interlock_High_Delay_s=RF_OFF_TO_INTERLOCK_HIGH_DELAY_S,
                          RF_Off_Confirm_Reads=RF_OFF_CONFIRM_READS,Scope_Time_Per_Div_s=SCOPE_TIME_PER_DIV_S,Scope_Trigger_Level_V=SCOPE_TRIGGER_LEVEL_V)
    ctx.write_manifest("initialized",expected);inst=Instruments(dut_address,scope_address,wavegen_address,supply_address);status="aborted"
    try:
        inst.connect();ctx.add_event("INFO","Instrument connection completed")
        for power in powers:
            setpoint=prepare_power_point(inst,power,rated);ctx.add_configuration(Configuration="Power Point",Requested_Power_W=power,Analog_Setpoint_V=setpoint,Cycles=cycles)
            for cycle in range(1,cycles+1):
                progress(f"{power:g} W, cycle {cycle}/{cycles}: establishing interlock-high RF-on state")
                set_wavegen_dc(inst.wavegen,RF_ENABLE_WG_CHANNEL,RF_ENABLE_LOW_V,True)
                if not wait_until(lambda:bool(inst.dut.isRFon()),False,RF_OFF_VERIFY_TIMEOUT_S,RF_OFF_CONFIRM_READS,.1):raise RuntimeError("Could not confirm stable RF off before restoring interlock high.")
                time.sleep(RF_OFF_TO_INTERLOCK_HIGH_DELAY_S);high=interlock_output(inst.supply,True,True);time.sleep(RECOVERY_S)
                set_wavegen_dc(inst.wavegen,RF_ENABLE_WG_CHANNEL,RF_ENABLE_HIGH_V,True)
                if not wait_until(lambda:bool(inst.dut.isRFon()),True,RF_ON_SETTLE_S+ACQUISITION_TIMEOUT_S):raise RuntimeError(f"RF did not turn on at {power:g} W cycle {cycle}.")
                time.sleep(RF_ON_SETTLE_S);captured=capture_interlock_fall(inst,ctx,power,setpoint,cycle)
                rf_off=wait_until(lambda:bool(inst.dut.isRFon()),False,RF_OFF_VERIFY_TIMEOUT_S)
                ctx.add_verification(Requested_Power_W=power,Analog_Setpoint_V=setpoint,Cycle=cycle,Scope_Captured=captured,RF_Off_Verified=rf_off,
                                     BK_High_Measured_Voltage_V=high.get("Measured_Voltage_V"),BK_High_Measured_Current_A=high.get("Measured_Current_A"),BK_High_Ready_Time_s=high.get("Ready_Time_s"))
                if not rf_off:
                    try:inst.dut.rfOFF()
                    finally:raise RuntimeError("Abort: RF remained on after the falling interlock stimulus.")
                set_wavegen_dc(inst.wavegen,RF_ENABLE_WG_CHANNEL,RF_ENABLE_LOW_V,True);time.sleep(RECOVERY_S);ctx.write_manifest("running",expected,current_power_w=power,current_cycle=cycle)
        status="completed";ctx.add_event("INFO","Interlock response timing collection completed");return 0,run_dir
    finally:
        inst.close();ctx.write_manifest(status,expected)


class CollectionGUI:
    def __init__(self,root):
        import tkinter as tk
        from tkinter import ttk
        self.root=root;self.tk=tk;self.ttk=ttk;self.running=False;self.exit_code=2
        self.parent=tk.StringVar(value=os.environ.get("PARAMOUNT_COLLECTION_PARENT_FOLDER","") or str(Path.home()))
        self.dut=tk.StringVar(value=DUT_ADDRESS_DEFAULT or "");self.scope=tk.StringVar(value=SCOPE_ADDRESS_DEFAULT or "");self.wavegen=tk.StringVar(value=WAVEGEN_ADDRESS_DEFAULT or "");self.supply=tk.StringVar(value=DC_SUPPLY_ADDRESS_DEFAULT or "")
        self.rated=tk.StringVar(value=f"{DEFAULT_RATED_POWER_W:g}");self.powers=tk.StringVar(value=", ".join(f"{x:g}" for x in DEFAULT_POWER_LEVELS_W));self.cycles=tk.StringVar(value=str(DEFAULT_CYCLES_PER_POWER));self.status=tk.StringVar(value="Ready");self._build()
    def _build(self):
        t=self.ttk;r=self.root;r.title("Interlock Response Timing Collection");r.geometry("960x850");r.minsize(840,720);o=t.Frame(r,padding=14);o.pack(fill="both",expand=True)
        t.Label(o,text="Interlock Response Timing Collection",font=("Segoe UI",16,"bold")).pack(anchor="w");t.Label(o,text="Capture the falling 25 V interlock edge and resulting RF shutdown at each requested power.").pack(anchor="w",pady=(2,10))
        settings=t.LabelFrame(o,text="Run Settings",padding=10);settings.pack(fill="x");settings.columnconfigure(1,weight=1)
        for row,(label,var) in enumerate((("Rated power (W)",self.rated),("Power levels (W)",self.powers),("Cycles per power",self.cycles),("Results parent folder",self.parent))):
            t.Label(settings,text=label).grid(row=row,column=0,sticky="w",pady=4);t.Entry(settings,textvariable=var).grid(row=row,column=1,sticky="ew",padx=8)
        t.Button(settings,text="Browse...",command=self._browse).grid(row=3,column=2)
        equipment=t.LabelFrame(o,text="Equipment Addresses",padding=10);equipment.pack(fill="x",pady=10);equipment.columnconfigure(1,weight=1)
        for row,(label,var) in enumerate((("DUT IP / hostname",self.dut),("Oscilloscope VISA address / IP",self.scope),("Waveform generator VISA address / IP",self.wavegen),("BK Precision 9141 VISA address / IP",self.supply))):
            t.Label(equipment,text=label).grid(row=row,column=0,sticky="w",pady=4);t.Entry(equipment,textvariable=var).grid(row=row,column=1,sticky="ew",padx=8)
        t.Label(equipment,text="Defaults are inherited from the active unified collection bench profile and may be overridden for this run.",foreground="#555555").grid(row=4,column=0,columnspan=2,sticky="w")
        wiring=t.LabelFrame(o,text="Required Connections",padding=10);wiring.pack(fill="x")
        t.Label(wiring,justify="left",wraplength=880,text="Waveform generator CH1 to setpoint pins 5/18 and CH2 to RF Enable pins 4/17; both outputs use High-Z load. BK 9141 HI to pin 23 and LO to chassis, programmed for 25 V with 0.5 A limit. Scope CH1 from pin 23 to chassis, 1 Mohm, falling trigger near 15 V. Scope CH2 to the approved RF waveform monitor, 50 ohm. Verify load, cooling, interlocks, grounding, and emergency shutdown access.").pack(anchor="w")
        self.start=t.Button(o,text="START INTERLOCK RESPONSE TIMING COLLECTION",command=self._start);self.start.pack(fill="x",ipady=10,pady=10)
        box=t.LabelFrame(o,text="Progress",padding=10);box.pack(fill="both",expand=True);self.bar=t.Progressbar(box,mode="indeterminate");self.bar.pack(fill="x");self.log=self.tk.Text(box,height=11,state="disabled",wrap="word");self.log.pack(fill="both",expand=True,pady=(8,0))
        footer=t.Frame(o);footer.pack(fill="x",pady=(10,0));t.Label(footer,textvariable=self.status).pack(side="left");self.close=t.Button(footer,text="Close",command=self._close);self.close.pack(side="right");r.protocol("WM_DELETE_WINDOW",self._close)
    def _browse(self):
        from tkinter import filedialog
        value=filedialog.askdirectory(parent=self.root,initialdir=self.parent.get() or str(Path.home()))
        if value:self.parent.set(value)
    def _say(self,message):self.log.configure(state="normal");self.log.insert("end",message+"\n");self.log.see("end");self.log.configure(state="disabled")
    def _start(self):
        from tkinter import messagebox
        try:rated=float(self.rated.get());powers=parse_power_levels(self.powers.get());cycles=int(self.cycles.get());validate_settings(rated,powers,cycles)
        except Exception as exc:messagebox.showerror("Invalid settings",str(exc),parent=self.root);return
        parent=Path(self.parent.get().strip()).expanduser();addresses=[self.dut.get().strip(),self.scope.get().strip(),self.wavegen.get().strip(),self.supply.get().strip()]
        if not parent.is_dir() or any(not address for address in addresses):messagebox.showerror("Invalid setup","Select an existing results folder and enter all four equipment addresses.",parent=self.root);return
        expected=len(powers)*cycles
        if not messagebox.askyesno("Start interlock test",f"Run {cycles} cycle(s) at {len(powers)} power level(s), totaling {expected} interlock-drop acquisitions?\n\nThis safety-critical test removes the interlock while RF is operating.",parent=self.root):return
        self.running=True;self.start.configure(state="disabled");self.close.configure(state="disabled");self.bar.start(10);self.status.set("Running")
        def worker():
            try:code,folder=run_collection(parent,*addresses,rated,powers,cycles,lambda msg:self.root.after(0,lambda x=msg:self._say(x)));error=None
            except Exception as exc:traceback.print_exc();code=1;folder=None;error=exc
            self.root.after(0,lambda:self._finish(code,folder,error))
        threading.Thread(target=worker,daemon=True).start()
    def _finish(self,code,folder,error):
        from tkinter import messagebox
        self.running=False;self.exit_code=code;self.bar.stop();self.start.configure(state="normal");self.close.configure(state="normal")
        if code==0:self.status.set("Completed successfully");messagebox.showinfo("Collection complete",f"Results saved under:\n{folder}",parent=self.root)
        else:self.status.set("Collection failed");self._say(f"ERROR: {error}");messagebox.showerror("Collection failed",str(error),parent=self.root)
    def _close(self):
        from tkinter import messagebox
        if self.running:messagebox.showwarning("Collection running","Wait for collection cleanup to finish before closing.",parent=self.root);return
        self.root.destroy()


def main():
    import tkinter as tk
    root=tk.Tk();app=CollectionGUI(root);root.mainloop();return app.exit_code

if __name__=="__main__":raise SystemExit(main())
