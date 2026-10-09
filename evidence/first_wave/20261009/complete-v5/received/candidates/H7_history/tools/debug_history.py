"""Offline diagnostic launcher; not a product API."""
import sys
from pathlib import Path

def trace(frame,event,arg):
    if event=='exception' and frame.f_code.co_filename.endswith('/acceptance_history.py'):
        print('HISTORY DIAGNOSTIC',frame.f_lineno,repr(arg[1]),file=sys.stderr)
    return trace
sys.settrace(trace)
exec(compile((Path(__file__).parent/'offline_pytest.py').read_text(),str(Path(__file__).parent/'offline_pytest.py'),'exec'))
