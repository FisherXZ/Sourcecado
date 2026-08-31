"""PyInstaller entry point for the bundled Sourcecado backend.

Only imports coworker.run's public entrypoint; does not modify desktop/coworker.
"""

from coworker.run import main

if __name__ == "__main__":
    main()
