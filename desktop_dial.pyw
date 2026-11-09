# -*- coding: utf-8 -*-
"""圆盘启动器 · 免控制台启动入口（pythonw 运行，双击即可）。

用法：
    双击本文件；或在命令行执行  pythonw desktop_dial.pyw
    需要自定义配置时：pythonw desktop_dial.pyw --config "D:\\path\\my_config.json"

任何未捕获异常都会写入 %TEMP%\\desktop_dial_pyw_crash.log，便于排查（无控制台时唯一线索）。
"""
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _log_crash(exc_text: str) -> None:
    try:
        path = os.path.join(tempfile.gettempdir(), "desktop_dial_pyw_crash.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(exc_text + "\n" + "-" * 60 + "\n")
    except OSError:
        pass


def main() -> int:
    try:
        import desktop_dial
        return int(desktop_dial.main(sys.argv) or 0)
    except SystemExit:
        raise
    except BaseException:
        _log_crash(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
