"""Compatibility entry point for capture tests and Apple's LLDB Python."""
import sys,runpy
from pathlib import Path
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'chronicle/macos'))
if __name__=='__main__':runpy.run_path(str(root/'chronicle/macos/capture.py'),run_name='__main__')
else:
 from chronicle.macos import capture as implementation
 sys.modules[__name__]=implementation
