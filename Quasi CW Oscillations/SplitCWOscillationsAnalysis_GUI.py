"""Split-CW oscillations analysis entry point. Runs QuasiCWOscillationsAnalysis_GUI.py, which was byte-identical to this script."""
import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("QuasiCWOscillationsAnalysis_GUI.py")), run_name="__main__")