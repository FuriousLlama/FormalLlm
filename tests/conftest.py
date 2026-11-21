"""Shared fixtures for FormalLlm tests."""

import sys
from pathlib import Path

# Make sprint/ importable
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sprint" / "typecheck"))
sys.path.insert(0, str(ROOT / "sprint" / "benchmark"))
sys.path.insert(0, str(ROOT / "sprint" / "extract"))
sys.path.insert(0, str(ROOT / "sprint" / "corpus"))
