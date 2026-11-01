from __future__ import annotations

import argparse
import logging

from adc_ui.app import AdcApp
from adc_ui.service import DemoAdcService


def main() -> None:
    parser = argparse.ArgumentParser(description="Automated Medicine Cabinet touchscreen interface")
    parser.add_argument("--simulator", action="store_true", help="open the development simulator window")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    service = DemoAdcService()
    app = AdcApp(service, show_simulator=args.simulator)
    app.mainloop()


if __name__ == "__main__":
    main()
