"""Split-CW Rise/Fall analysis entry point. Runs QuasiCWDataAnalysis_GUI.py, which was byte-identical to this script."""
import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("QuasiCWDataAnalysis_GUI.py")), run_name="__main__")