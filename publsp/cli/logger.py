import logging


class LoggerSetup:
    def __init__(self, log_level):
        self.log_level = log_level

    def setup_logging(self):
        # 1) configure the root logger however you like
        level = getattr(logging, self.log_level.upper(), logging.INFO)
        logging.basicConfig(
            format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            level=level,
        )

        # 2) silence httpx (or bump it to ERROR-only)
        logging.getLogger("httpx").setLevel(logging.ERROR)
        # optionally disable httpcore and hpack noise too:
        logging.getLogger("httpcore").setLevel(logging.ERROR)
        logging.getLogger("hpack.codec").setLevel(logging.ERROR)
