"""Compatibility entry point; implementation lives in chronicle.macos."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from chronicle.macos import core as implementation
if __name__ == '__main__':
    if hasattr(implementation,'main'):
        result=implementation.main()
        if isinstance(result,int):raise SystemExit(result)
else:
    sys.modules[__name__]=implementation
