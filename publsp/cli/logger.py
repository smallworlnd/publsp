import logging
import time

from datetime import datetime
from nostr_sdk import init_logger, LogLevel


class UTCFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.utcfromtimestamp(record.created)
        return dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')


class LoggerSetup:
    def __init__(self, log_level):
        self.log_level = log_level

    def setup_logging(self):
        # 1) configure the root logger however you like
        level = getattr(logging, self.log_level.upper(), logging.INFO)
        init_logger(LogLevel[self.log_level.upper()])
        logging.converter = time.gmtime
        logging.basicConfig(
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            level=level,
            datefmt='%Y-%m-%dT%H:%M:%S.%fZ'
        )
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            handler.setFormatter(UTCFormatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))

        # 2) silence httpx (or bump it to ERROR-only)
        logging.getLogger("httpx").setLevel(logging.ERROR)
        # optionally disable httpcore and hpack noise too:
        logging.getLogger("httpcore").setLevel(logging.ERROR)
        logging.getLogger("hpack.codec").setLevel(logging.ERROR)
