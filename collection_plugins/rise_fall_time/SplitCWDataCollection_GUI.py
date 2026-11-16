"""Split-CW Rise/Fall collection.

Same procedure as QuasiCWDataCollection_GUI.py, plus setSplitCWEnable() before each
transition and setSplitCWDisable() after RF OFF.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from QuasiCWDataCollection_GUI import main

if __name__ == "__main__":
    main(split_cw=True)