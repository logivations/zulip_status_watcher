#  (C) Copyright
#  Logivations GmbH, Munich 2025
import os
from pathlib import Path
from typing import *
import configparser
import gzip
import logging
import logging.handlers
import multiprocessing as mp
import os
import sys
import traceback
from functools import partial
from logging.handlers import RotatingFileHandler
from typing import Callable, Optional

# most prioritized first
APP_CONFIG_PATHS: List[Tuple[str, Path]] = []

# Here you can specify the path along which your
# static configuration file is located
STATIC_FILE_NAME: str = "appconfig_static"

STATIC_FILE_PATH: str = "/data/"
STATIC_PATH: str = os.path.join(STATIC_FILE_PATH, STATIC_FILE_NAME)
PROJECT_PATH = "/zulip_status_watcher/appconfig/"
APP_CONFIG_PATHS.append(("PROJECT", Path(PROJECT_PATH)))
if os.path.exists(STATIC_PATH):
    APP_CONFIG_PATHS.append(("STATIC", Path(STATIC_PATH)))

LOG_PATH = "/data/logs/"
LOG_PATH_COMPRESSED = "/data/logs/backup/"

def get_expanded_appconfig(extend: str = "") -> List[str]:
    """
    The method takes the part of the path that follows "... / appconfig /" and returns a list that contains
    the full default configuration path and the static configuration path,
    in the following order: ["default", "static", "env"].

    Use this method if you want to pass a path to a parser.

    Be careful, not every parser can get a list as an argument (for example: xml.sax.make_parser())

    Example 1:
        path = "w2mo.connection.properties"
        1. p = configparser.ConfigParser(); p.read(get_expanded_appconfig(path)),
        2. p = configparser.RawConfigParser(); p.read(get_expanded_appconfig(path))
        3. p = GeneralParser(path=get_expanded_appconfig(path))

    Example 2:
        get_expanded_appconfig("tracking/tracking.properties") ->
            [
                "/code/deep_cv/appconfig/tracking/tracking.properties",
                "/data/appconfig_static/tracking/tracking.properties",
                "${DEEP_CV_APPCONFIG}"/tracking/tracking.properties", # only if exists
            ]
    :return: list
    """
    return [str(p / extend) for _, p in reversed(APP_CONFIG_PATHS)]


class CustomFormatter(logging.Formatter):
    """Logging colored formatter, adapted from https://stackoverflow.com/a/56944256/3638629"""

    # same as ROS2 https://github.com/ros2/rcutils/blob/b4a039592a1afa4654d3f0032ddd9e2b4dcab1f2/src/logging.c#L790
    white = "\033[0m"
    green = "\033[32m"
    yellow = "\033[33m"
    red = "\033[31m"
    reset = white

    def __init__(self, fmt):
        super().__init__()
        self.fmt = fmt
        self.FORMATS = {
            logging.DEBUG: self.green + self.fmt + self.reset,
            logging.INFO: self.white + self.fmt + self.reset,
            logging.WARNING: self.yellow + self.fmt + self.reset,
            logging.ERROR: self.red + self.fmt + self.reset,
            logging.CRITICAL: self.red + self.fmt + self.reset,
        }

    def format(self, record):
        """Format with color"""
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


def setup_logging(
    file_name: Optional[str] = "deep_cv.log",
    fail_if_not_first_import: bool = False,
    logger: logging.Logger = None,
    unique_identifier: Optional[str] = None,
):
    """
    setups loggers for a current running server defined in logging.yaml
    IMPORTANT: Call this function as early as possible, to handle uncaught exceptions as well. See WMO-56487
    :param unique_identifier: a string that will be added to the output format as a unique identifier, usually of a process.
    :param file_name: Name of the logfile. In most cases, it is fine to use the default configuration and
                      to specify the filename. Set to None to disable writing to a file
    :param fail_if_not_first_import: Whether to raise if logging can not be set up. Use for logging for main applications.
    :param logger: existing logger that is to be set up
    :return:
    """

    if not logger:
        logging_was_setup = True
    os.makedirs(LOG_PATH_COMPRESSED, exist_ok=True)

    identifier = f" [{unique_identifier}] " if unique_identifier else " "
    format = CustomFormatter(
        f"[%(levelname)s] [%(asctime)s]{identifier}[%(name)s]: %(message)s"
    )

    is_root_logger = False
    if not logger:
        logger = logging.getLogger()
        is_root_logger = True

    if file_name:
        # write more logs on servers
        max_size = 152428800
        backup_count = 10

        def _rotator(source, dest):
            with open(source, "rb") as sf:
                data = sf.read()
                compressed = gzip.compress(data)
                with open(dest, "wb") as df:
                    df.write(compressed)
            os.remove(source)

        rh = RotatingFileHandler(
            filename=f"{LOG_PATH}{file_name}",
            maxBytes=max_size,
            backupCount=backup_count,
        )
        rh.setLevel(logging.DEBUG)
        rh.setFormatter(format)
        rh.rotator = _rotator
        rh.namer = lambda name: name.replace(LOG_PATH, LOG_PATH_COMPRESSED) + ".gz"
        logger.addHandler(rh)

        logger_uvicorn = logging.getLogger("uvicorn")
        logger_uvicorn_error = logging.getLogger("uvicorn.error")
        logger_uvicorn_access = logging.getLogger("uvicorn.access")
        logger_uvicorn.addHandler(rh)
        logger_uvicorn_error.addHandler(rh)
        logger_uvicorn_access.addHandler(rh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(format)
    logger.addHandler(ch)

    if is_root_logger:
        _logging_queue = mp.get_context("spawn").Queue()
        subprocess_listener = logging.handlers.QueueListener(
            _logging_queue, *logger.handlers, respect_handler_level=True
        )
        subprocess_listener.start()

    logger.setLevel(logging.DEBUG)


