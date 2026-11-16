"""RF ramp verification data collection GUI for the Paramount transparency suite.

Dependencies: pyvisa, numpy, and the local AE_Commands_PMNT_HF_1XX.py library.
The script is standalone and also consumes PARAMOUNT_* values from the unified launcher.
"""
from __future__ import annotations
import csv, json, os, re, sys, threading, time, traceback
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "RF Ramp Verification - Data Collection"
APP_DIR = Path(__file__).resolve().parent
SETTINGS = APP_DIR / "RF_Ramp_Collection_Settings.json"
TEST_NAME = "RF Ramp Verification"
GENERATOR_MODULE = "AE_Commands_PMNT_HF_1XX"
DEFAULT_POWER_LEVELS = "50, 1000, 2000, 3000"
DEFAULT_RAMP_TIMES = "200, 500, 1000, 2000, 3000, 5000"

@dataclass(frozen=True)
class Condition:
    high_w: float
    ramp_ms: float
    direction: str
    repetition: int


def safe_component(value):
    return re.sub(r'[<>:"/\\|?*]+', '_', str(value)).strip().rstrip('.') or "Unnamed"

def unique_run_folder(parent: Path, product: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base = f"{stamp} - {TEST_NAME} - {safe_component(product)}"
    candidate = parent / base
    run = 2
    while candidate.exists():
        candidate = parent / f"{base} - Run {run}"
        run += 1
    candidate.mkdir(parents=False, exist_ok=False)
    return candidate

def parse_transitions(text):
    out=[]
    for item in re.split(r'[;\n]+', text.strip()):
        if not item.strip(): continue
        parts=re.split(r'\s*(?:->|,|:)\s*', item.strip())
        if len(parts)!=2: raise ValueError(f"Invalid transition: {item!r}. Use low->high.")
        lo,hi=map(float,parts)
        if lo < 0 or hi <= lo: raise ValueError(f"Transition must satisfy 0 <= low < high: {item}")
        if (lo,hi) not in out: out.append((lo,hi))
    if not out: raise ValueError("Enter at least one power transition.")
    return out

def parse_positive_list(text, label):
    vals=[]
    for item in re.split(r'[,;\s]+', text.strip()):
        if not item: continue
        value=float(item)
        if value <= 0: raise ValueError(f"{label} values must be positive.")
        if value not in vals: vals.append(value)
    if not vals: raise ValueError(f"Enter at least one {label} value.")
    return vals

def load_generator_module():
    """Import the AE command library from common test-suite locations."""
    import importlib
    module_filename = f"{GENERATOR_MODULE}.py"
    candidates = []
    env_file = os.getenv("PARAMOUNT_AE_COMMANDS_FILE", "").strip()
    if env_file:
        candidates.append(Path(env_file).expanduser())
    roots = [APP_DIR, Path.cwd()]
    suite_root = os.getenv("PARAMOUNT_SUITE_ROOT", "").strip()
    if suite_root:
        roots.append(Path(suite_root).expanduser())
    for root in roots:
        candidates.extend([
            root / module_filename,
            root / "suite_sdk" / module_filename,
            root / "libraries" / module_filename,
            root / "Library" / module_filename,
        ])
    # Also inspect parents of the script, which supports scripts kept in test subfolders.
    for parent in list(APP_DIR.parents)[:4]:
        candidates.extend([parent / module_filename, parent / "suite_sdk" / module_filename])
    found = next((c.resolve() for c in candidates if c.is_file()), None)
    # The plugin can be nested several levels below the repository. If the normal
    # locations fail, search the repository tree and the user's Documents tree.
    if found is None:
        search_roots = []
        for parent in [APP_DIR, *list(APP_DIR.parents)[:5]]:
            if parent.is_dir() and parent not in search_roots:
                search_roots.append(parent)
        documents = Path.home() / "Documents"
        if documents.is_dir() and documents not in search_roots:
            search_roots.append(documents)
        for root in search_roots:
            try:
                matches = list(root.rglob(module_filename))
            except (OSError, PermissionError):
                matches = []
            if matches:
                found = matches[0].resolve()
                break
    if found is None:
        checked = "\n".join(f"  - {c}" for c in dict.fromkeys(candidates))
        raise FileNotFoundError(
            f"Could not locate {module_filename}. Copy that file and its sibling "
            f"dependencies AE_Com_Module.py and fixedpoint.py into the same folder as "
            f"this collection script, or set PARAMOUNT_AE_COMMANDS_FILE to its full path.\n"
            f"Direct locations checked:\n{checked}\n"
            f"A recursive search of the repository parents and Documents folder also found no match."
        )
    # Add the library directory so its sibling dependencies, including AE_Com_Module,
    # can be imported by AE_Commands_PMNT_HF_1XX.py.
    library_dir = str(found.parent)
    if library_dir not in sys.path:
        sys.path.insert(0, library_dir)
    return importlib.import_module(GENERATOR_MODULE)

def visa_resource(address):
    address=address.strip()
    if '::' in address: return address
    return f"TCPIP0::{address}::inst0::INSTR"

def format_num(value):
    return f"{value:g}".replace('.', 'p')

def load_settings():
    try: return json.loads(SETTINGS.read_text(encoding='utf-8'))
    except Exception: return {}

def crossing_time(x, y, level, rising):
    import numpy as np
    if rising:
        idx=np.flatnonzero((y[:-1] < level) & (y[1:] >= level))
    else:
        idx=np.flatnonzero((y[:-1] > level) & (y[1:] <= level))
    if not len(idx): return None
    i=int(idx[0]); dy=y[i+1]-y[i]
    return float(x[i] if dy == 0 else x[i] + (level-y[i])*(x[i+1]-x[i])/dy)

def call_dut_with_interlock_retry(action, description, timeout_s=20.0, poll_s=0.25):
    """Retry a DUT command while the firmware minimum-off-time interlock is active."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            return action()
        except Exception as exc:
            text = str(exc).lower()
            minimum_off_active = "minimum off time active" in text or "minimum off time" in text
            if not minimum_off_active or time.monotonic() >= deadline:
                raise RuntimeError(f"{description} failed: {exc}") from exc
            time.sleep(poll_s)


def rf_state_is_on(value):
    """Normalize the different RF-state forms returned by AE command libraries."""
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    if isinstance(value, str):
        token = value.strip().upper()
        if token in {"1", "ON", "TRUE", "RF ON", "ENABLED"}:
            return True
        if token in {"0", "OFF", "FALSE", "RF OFF", "DISABLED", ""}:
            return False
        try:
            return bool(int(float(token)))
        except ValueError:
            raise ValueError(f"Unrecognized RF state returned by DUT: {value!r}")
    return bool(value)


def get_rf_state(dut):
    return rf_state_is_on(dut.isRFon())


def wait_for_rf_state(dut, expected_on, timeout_s=10.0):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = dut.isRFon()
        if rf_state_is_on(last) == expected_on:
            return last
        time.sleep(0.1)
    expected = "ON" if expected_on else "OFF"
    raise TimeoutError(f"DUT did not reach RF {expected}. Last isRFon() value: {last!r}")


def ensure_rf_off(dut):
    """Avoid sending a redundant RF-OFF command and verify the resulting state."""
    if get_rf_state(dut):
        call_dut_with_interlock_retry(dut.rfOFF, "RF OFF")
    wait_for_rf_state(dut, False)


def ensure_rf_on(dut):
    """Command RF on when needed and verify that the generator actually turned on."""
    if not get_rf_state(dut):
        call_dut_with_interlock_retry(dut.rfON, "RF ON")
    wait_for_rf_state(dut, True)

MONITOR_CALIBRATION = [(0.0, 0.0), (50.0, 0.075), (1000.0, 0.300), (2000.0, 0.425), (3000.0, 0.510)]

def expected_monitor_voltage(power_w):
    """Piecewise-linear estimate from experimentally observed CH1 50-ohm levels."""
    points = MONITOR_CALIBRATION
    if power_w <= points[0][0]: return points[0][1]
    for (p0,v0),(p1,v1) in zip(points, points[1:]):
        if power_w <= p1:
            return v0 + (power_w-p0)*(v1-v0)/(p1-p0)
    p0,v0=points[-2]; p1,v1=points[-1]
    return v1 + (power_w-p1)*(v1-v0)/(p1-p0)

def scope_seconds_per_div(ramp_s):
    # Experimental anchor: 0.5 to 2 s ramps work well at 400 ms/div.
    if ramp_s < 0.5: return 0.1
    if ramp_s <= 2.0: return 0.4
    if ramp_s <= 3.0: return 0.8
    return 1.0

class TekScope:
    def __init__(self, address, channel, timeout_ms=30000):
        import pyvisa
        self.rm=pyvisa.ResourceManager()
        self.inst=self.rm.open_resource(visa_resource(address))
        self.inst.timeout=timeout_ms
        self.inst.write_termination='\n'; self.inst.read_termination='\n'
        self.channel=channel.upper()
        self.idn=self.inst.query('*IDN?').strip()
    def write(self, command): self.inst.write(command)
    def query(self, command): return self.inst.query(command).strip()
    def configure(self, ramp_s, record_length, volts_per_div, trigger_level, direction, pretrigger_pct):
        sec_div=scope_seconds_per_div(ramp_s)
        window=sec_div*10.0
        self.write('ACQuire:STOPAfter SEQuence')
        self.write(f'SELect:{self.channel} ON')
        self.write(f'{self.channel}:TERmination 50')
        self.write(f'{self.channel}:SCAle {volts_per_div}')
        self.write(f'HORizontal:SCAle {sec_div}')
        self.write(f'HORizontal:RECOrdlength {record_length}')
        # Tek horizontal position is screen percent on supported 4/5/6 Series scopes.
        horizontal_position = 0.0 if direction == 'RISE' else 50.0
        self.write(f'HORizontal:POSition {horizontal_position}')
        self.write('TRIGger:A:TYPe EDGE')
        self.write('TRIGger:A:MODe NORMal')
        self.write(f'TRIGger:A:EDGE:SOUrce {self.channel}')
        self.write(f'TRIGger:A:EDGE:SLOpe {"RISe" if direction=="RISE" else "FALL"}')
        self.write(f'TRIGger:A:LEVel:{self.channel} {trigger_level}')
        return window,sec_div
    def trigger_state(self):
        try:
            return self.query('TRIGger:STATE?').strip().upper()
        except Exception:
            return 'UNKNOWN'
    def arm(self, timeout_s=5.0):
        self.write('ACQuire:STATE STOP')
        self.write('ACQuire:STOPAfter SEQuence')
        self.write('ACQuire:STATE RUN')
        end=time.monotonic()+timeout_s
        last='UNKNOWN'
        while time.monotonic()<end:
            last=self.trigger_state()
            # READY means armed; ARMED and AUTO are used by some Tektronix families.
            if any(token in last for token in ('READY','ARMED','AUTO')):
                return last
            time.sleep(0.05)
        raise TimeoutError(f"Oscilloscope did not arm. Last trigger state: {last}")
    def wait(self, timeout_s):
        end=time.monotonic()+timeout_s
        last_acq='UNKNOWN'; last_trigger='UNKNOWN'
        while time.monotonic()<end:
            try:
                last_acq=self.query('ACQuire:STATE?')
                last_trigger=self.trigger_state()
                # In single-sequence mode, acquisition off is the most portable
                # completion indication across Tektronix scope families.
                if int(float(last_acq)) == 0:
                    return last_trigger
            except Exception:
                pass
            time.sleep(0.05)
        raise TimeoutError(
            "Oscilloscope acquisition did not complete. "
            f"ACQuire:STATE?={last_acq}, TRIGger:STATE?={last_trigger}. "
            "The trigger level may not be between the actual low and high RF-monitor "
            "voltages, or the selected trigger channel/slope may be incorrect."
        )
    def clear_acquisition(self):
        """Stop acquisition and clear the previous waveform/display record."""
        self.write('ACQuire:STATE STOP')
        try:
            self.write('CLEAR')
        except Exception:
            # Some Tektronix families expose display clearing through a different
            # command; stopping and restarting still guarantees a fresh acquisition.
            pass
        # Do not query SYSTem:ERRor? here. On some Tektronix models an unsupported
        # error-queue query waits for the full VISA timeout and makes Start appear hung.
        time.sleep(0.15)

    def start_free_run(self):
        self.write('ACQuire:STATE STOP')
        self.write('ACQuire:STOPAfter RUNSTop')
        self.write('TRIGger:A:MODe AUTO')
        self.write('ACQuire:STATE RUN')
        time.sleep(0.2)
    def stop(self):
        self.write('ACQuire:STATE STOP')
        time.sleep(0.2)

    def waveform(self):
        import numpy as np
        self.write(f'DATa:SOUrce {self.channel}')
        self.write('DATa:ENCdg SRIBinary'); self.write('WFMOutpre:BYT_Nr 2')
        self.write('DATa:STARt 1')
        try: n=int(float(self.query('HORizontal:RECOrdlength?')))
        except Exception: n=100000
        self.write(f'DATa:STOP {n}')
        xincr=float(self.query('WFMOutpre:XINcr?')); xzero=float(self.query('WFMOutpre:XZEro?'))
        pt_off=float(self.query('WFMOutpre:PT_Off?')); ymult=float(self.query('WFMOutpre:YMUlt?'))
        yzero=float(self.query('WFMOutpre:YZEro?')); yoff=float(self.query('WFMOutpre:YOFf?'))
        try:
            byte_order=self.query('WFMOutpre:BYT_Or?').strip().upper()
        except Exception:
            byte_order='MSB'
        is_big_endian = byte_order.startswith('MSB') or byte_order.startswith('BIG')
        raw=self.inst.query_binary_values('CURVe?', datatype='h', is_big_endian=is_big_endian, container=np.array)
        y=(raw-yoff)*ymult+yzero
        x=xzero+(np.arange(len(y))-pt_off)*xincr
        return x,y,{'xincr_s':xincr,'xzero_s':xzero,'pt_off':pt_off,'ymult':ymult,'yzero':yzero,'yoff':yoff,'points':len(y),'waveform_byte_order':byte_order}
    def close(self):
        try: self.inst.close()
        finally: self.rm.close()

class App:
    def __init__(self, root):
        self.root=root; self.stop_event=threading.Event(); self.running=False
        cfg=load_settings()
        defaults={
          'generator':os.getenv('PARAMOUNT_GENERATOR_ADDRESS','PMNT046708'),
          'scope':os.getenv('PARAMOUNT_OSCILLOSCOPE_ADDRESS',''),
          'parent':os.getenv('PARAMOUNT_COLLECTION_PARENT_FOLDER',str(Path.home())),
          'product':os.getenv('PARAMOUNT_PRODUCT_NAME','Paramount'), 'power_levels':DEFAULT_POWER_LEVELS,
          'ramps':DEFAULT_RAMP_TIMES,'frequency_khz':'13560','channel':'CH1','volts_div':'AUTO',
          'trigger_low_v':'0.0','trigger_high_v':'0.075','record_length':'100000','pretrigger_pct':'20',
          'repetitions':'3','settle_s':'1.0','capture_pre_delay_pct':100.0,'capture_stop_delay_pct':100.0,'inter_condition_delay_s':1.0,'raw_per_ms':'1','ramp_mode':'2','reg_mode':'6',
          'measure_definition':'10_TO_90','rise':True,'fall':True,'rf_off_between':False
        }
        defaults.update({k:v for k,v in cfg.items() if k in defaults})
        # Delay sliders are percentages of the per-condition AUTO calculation.
        # Older absolute-second settings cannot map safely across multiple ramp times,
        # so upgraded installations begin at 100% (exactly AUTO).
        if 'capture_pre_delay_pct' not in cfg:
            defaults['capture_pre_delay_pct']=100.0
        if 'capture_stop_delay_pct' not in cfg:
            defaults['capture_stop_delay_pct']=100.0
        defaults['raw_per_ms']='1'; defaults['ramp_mode']='2'
        if 'power_levels' not in cfg: defaults['power_levels']=DEFAULT_POWER_LEVELS
        defaults.pop('transitions',None)
        self.v={k:(tk.BooleanVar(value=val) if isinstance(val,bool) else tk.StringVar(value=str(val))) for k,val in defaults.items()}
        self.v['capture_pre_delay_pct']=tk.DoubleVar(value=float(defaults['capture_pre_delay_pct']))
        self.v['capture_stop_delay_pct']=tk.DoubleVar(value=float(defaults['capture_stop_delay_pct']))
        self.v['inter_condition_delay_s']=tk.DoubleVar(value=float(defaults['inter_condition_delay_s']))
        self.status=tk.StringVar(value='Ready.'); self.progress=tk.DoubleVar(value=0)
        self.build()
    def build(self):
        r=self.root; r.title(APP_TITLE); r.geometry('980x820'); r.minsize(900,700)
        outer=ttk.Frame(r,padding=12); outer.pack(fill='both',expand=True)
        ttk.Label(outer,text=APP_TITLE,font=('Segoe UI',16,'bold')).pack(anchor='w')
        nb=ttk.Notebook(outer); nb.pack(fill='both',expand=True,pady=8)
        setup=ttk.Frame(nb,padding=12); matrix=ttk.Frame(nb,padding=12); advanced=ttk.Frame(nb,padding=12)
        nb.add(setup,text='Setup'); nb.add(matrix,text='Test Matrix'); nb.add(advanced,text='Advanced')
        self.form(setup,[('generator','Generator IP / hostname'),('scope','Tektronix address'),('parent','Output parent folder'),('product','Generator / product name'),('frequency_khz','RF frequency (kHz)'),('channel','RF monitor channel (fixed CH1)'),('volts_div','Scope volts/div (AUTO recommended)'),('trigger_low_v','Expected low monitor voltage'),('trigger_high_v','Expected high monitor voltage')])
        ttk.Button(setup,text='Browse output...',command=self.browse).grid(row=2,column=2,padx=4)
        self.form(matrix,[('power_levels','High-power setpoints (W)'),('ramps','Commanded ramp times (ms)'),('repetitions','Repetitions per condition')])
        ttk.Checkbutton(matrix,text='Ramp up',variable=self.v['rise'],command=self.update_count).grid(row=3,column=0,sticky='w',pady=5)
        ttk.Checkbutton(matrix,text='Ramp down',variable=self.v['fall'],command=self.update_count).grid(row=3,column=1,sticky='w',pady=5)
        ttk.Label(matrix,text='Official analysis definition').grid(row=4,column=0,sticky='w',pady=5)
        ttk.Combobox(matrix,textvariable=self.v['measure_definition'],values=('10_TO_90','0_TO_100'),state='readonly').grid(row=4,column=1,sticky='ew')
        self.count_label=ttk.Label(matrix,text=''); self.count_label.grid(row=5,column=0,columnspan=2,sticky='w',pady=10)
        for key in ('power_levels','ramps','repetitions'): self.v[key].trace_add('write',lambda *_:self.update_count())
        self.form(advanced,[('raw_per_ms','Absolute ramp-time units per millisecond (fixed at 1)'),('ramp_mode','Command 31 mode (2 = absolute ramp time)'),('reg_mode','Regulation mode (6=fwd, 7=delivered)'),('record_length','Scope record length'),('pretrigger_pct','Trigger position / pretrigger percent'),('settle_s','Power stabilization delay (s)')])
        self.add_delay_slider(advanced,6,'Pre-ramp capture delay','capture_pre_delay_pct')
        self.add_delay_slider(advanced,7,'Post-ramp delay before scope STOP','capture_stop_delay_pct')
        self.add_seconds_slider(advanced,8,'Inter-condition delay','inter_condition_delay_s',0.0,5.0)
        ttk.Checkbutton(advanced,text='RF OFF between captures',variable=self.v['rf_off_between']).grid(row=9,column=0,columnspan=2,sticky='w',pady=5)
        ttk.Label(advanced,text="Delay sliders scale each condition's AUTO timing. 100% is the calculated AUTO value; lower values stop sooner and higher values retain more baseline.",foreground='#23527C',wraplength=800).grid(row=10,column=0,columnspan=3,sticky='w',pady=8)
        ttk.Label(advanced,text='Command 31 mode 2 commands an absolute up/down ramp time. Command values are milliseconds: 500 commands an absolute 500 ms ramp.',foreground='#9A5B00',wraplength=800).grid(row=11,column=0,columnspan=3,sticky='w',pady=8)
        ttk.Progressbar(outer,variable=self.progress,maximum=100).pack(fill='x')
        ttk.Label(outer,textvariable=self.status,wraplength=900).pack(anchor='w',pady=6)
        buttons=ttk.Frame(outer); buttons.pack(fill='x')
        self.start=ttk.Button(buttons,text='Start Collection',command=self.start_run); self.start.pack(side='right')
        self.stop=ttk.Button(buttons,text='Stop Safely',command=self.stop_event.set,state='disabled'); self.stop.pack(side='right',padx=8)
        ttk.Button(buttons,text='Save Settings',command=self.save).pack(side='left')
        r.protocol('WM_DELETE_WINDOW',self.close); self.update_count()
    def add_delay_slider(self, parent, row, label, key):
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',pady=5)
        frame=ttk.Frame(parent); frame.grid(row=row,column=1,sticky='ew',padx=8,pady=5)
        frame.columnconfigure(0,weight=1)
        value_label=ttk.Label(frame,width=22,anchor='e')
        value_label.grid(row=0,column=1,padx=(8,0))
        def refresh(*_):
            pct=round(float(self.v[key].get())/5)*5
            self.v[key].set(pct)
            value_label.configure(text=f'{pct:.0f}% of AUTO')
        scale=ttk.Scale(frame,from_=25,to=200,variable=self.v[key],command=lambda _value: refresh())
        scale.grid(row=0,column=0,sticky='ew')
        refresh()

    def add_seconds_slider(self, parent, row, label, key, minimum, maximum):
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',pady=5)
        frame=ttk.Frame(parent); frame.grid(row=row,column=1,sticky='ew',padx=8,pady=5)
        frame.columnconfigure(0,weight=1)
        value_label=ttk.Label(frame,width=10,anchor='e'); value_label.grid(row=0,column=1,padx=(8,0))
        def refresh(*_):
            value=round(float(self.v[key].get())*10)/10
            self.v[key].set(value); value_label.configure(text=f'{value:.1f} s')
        ttk.Scale(frame,from_=minimum,to=maximum,variable=self.v[key],command=lambda _value: refresh()).grid(row=0,column=0,sticky='ew')
        refresh()

    def form(self,parent,fields):
        parent.columnconfigure(1,weight=1)
        for row,(key,label) in enumerate(fields):
            ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',pady=5)
            ttk.Entry(parent,textvariable=self.v[key]).grid(row=row,column=1,sticky='ew',padx=8,pady=5)
    def browse(self):
        p=filedialog.askdirectory(parent=self.root,initialdir=self.v['parent'].get() or str(Path.home()))
        if p:self.v['parent'].set(p)
    def update_count(self):
        try:
            n=len(parse_positive_list(self.v['power_levels'].get(),'power level'))*len(parse_positive_list(self.v['ramps'].get(),'ramp time'))*int(self.v['repetitions'].get())*(int(self.v['rise'].get())+int(self.v['fall'].get()))
            self.count_label.configure(text=f'Planned captures: {n}')
        except Exception:self.count_label.configure(text='Planned captures: check entries')
    def values(self): return {k:v.get() for k,v in self.v.items()}
    def save(self): SETTINGS.write_text(json.dumps(self.values(),indent=2),encoding='utf-8'); self.status.set(f'Saved {SETTINGS.name}')
    def validate(self):
        power_levels=parse_positive_list(self.v['power_levels'].get(),'power level'); ramps=parse_positive_list(self.v['ramps'].get(),'ramp time')
        reps=int(self.v['repetitions'].get()); raw_per_ms=float(self.v['raw_per_ms'].get())
        if raw_per_ms != 1:
            raise ValueError('Absolute ramp-time units per millisecond must be 1 because Command 31 values are milliseconds.')
        if int(self.v['ramp_mode'].get()) != 2:
            raise ValueError('This test requires Command 31 ramp mode 2, absolute ramp time. Ramp-rate mode 1 is not valid for this test.')
        if self.v['channel'].get().strip().upper() != 'CH1': raise ValueError('This test is fixed to oscilloscope CH1 in 50-ohm mode.')
        if not 0 <= float(self.v['inter_condition_delay_s'].get()) <= 5:
            raise ValueError('Inter-condition delay must be between 0 and 5 seconds.')
        for key, label in (("capture_pre_delay_pct", "Pre-ramp delay slider"), ("capture_stop_delay_pct", "Post-ramp stop-delay slider")):
            value=float(self.v[key].get())
            if not 25 <= value <= 200:
                raise ValueError(f'{label} must be between 25% and 200% of AUTO.')
        if reps<1: raise ValueError('Repetitions must be at least 1.')
        if not self.v['rise'].get() and not self.v['fall'].get(): raise ValueError('Select RISE and/or FALL.')
        if not Path(self.v['parent'].get()).is_dir(): raise ValueError('Output parent folder does not exist.')
        for ms in ramps:
            raw=round(ms*raw_per_ms)
            if not 0 <= raw <= 65535 or abs(raw/raw_per_ms-ms)>1e-9: raise ValueError(f'{ms} ms cannot be represented exactly with {raw_per_ms:g} raw units/ms.')
        return power_levels,ramps,reps
    def start_run(self):
        try:self.validate()
        except Exception as e:messagebox.showerror('Invalid settings',str(e),parent=self.root);return
        if not messagebox.askyesno('Confirm RF test',f"Maximum selected power is {max(parse_positive_list(self.v['power_levels'].get(),'power level')):g} W. Confirm the load, coupler, detector, cabling, attenuation, and scope input are correctly rated.",parent=self.root):return
        self.save(); self.stop_event.clear(); self.running=True; self.start.config(state='disabled'); self.stop.config(state='normal')
        threading.Thread(target=self.collect,daemon=True).start()
    def ui(self,text,pct=None):
        self.root.after(0,lambda:self.status.set(text))
        if pct is not None:self.root.after(0,lambda:self.progress.set(pct))
    def collect(self):
        scope=dut=None; run_dir=None
        try:
            mod=load_generator_module()
            power_levels,ramps,reps=self.validate(); directions=[]
            if self.v['rise'].get():directions.append('RISE')
            if self.v['fall'].get():directions.append('FALL')
            conditions=[Condition(hi,ms,d,rep) for hi in power_levels for ms in ramps for d in directions for rep in range(1,reps+1)]
            run_dir=unique_run_folder(Path(self.v['parent'].get()),self.v['product'].get())
            (run_dir/'waveforms').mkdir()
            meta={'schema_version':1,'test_id':'rf_ramp_verification','created':datetime.now().isoformat(),'product_id':os.getenv('PARAMOUNT_PRODUCT_ID','UNSPECIFIED'),'product_name':self.v['product'].get(),'generator_address':self.v['generator'].get(),'scope_address':self.v['scope'].get(),'settings':self.values(),'complete':False}
            (run_dir/'run_manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
            self.ui('Connecting to oscilloscope...',1)
            scope=TekScope(self.v['scope'].get(),self.v['channel'].get()); meta['scope_idn']=scope.idn
            self.ui('Connecting to generator and configuring Host control...',2)
            dut=mod.generator(self.v['generator'].get())
            call_dut_with_interlock_retry(lambda: dut.setActiveControlMode(2), "Set Host control mode")
            ensure_rf_off(dut)
            call_dut_with_interlock_retry(lambda: dut.setRegulationMode(int(self.v['reg_mode'].get())), "Set regulation mode")
            call_dut_with_interlock_retry(lambda: dut.setFixedFrequency(int(float(self.v['frequency_khz'].get()))), "Set fixed frequency")
            self.ui('Equipment connected. Preparing first test condition...',3)
            rows=[]
            for index,c in enumerate(conditions,1):
                if self.stop_event.is_set():break
                if index > 1:
                    scope.clear_acquisition()
                inter_condition_s=0.0 if index==1 else float(self.v['inter_condition_delay_s'].get())
                if inter_condition_s:
                    self.ui(f'Waiting {inter_condition_s:.1f} s between conditions...',100*(index-1)/len(conditions))
                    time.sleep(inter_condition_s)
                raw=int(round(c.ramp_ms*float(self.v['raw_per_ms'].get())))
                call_dut_with_interlock_retry(lambda: dut.setSetpointRampingConfiguration(6,0,int(self.v['ramp_mode'].get()),raw,raw,0,0), "Set ramp configuration")
                rb=dut.reportSetpointRampingConfiguration(6,0)
                start_w=0.0 if c.direction=='RISE' else c.high_w; end_w=c.high_w if c.direction=='RISE' else 0.0
                if self.v['rf_off_between'].get():
                    ensure_rf_off(dut)
                call_dut_with_interlock_retry(lambda: dut.setPowerSetpoint(int(round(start_w*10))), "Set starting power")
                ensure_rf_on(dut)
                settle_s=float(self.v['settle_s'].get())
                self.ui(f'Condition {index}/{len(conditions)}: stabilizing start power for {settle_s:.1f} s...',100*(index-1)/len(conditions))
                time.sleep(settle_s)
                if not get_rf_state(dut):
                    raise RuntimeError('RF turned off before scope acquisition; no ramp waveform can be captured.')
                expected_high_v=expected_monitor_voltage(c.high_w)
                trigger=expected_high_v/2.0
                scale_text=self.v['volts_div'].get().strip().upper()
                volts_div=max(0.01, expected_high_v/5.0) if scale_text=='AUTO' else float(scale_text)
                window,secdiv=scope.configure(c.ramp_ms/1000,int(self.v['record_length'].get()),volts_div,trigger,c.direction,float(self.v['pretrigger_pct'].get()))
                armed_state='FREE_RUN_TIMED_STOP'
                scope.start_free_run()
                auto_pre_s=max(0.5,0.15*window)
                pre_pct=float(self.v['capture_pre_delay_pct'].get())
                pre_s=auto_pre_s*pre_pct/100.0
                self.ui(f'Condition {index}/{len(conditions)}: acquiring baseline for {pre_s:.2f} s...',100*(index-1)/len(conditions))
                time.sleep(pre_s)
                call_dut_with_interlock_retry(
                    lambda: dut.setPowerSetpoint(int(round(end_w*10))),
                    f"Set destination power to {end_w:g} W",
                )
                auto_post_s=max(c.ramp_ms/1000+0.5,0.70*window)
                stop_pct=float(self.v['capture_stop_delay_pct'].get())
                post_s=auto_post_s*stop_pct/100.0
                time.sleep(post_s)
                scope.stop()
                x,y,wmeta=scope.waveform()
                name=f"RF_Ramp_{c.direction}_{format_num(start_w)}W_to_{format_num(end_w)}W_{format_num(c.ramp_ms)}ms_Rep{c.repetition:03d}.csv"
                path=run_dir/'waveforms'/name
                with path.open('w',newline='',encoding='utf-8') as f:
                    w=csv.writer(f); w.writerow(['Time (s)','Voltage (V)']); w.writerows(zip(x,y))
                row={**asdict(c),'start_power_w':start_w,'end_power_w':end_w,'commanded_absolute_ramp_time_raw':raw,'commanded_raw':raw,'readback_mode':rb[0],'readback_up_raw':rb[1],'readback_down_raw':rb[2],'expected_high_monitor_v':expected_high_v,'trigger_level_v':trigger,'volts_per_div':volts_div,'inter_condition_delay_s':inter_condition_s,'auto_capture_pre_delay_s':auto_pre_s,'capture_pre_delay_pct':pre_pct,'capture_pre_delay_s':pre_s,'auto_capture_stop_delay_s':auto_post_s,'capture_stop_delay_pct':stop_pct,'capture_stop_delay_s':post_s,'requested_window_s':window,'requested_seconds_per_div':secdiv,'waveform_file':str(path.relative_to(run_dir)),**wmeta,'scope_armed_state':armed_state,'status':'Captured'}
                rows.append(row)
                with (run_dir/'capture_index.csv').open('w',newline='',encoding='utf-8') as f:
                    w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
                self.ui(f'Captured {index}/{len(conditions)}: {name}',100*index/len(conditions))
            meta['complete']=not self.stop_event.is_set(); meta['captures_completed']=len(rows); meta['captures_planned']=len(conditions); meta['finished']=datetime.now().isoformat()
            (run_dir/'run_manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
            self.ui(f"Collection {'completed' if meta['complete'] else 'stopped'}: {run_dir}",100*len(rows)/max(1,len(conditions)))
            self.root.after(0,lambda:messagebox.showinfo('RF ramp collection',f"Saved {len(rows)} capture(s) to:\n{run_dir}",parent=self.root))
        except Exception as e:
            detail=traceback.format_exc();
            if run_dir:(run_dir/'collection_error.txt').write_text(detail,encoding='utf-8')
            self.ui(f'Collection failed: {e}')
            self.root.after(0,lambda err=str(e):messagebox.showerror('Collection failed',err,parent=self.root))
        finally:
            if dut:
                try:dut.rfOFF()
                except Exception:pass
                try:dut.close()
                except Exception:pass
            if scope:
                try:scope.close()
                except Exception:pass
            self.running=False; self.root.after(0,lambda:(self.start.config(state='normal'),self.stop.config(state='disabled')))
    def close(self):
        if self.running:
            if not messagebox.askyesno('Collection running','Request a safe stop and close after cleanup?',parent=self.root):return
            self.stop_event.set(); return
        self.save(); self.root.destroy()

def self_test():
    assert parse_positive_list('50,1000,2000','power')==[50.0,1000.0,2000.0]
    assert parse_positive_list('200,500,5000','ramp')==[200.0,500.0,5000.0]
    assert abs(expected_monitor_voltage(50)-0.075)<1e-12
    assert scope_seconds_per_div(1.0)==0.4
    assert visa_resource('1.2.3.4')=='TCPIP0::1.2.3.4::inst0::INSTR'
    print('RF ramp collection self-test passed.');return 0

def main():
    if '--self-test' in sys.argv:return self_test()
    root=tk.Tk();App(root);root.mainloop();return 0
if __name__=='__main__':raise SystemExit(main())
