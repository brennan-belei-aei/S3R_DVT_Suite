"""Shared helpers for the S3R Transparency Testing Suite scripts."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AE_DEPENDENCIES_DIR = REPO_ROOT / "AESpecificDependencies"
