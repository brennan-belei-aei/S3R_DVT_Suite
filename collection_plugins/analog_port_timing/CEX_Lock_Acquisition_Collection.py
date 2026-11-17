"""CEX Lock Acquisition data collection GUI.

For each manually established analog setpoint, the script verifies RF-off and
CEX-unlocked state, enables RF, arms the oscilloscope, then applies a 13.56 MHz
CEX reference. Scope CH1 captures the CEX reference, CH2 the raw RF waveform,
and CH3 the active-low CEX LOCK pull-up. All channels are transferred directly
to the laptop over Ethernet.
"""
from __future__ import annotations
import csv,json,math,os,re,sys,threading,time,traceback
from dataclasses import dataclass,field
from datetime import datetime
from pathlib import Path
from typing import Any,Callable,Iterable,Optional
import numpy as np
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.output_folders import create_unique_folder, path_component_strict  # noqa: E402
import suite_common.bootstrap  # noqa: F401
from suite_common.bench import bench_default,visa_socket_resource
from suite_instruments.factory import InstrumentFactory
from suite_instruments.transport.visa import resource_manager

TEST_NAME="CEX Lock Acquisition"
TEST_ID="CEX_LOCK_ACQUISITION"
CONTRACT_NAME="Paramount.CEXLockAcquisition"
CONTRACT_VERSION="1.0.0"
DUT_ADDRESS_DEFAULT=bench_default("generator")
SCOPE_ADDRESS_DEFAULT=bench_default("oscilloscope")
WAVEGEN_ADDRESS_DEFAULT=bench_default("waveform_generator")
DEFAULT_RATED_POWER_W=3000.0
DEFAULT_POWER_LEVELS_W=[500.0,1500.0,3000.0]
DEFAULT_ACQUISITIONS_PER_POWER=10
MAX_ACQUISITIONS=100
SETPOINT_FULL_SCALE_V=10.0
RF_ENABLE_WG_CHANNEL=1
CEX_REFERENCE_WG_CHANNEL=2
RF_ENABLE_LOW_V=0.0
RF_ENABLE_HIGH_V=6.0
CEX_REFERENCE_FREQ_HZ=13_560_000.0
CEX_REFERENCE_VPP=2.0
CEX_REFERENCE_OFFSET_V=0.0
CEX_REFERENCE_OBSERVED_SCOPE_VPP_APPROX=1.18
RF_OFF_SETTLE_S=1.0
RF_ON_SETTLE_S=1.0
CEX_UNLOCK_DWELL_S=1.0
CEX_LOCK_DWELL_S=1.0
REQUIRE_CONFIRMED_UNLOCK=True
REQUIRE_CONFIRMED_LOCK=True
OPERATING_FREQUENCY_KHZ=13_560
HOST_MODE=2
USER_PORT_MODE=4
HALO_SETPOINT_RESOLUTION=10
COMMAND_SETTLE_S=.25
ACQUISITION_TIMEOUT_S=10.0
INSTRUMENT_TIMEOUT_MS=30_000
WAVEFORM_TRANSFER_TIMEOUT_MS=120_000
WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES=25*1024*1024
SCOPE_TIME_PER_DIV_S=200e-3
SCOPE_HORIZONTAL_POSITION_PERCENT=35.0
SCOPE_RECORD_LENGTH=100_000
SCOPE_ARM_S=.35
SCOPE_CONFIG={
 "trigger_ch":1,"trigger_level":.25,"slope":"RISE","trigger_coupling":"DC","save_channels":(1,2,3),
 "scale":{1:.2,2:1.0,3:1.0},"position":{1:0.0,2:0.0,3:0.0},"offset":{1:0.0,2:0.0,3:2.5},
 "termination":{1:1_000_000.0,2:50.0,3:1_000_000.0},
 "labels":{1:"CEX_13p56MHz_Reference",2:"RF_Waveform_Monitor",3:"CEX_LOCK_Active_Low_Pullup"}}
T1_DEFINITION="Scope CH1 rolling-RMS activity rising 90% crossing"
T2_DEFINITION="Scope CH3 falling 90% level, first 10% of drop toward 0 V"


def safe(value,fallback):
 return path_component_strict(value, fallback)

def parse_powers(text):
 values=[float(x) for x in re.split(r"[,;\s]+",text.strip()) if x]
 if not values:raise ValueError("Enter at least one power level.")
 if any(not math.isfinite(x) for x in values) or len(set(values))!=len(values):raise ValueError("Power levels must be finite and unique.")
 return values

def validate(rated,powers,count):
 if rated<=0 or not math.isfinite(rated):raise ValueError("Rated power must be positive.")
 if not 1<=count<=MAX_ACQUISITIONS:raise ValueError(f"Acquisitions must be 1 through {MAX_ACQUISITIONS}.")
 for power in powers:
  if not 0<power<=rated:raise ValueError(f"Power {power:g} W is outside 0 to {rated:g} W.")

def unique_folder(parent,dut):
 stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
 return create_unique_folder(parent, f"{stamp} - {TEST_NAME} - {safe(dut, 'Generator')}")

@dataclass
class RunContext:
 run_dir:Path;run_id:str=field(default_factory=lambda:datetime.now().strftime("%Y%m%d_%H%M%S_%f"));config:list=field(default_factory=list);captures:list=field(default_factory=list);states:list=field(default_factory=list);events:list=field(default_factory=list)
 def _stamp(self,row):return {"Timestamp":datetime.now().isoformat(timespec="milliseconds"),"Contract_Name":CONTRACT_NAME,"Contract_Version":CONTRACT_VERSION,"Test_ID":TEST_ID,"Run_ID":self.run_id,**row}
 def _write(self,name,rows):
  if not rows:return
  fields=[]
  for row in rows:
   for key in row:
    if key not in fields:fields.append(key)
  target=self.run_dir/name;tmp=target.with_suffix(target.suffix+".tmp")
  with tmp.open("w",newline="",encoding="utf-8-sig") as f:w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(rows)
  os.replace(tmp,target)
 def add_config(self,**row):self.config.append(self._stamp(row));self._write("Run_Configuration.csv",self.config)
 def add_capture(self,**row):self.captures.append(self._stamp(row));self._write("Capture_Results.csv",self.captures)
 def add_state(self,**row):self.states.append(self._stamp(row));self._write("Lock_State_Verification.csv",self.states)
 def add_event(self,severity,event,**row):self.events.append(self._stamp({"Severity":severity,"Event":event,**row}));self._write("Event_Log.csv",self.events)
 def contract(self):
  data={"contract_name":CONTRACT_NAME,"contract_version":CONTRACT_VERSION,"test_id":TEST_ID,"test_name":TEST_NAME,"waveforms_are_transferred_over_ethernet":True,"waveform_channels":{"CH1":"13.56 MHz CEX reference","CH2":"Raw RF waveform monitor","CH3":"Active-low CEX LOCK pull-up"},"capture_key":["Run_ID","Requested_Power_W","Acquisition"],"T1_definition":T1_DEFINITION,"T2_definition":T2_DEFINITION}
  (self.run_dir/"Output_Contract.json").write_text(json.dumps(data,indent=2),encoding="utf-8")
 def manifest(self,status,expected,**extra):
  data={"contract_name":CONTRACT_NAME,"contract_version":CONTRACT_VERSION,"run_id":self.run_id,"status":status,"updated":datetime.now().isoformat(timespec="seconds"),"expected_captures":expected,"recorded_captures":len(self.captures),"state_verifications":len(self.states),"files":sorted(p.name for p in self.run_dir.iterdir() if p.is_file()),**extra}
  (self.run_dir/"Run_Manifest.json").write_text(json.dumps(data,indent=2),encoding="utf-8")

class Instruments:
 def __init__(self,dut,scope,wg):self.dut_address=dut.strip();self.scope_address=scope.strip();self.wg_address=wg.strip();self.wg_fallback=visa_socket_resource(self.wg_address);self.rm=self.scope=self.wg=self.dut=None
 def connect(self):
  self.rm=resource_manager();factory=InstrumentFactory({"generator":self.dut_address,"oscilloscope":self.scope_address,"waveform_generator":self.wg_address})
  self.scope=factory.create("oscilloscope",resource_manager=self.rm,open_attempts=1);self.scope.timeout=INSTRUMENT_TIMEOUT_MS;self.scope.read_termination="\n";self.scope.write_termination="\n";self.scope.chunk_size=WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES;print("Scope:",self.scope.query("*IDN?").strip())
  errors=[]
  for address in dict.fromkeys((self.wg_address,self.wg_fallback)):
   try:
    item=factory.create("waveform_generator",address=address,resource_manager=self.rm,open_attempts=1);item.timeout=INSTRUMENT_TIMEOUT_MS
    if str(address).endswith("::SOCKET"):item.read_termination="\n";item.write_termination="\n"
    print("Wavegen:",item.query("*IDN?").strip());self.wg=item;break
   except Exception as exc:errors.append(f"{address}: {exc}")
  if self.wg is None:raise RuntimeError("Wavegen connection failed. "+" | ".join(errors))
  self.dut=factory.create("generator");print("DUT connected.")
 def close(self):
  try:
   if self.wg is not None:
    set_dc(self.wg,RF_ENABLE_WG_CHANNEL,RF_ENABLE_LOW_V,True);self.wg.write(f"C{CEX_REFERENCE_WG_CHANNEL}:OUTP OFF")
  except Exception as exc:print("Wavegen cleanup warning:",exc)
  try:
   if self.dut is not None:self.dut.setActiveControlMode(HOST_MODE);time.sleep(COMMAND_SETTLE_S);self.dut.rfOFF()
  except Exception as exc:print("DUT cleanup warning:",exc)
  try:
   if self.scope is not None:self.scope.write("ACQ:STATE OFF")
  except Exception:pass
  for item in (self.scope,self.wg):
   try:
    if item is not None:item.close()
   except Exception:pass
  try:
   if self.dut is not None:self.dut.close()
  except Exception:pass
  try:
   if self.rm is not None:self.rm.close()
  except Exception:pass

def set_dc(wg,ch,v,on=True):wg.write(f"C{ch}:BSWV WVTP,DC");wg.write(f"C{ch}:BSWV OFST,{float(v):.6f}");wg.write(f"C{ch}:OUTP {'ON' if on else 'OFF'}")
def configure_sine(wg,ch,freq,vpp,offset):wg.write(f"C{ch}:OUTP OFF");wg.write(f"C{ch}:BSWV WVTP,SINE");wg.write(f"C{ch}:BSWV FRQ,{freq:.6f}");wg.write(f"C{ch}:BSWV AMP,{vpp:.6f}");wg.write(f"C{ch}:BSWV OFST,{offset:.6f}")
def set_host(dut,power):dut.setActiveControlMode(HOST_MODE);time.sleep(COMMAND_SETTLE_S);dut.rfOFF();dut.setFixedFrequency(OPERATING_FREQUENCY_KHZ);dut.setFrequencyMode(0);dut.setPulsingFrequency(0);dut.setPowerSetpoint(int(round(power*HALO_SETPOINT_RESOLUTION)))
def enter_user(dut):dut.setActiveControlMode(USER_PORT_MODE);time.sleep(COMMAND_SETTLE_S)
def cex_locked(dut):
 status=dut.reportProcessStatus();return status[3][0]=="1" if isinstance(status[3],str) else bool(int(status[3])&0x80)
def wait_until(predicate,timeout,poll=.05):
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  if predicate():return True
  time.sleep(poll)
 return False

def prepare(inst,rated):
 set_dc(inst.wg,RF_ENABLE_WG_CHANNEL,RF_ENABLE_LOW_V,False);configure_sine(inst.wg,CEX_REFERENCE_WG_CHANNEL,CEX_REFERENCE_FREQ_HZ,CEX_REFERENCE_VPP,CEX_REFERENCE_OFFSET_V);set_host(inst.dut,rated);enter_user(inst.dut);set_dc(inst.wg,RF_ENABLE_WG_CHANNEL,RF_ENABLE_LOW_V,True);inst.wg.write(f"C{CEX_REFERENCE_WG_CHANNEL}:OUTP OFF");time.sleep(RF_OFF_SETTLE_S)
def termination_50(text):
 text=text.strip().upper().replace("OHM","")
 try:return text=="FIFTY" or 45<=float(text)<=55
 except ValueError:return False
def set_term(scope,ch,value):
 commands=[f"CH{ch}:TERMINATION FIFTY",f"CH{ch}:TERMINATION 50"] if value==50 else [f"CH{ch}:TERMINATION MEG",f"CH{ch}:TERMINATION {value:g}"]
 for command in commands:
  try:
   scope.write(command);scope.query("*OPC?");response=scope.query(f"CH{ch}:TERMINATION?").strip()
   if termination_50(response) if value==50 else not termination_50(response):return
  except Exception:pass
 raise RuntimeError(f"Unable to verify CH{ch} termination.")
def configure_scope(scope):
 scope.write("RECALL:SETUP DEFAULT");time.sleep(1.5);scope.write("*CLS");scope.write("ACQ:STATE OFF")
 for ch in range(1,5):
  if ch in SCOPE_CONFIG["save_channels"]:
   scope.write(f"SEL:CH{ch} ON");scope.write(f"CH{ch}:COUP DC");set_term(scope,ch,SCOPE_CONFIG["termination"][ch]);scope.write(f"CH{ch}:SCA {SCOPE_CONFIG['scale'][ch]}");scope.write(f"CH{ch}:POS {SCOPE_CONFIG['position'][ch]}");scope.write(f"CH{ch}:OFFSET {SCOPE_CONFIG['offset'][ch]}")
  else:scope.write(f"SEL:CH{ch} OFF")
 scope.write(f"HOR:SCALE {SCOPE_TIME_PER_DIV_S}");scope.write("HORIZONTAL:DELAY:MODE OFF");scope.write(f"HORIZONTAL:POSITION {SCOPE_HORIZONTAL_POSITION_PERCENT}");scope.write(f"HORIZONTAL:RECORDLENGTH {SCOPE_RECORD_LENGTH}");scope.write("TRIG:A:EDGE:SOU CH1");scope.write(f"TRIG:A:LEV:CH1 {SCOPE_CONFIG['trigger_level']}");scope.write(f"TRIG:A:EDGE:SLO {SCOPE_CONFIG['slope']}");scope.write("TRIG:A:EDGE:COUP DC");scope.write("ACQ:STOPA SEQ")
def arm(scope):scope.write("*CLS");scope.write("ACQ:STOPA SEQ");scope.write("ACQ:STATE ON");time.sleep(SCOPE_ARM_S)
def wait_scope(scope):
 deadline=time.monotonic()+ACQUISITION_TIMEOUT_S
 while time.monotonic()<deadline:
  if scope.query("TRIG:STATE?").strip().upper() in {"SAVE","STOP"}:return True
  time.sleep(.05)
 return False
def transfer(scope,ch,path):
 partial=path.with_suffix(path.suffix+".partial");original=scope.timeout
 try:
  scope.timeout=WAVEFORM_TRANSFER_TIMEOUT_MS;scope.write("ACQUIRE:STATE STOP");scope.write("*WAI");scope.write(f"DATA:SOURCE CH{ch}");scope.write("DATA:ENCDG RIBINARY");scope.write("DATA:WIDTH 2");count=int(float(scope.query("HORIZONTAL:RECORDLENGTH?")));scope.write("DATA:START 1");scope.write(f"DATA:STOP {count}")
  xincr=float(scope.query("WFMOUTPRE:XINCR?"));xzero=float(scope.query("WFMOUTPRE:XZERO?"));pt=float(scope.query("WFMOUTPRE:PT_OFF?"));ym=float(scope.query("WFMOUTPRE:YMULT?"));yz=float(scope.query("WFMOUTPRE:YZERO?"));yo=float(scope.query("WFMOUTPRE:YOFF?"));raw=np.asarray(scope.query_binary_values("CURVE?",datatype="h",is_big_endian=True,container=np.array,expect_termination=False),float)
  if raw.size==0:raise RuntimeError(f"No CH{ch} waveform returned.")
  idx=np.arange(raw.size,dtype=float);times=xzero+(idx-pt)*xincr;values=(raw-yo)*ym+yz;np.savetxt(partial,np.column_stack((times,values)),delimiter=",",header=f"Time (s),CH{ch} (V)",comments="",fmt="%.12e");partial.replace(path);return {"Path":str(path),"Points":int(raw.size),"Sample_Interval_s":xincr}
 except Exception:
  if partial.exists():partial.unlink()
  raise
 finally:scope.timeout=original

def capture(inst,ctx,power,setpoint,acquisition):
 descriptor=f"{power:g}W_Acquisition{acquisition}_CEX_Acquire";configure_scope(inst.scope);arm(inst.scope);inst.wg.write(f"C{CEX_REFERENCE_WG_CHANNEL}:OUTP ON");captured=wait_scope(inst.scope);final=inst.scope.query("TRIG:STATE?").strip().upper();files={}
 if captured:
  for ch in SCOPE_CONFIG["save_channels"]:
   stamp=datetime.now().strftime("%Y%m%d_%H%M%S_%f");files[ch]=transfer(inst.scope,ch,ctx.run_dir/f"{TEST_ID}_{descriptor}_CH{ch}_{stamp}.csv")
 ctx.add_capture(Capture_ID=descriptor,Requested_Power_W=power,Manual_Setpoint_V=setpoint,Acquisition=acquisition,Triggered=captured,Trigger_Level_V=SCOPE_CONFIG["trigger_level"],Trigger_Slope=SCOPE_CONFIG["slope"],Final_Trigger_State=final,CH1_File=files.get(1,{}).get("Path"),CH2_File=files.get(2,{}).get("Path"),CH3_File=files.get(3,{}).get("Path"),CH1_Points=files.get(1,{}).get("Points"),CH2_Points=files.get(2,{}).get("Points"),CH3_Points=files.get(3,{}).get("Points"));return captured

def run_collection(parent,dut,scope,wg,rated,powers,count,confirm_setpoint:Callable[[float,float],bool],progress):
 validate(rated,powers,count);run_dir=unique_folder(parent,dut);ctx=RunContext(run_dir);ctx.contract();expected=len(powers)*count;ctx.add_config(Configuration="Run",DUT_Address=dut,Scope_Address=scope,Wavegen_Address=wg,Rated_Power_W=rated,Power_Levels_W=str(powers),Acquisitions_Per_Power=count,Setpoint_Control="External DC supply, manually changed per power point",CEX_Reference_Frequency_Hz=CEX_REFERENCE_FREQ_HZ,CEX_Reference_Commanded_Vpp=CEX_REFERENCE_VPP,CEX_Reference_Observed_Scope_Vpp_Approx=CEX_REFERENCE_OBSERVED_SCOPE_VPP_APPROX,T1_Definition=T1_DEFINITION,T2_Definition=T2_DEFINITION);ctx.manifest("initialized",expected);inst=Instruments(dut,scope,wg);status="aborted"
 try:
  inst.connect();prepare(inst,rated)
  for power in powers:
   setpoint=power/rated*SETPOINT_FULL_SCALE_V;set_dc(inst.wg,RF_ENABLE_WG_CHANNEL,RF_ENABLE_LOW_V,True);inst.wg.write(f"C{CEX_REFERENCE_WG_CHANNEL}:OUTP OFF");time.sleep(RF_OFF_SETTLE_S)
   if not confirm_setpoint(power,setpoint):raise RuntimeError(f"Operator cancelled before confirming {power:g} W setpoint.")
   ctx.add_config(Configuration="Power Point",Requested_Power_W=power,Manual_Setpoint_V=setpoint,Acquisitions=count)
   for acquisition in range(1,count+1):
    progress(f"{power:g} W, acquisition {acquisition}/{count}: establishing unlocked state")
    set_dc(inst.wg,RF_ENABLE_WG_CHANNEL,RF_ENABLE_LOW_V,True);inst.wg.write(f"C{CEX_REFERENCE_WG_CHANNEL}:OUTP OFF");time.sleep(max(RF_OFF_SETTLE_S,CEX_UNLOCK_DWELL_S));unlocked=not cex_locked(inst.dut)
    if REQUIRE_CONFIRMED_UNLOCK and not unlocked:raise RuntimeError(f"DUT remained CEX locked before acquisition {acquisition} at {power:g} W.")
    set_dc(inst.wg,RF_ENABLE_WG_CHANNEL,RF_ENABLE_HIGH_V,True);time.sleep(RF_ON_SETTLE_S);captured=capture(inst,ctx,power,setpoint,acquisition);locked=wait_until(lambda:cex_locked(inst.dut),ACQUISITION_TIMEOUT_S)
    ctx.add_state(Requested_Power_W=power,Manual_Setpoint_V=setpoint,Acquisition=acquisition,Scope_Captured=captured,Unlocked_Before=unlocked,Locked_After=locked,T1_Definition=T1_DEFINITION,T2_Definition=T2_DEFINITION)
    if REQUIRE_CONFIRMED_LOCK and not locked:raise RuntimeError(f"DUT did not report CEX lock after acquisition {acquisition} at {power:g} W.")
    time.sleep(CEX_LOCK_DWELL_S);set_dc(inst.wg,RF_ENABLE_WG_CHANNEL,RF_ENABLE_LOW_V,True);inst.wg.write(f"C{CEX_REFERENCE_WG_CHANNEL}:OUTP OFF");time.sleep(RF_OFF_SETTLE_S);ctx.manifest("running",expected,current_power_w=power,current_acquisition=acquisition)
  status="completed";return 0,run_dir
 finally:inst.close();ctx.manifest(status,expected)

class GUI:
 def __init__(self,root):
  import tkinter as tk
  from tkinter import ttk
  self.root=root;self.tk=tk;self.ttk=ttk;self.running=False;self.exit_code=2;self.parent=tk.StringVar(value=os.environ.get("PARAMOUNT_COLLECTION_PARENT_FOLDER","") or str(Path.home()));self.dut=tk.StringVar(value=DUT_ADDRESS_DEFAULT or "");self.scope=tk.StringVar(value=SCOPE_ADDRESS_DEFAULT or "");self.wg=tk.StringVar(value=WAVEGEN_ADDRESS_DEFAULT or "");self.rated=tk.StringVar(value=f"{DEFAULT_RATED_POWER_W:g}");self.powers=tk.StringVar(value=", ".join(f"{x:g}" for x in DEFAULT_POWER_LEVELS_W));self.count=tk.StringVar(value=str(DEFAULT_ACQUISITIONS_PER_POWER));self.status=tk.StringVar(value="Ready");self._build()
 def _build(self):
  t=self.ttk;r=self.root;r.title("CEX Lock Acquisition Collection");r.geometry("960x840");r.minsize(840,720);o=t.Frame(r,padding=14);o.pack(fill="both",expand=True);t.Label(o,text="CEX Lock Acquisition Collection",font=("Segoe UI",16,"bold")).pack(anchor="w");t.Label(o,text="Apply the CEX reference and capture reference activity, RF waveform, and active-low lock response.").pack(anchor="w",pady=(2,10))
  s=t.LabelFrame(o,text="Run Settings",padding=10);s.pack(fill="x");s.columnconfigure(1,weight=1)
  for row,(label,var) in enumerate((("Rated power (W)",self.rated),("Power levels (W)",self.powers),("Acquisitions per power",self.count),("Results parent folder",self.parent))):t.Label(s,text=label).grid(row=row,column=0,sticky="w",pady=4);t.Entry(s,textvariable=var).grid(row=row,column=1,sticky="ew",padx=8)
  t.Button(s,text="Browse...",command=self._browse).grid(row=3,column=2)
  e=t.LabelFrame(o,text="Equipment Addresses",padding=10);e.pack(fill="x",pady=10);e.columnconfigure(1,weight=1)
  for row,(label,var) in enumerate((("DUT IP / hostname",self.dut),("Oscilloscope VISA address / IP",self.scope),("Waveform generator VISA address / IP",self.wg))):t.Label(e,text=label).grid(row=row,column=0,sticky="w",pady=4);t.Entry(e,textvariable=var).grid(row=row,column=1,sticky="ew",padx=8)
  t.Label(e,text="Defaults are inherited from the unified collection bench profile and remain editable.",foreground="#555555").grid(row=3,column=0,columnspan=2,sticky="w")
  wiring=t.LabelFrame(o,text="Required Connections",padding=10);wiring.pack(fill="x");t.Label(wiring,justify="left",wraplength=880,text="External DC setpoint supply to pins 5/18. Waveform-generator CH1 to RF Enable pins 4/17. CH2 to the approved CEX reference-input interface and Scope CH1 through an appropriate tee/high-impedance monitor. Scope CH2 to the approved RF waveform monitor at 50 ohm. Scope CH3 across active-low CEX LOCK pin 12 and return pin 25. Verify electrical interface, pull-up, attenuation, grounding, load, cooling, and emergency shutdown access.").pack(anchor="w")
  self.start=t.Button(o,text="START CEX LOCK ACQUISITION COLLECTION",command=self._start);self.start.pack(fill="x",ipady=10,pady=10);p=t.LabelFrame(o,text="Progress",padding=10);p.pack(fill="both",expand=True);self.bar=t.Progressbar(p,mode="indeterminate");self.bar.pack(fill="x");self.log=self.tk.Text(p,height=11,state="disabled",wrap="word");self.log.pack(fill="both",expand=True,pady=(8,0));f=t.Frame(o);f.pack(fill="x",pady=(10,0));t.Label(f,textvariable=self.status).pack(side="left");self.close=t.Button(f,text="Close",command=self._close);self.close.pack(side="right");r.protocol("WM_DELETE_WINDOW",self._close)
 def _browse(self):
  from tkinter import filedialog
  value=filedialog.askdirectory(parent=self.root,initialdir=self.parent.get() or str(Path.home()))
  if value:self.parent.set(value)
 def _say(self,msg):self.log.configure(state="normal");self.log.insert("end",msg+"\n");self.log.see("end");self.log.configure(state="disabled")
 def _confirm_setpoint(self,power,voltage):
  from tkinter import messagebox
  event=threading.Event();answer={"value":False}
  def ask():answer["value"]=messagebox.askokcancel("Manual setpoint confirmation",f"Set the external DC setpoint supply to {voltage:.6f} V for {power:g} W.\n\nVerify the voltage at DUT pins 5/18, then select OK.",parent=self.root);event.set()
  self.root.after(0,ask);event.wait();return answer["value"]
 def _start(self):
  from tkinter import messagebox
  try:rated=float(self.rated.get());powers=parse_powers(self.powers.get());count=int(self.count.get());validate(rated,powers,count)
  except Exception as exc:messagebox.showerror("Invalid settings",str(exc),parent=self.root);return
  parent=Path(self.parent.get().strip()).expanduser();addresses=[self.dut.get().strip(),self.scope.get().strip(),self.wg.get().strip()]
  if not parent.is_dir() or any(not x for x in addresses):messagebox.showerror("Invalid setup","Select an existing results folder and enter all equipment addresses.",parent=self.root);return
  expected=len(powers)*count
  if not messagebox.askyesno("Start CEX acquisition",f"Run {count} acquisition(s) at {len(powers)} power level(s), totaling {expected} captures?\n\nYou will be prompted to set and verify the external DC setpoint at each power.",parent=self.root):return
  self.running=True;self.start.configure(state="disabled");self.close.configure(state="disabled");self.bar.start(10);self.status.set("Running")
  def worker():
   try:code,folder=run_collection(parent,*addresses,rated,powers,count,self._confirm_setpoint,lambda m:self.root.after(0,lambda x=m:self._say(x)));error=None
   except Exception as exc:traceback.print_exc();code=1;folder=None;error=exc
   self.root.after(0,lambda:self._finish(code,folder,error))
  threading.Thread(target=worker,daemon=True).start()
 def _finish(self,code,folder,error):
  from tkinter import messagebox
  self.running=False;self.exit_code=code;self.bar.stop();self.start.configure(state="normal");self.close.configure(state="normal")
  if code==0:self.status.set("Completed successfully");messagebox.showinfo("Collection complete",f"Results saved under:\n{folder}\n\nReturn the external setpoint supply to 0 V.",parent=self.root)
  else:self.status.set("Collection failed");self._say(f"ERROR: {error}");messagebox.showerror("Collection failed",str(error),parent=self.root)
 def _close(self):
  from tkinter import messagebox
  if self.running:messagebox.showwarning("Collection running","Wait for cleanup to finish before closing.",parent=self.root);return
  self.root.destroy()

def main():
 import tkinter as tk
 root=tk.Tk();app=GUI(root);root.mainloop();return app.exit_code
if __name__=="__main__":raise SystemExit(main())
