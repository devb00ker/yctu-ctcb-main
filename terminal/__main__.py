import argparse
import logging
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from .config import Config
from .ui import MainWindow


def main():
    parser = argparse.ArgumentParser(description="YCTU 自定义终端")
    parser.add_argument("--demo", action="store_true", help="启动本机 MQTT + HEVC 演示")
    parser.add_argument("--config")
    parser.add_argument("--connect", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    config = Config.load(args.config)
    simulator = None
    if args.demo:
        from .simulator import Simulator
        config = Config(host="127.0.0.1", video_source="127.0.0.1")
        simulator = Simulator()
        simulator.start()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("YCTU Custom Terminal")
    window = MainWindow(config, args.demo)
    window.show()
    if args.demo or args.connect:
        QTimer.singleShot(0, window.toggle_connection)
    try:
        result = app.exec()
    finally:
        if simulator:
            simulator.stop()
    return result


if __name__ == "__main__":
    sys.exit(main())
