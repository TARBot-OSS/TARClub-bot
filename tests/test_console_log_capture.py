import logging
from unittest.mock import MagicMock

from tarbot_console import ConsoleAwareStreamHandler

from tarclub.config import Settings
from tarclub.services.console_service import ConsoleService


def test_log_capture_attaches_once_and_keeps_pre_start_logs():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    ConsoleAwareStreamHandler._log_buffer.clear()
    try:
        for handler in list(root.handlers):
            root.removeHandler(handler)

        settings = MagicMock(spec=Settings, console_session_timeout=300.0)
        console = ConsoleService(MagicMock(), MagicMock(), settings)
        console.attach_log_capture()
        console._before_start()

        logging.getLogger("tarclub").warning("PRE_START_MARKER")

        attached = [h for h in root.handlers if isinstance(h, ConsoleAwareStreamHandler)]
        assert len(attached) == 1
        assert any("PRE_START_MARKER" in line for line in ConsoleAwareStreamHandler._log_buffer)
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            if handler not in original_handlers:
                handler.close()
        for handler in original_handlers:
            root.addHandler(handler)
