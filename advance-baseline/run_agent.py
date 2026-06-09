"""smolagents 进阶 baseline 入口"""

import sys
import os

for _p in [
    os.path.expanduser("~/.local/lib/python3.10/site-packages"),
    "/usr/lib/python3/dist-packages",
]:
    if _p not in sys.path and os.path.isdir(_p):
        sys.path.insert(0, _p)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from src.agent.smol_agent import main

if __name__ == "__main__":
    main()