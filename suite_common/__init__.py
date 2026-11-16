"""Shared helpers for the S3R Transparency Testing Suite scripts."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# AE command modules (AE_Com_Module, AECommandsUniversal, Lynx, ...); imported flat by name.
AE_ADAPTERS_DIR = REPO_ROOT / "suite_instruments" / "adapters" / "ae"
