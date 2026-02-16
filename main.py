import os
import sys
import logging

from dotenv import load_dotenv
from PyQt5.QtWidgets import QApplication

from app.ui.desktop_pet_window import DesktopPetWindow


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    load_dotenv()
    app = QApplication(sys.argv)
    app.setApplicationName("Live2D Desktop Pet")

    window = DesktopPetWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
