import multiprocessing
import sys

from framecut.main_window import main


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
