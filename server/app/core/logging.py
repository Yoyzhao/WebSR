"""日志初始化。

时区口径：日志时间戳用本机时间（Asia/Shanghai）；业务时间字段一律存 UTC，
由展示层转换（tech-arch §4.2 / api-contract §1）。
"""
import logging
import sys

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "info") -> None:
    root = logging.getLogger()
    if root.handlers:
        # uvicorn --reload 会重新 import 应用；避免重复挂 handler 导致日志翻倍
        root.setLevel(level.upper())
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    root.addHandler(handler)
    root.setLevel(level.upper())
