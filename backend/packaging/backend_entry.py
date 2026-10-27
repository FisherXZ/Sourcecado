"""PyInstaller entry point for the bundled Sourcecado backend.

Only imports coworker's public entrypoints; does not modify backend/coworker.
The frozen backend has no `python -m`, so a PDF read runs this same executable
with `--pdf-worker` (see coworker/pdf_isolation.py). The worker path imports
only the worker, so it starts fast.
"""

import sys

if __name__ == "__main__":
    if sys.argv[1:2] == ["--pdf-worker"]:
        from coworker.pdf_worker import serve

        serve(sys.argv[2:])
    else:
        from coworker.run import main

        main()
