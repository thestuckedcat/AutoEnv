import logging
import os
from datetime import datetime
from typing import Optional


def setup_logger(log_dir: str = "logs", run_id: Optional[str] = None) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"autoenv_{run_id}.log")

    logger = logging.getLogger("autoenv")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | run=%(run_id)s | %(filename)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    class RunIdFilter(logging.Filter):
        def __init__(self, run: str):
            super().__init__()
            self.run = run

        def filter(self, record: logging.LogRecord) -> bool:
            record.run_id = self.run
            return True

    run_filter = RunIdFilter(run_id)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    fh.addFilter(run_filter)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    sh.addFilter(run_filter)
    logger.addHandler(sh)

    logger.info("日志初始化完成，文件：%s", log_file)
    return logger
