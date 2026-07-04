# -*- coding: utf-8 -*-
"""
无控制台启动入口（pythonw.exe 启动，仅 GUI + 托盘，无黑色 cmd 窗口）。
双击此文件即可运行。日志写入同目录 desktop_hider.log。
"""
import main  # 复用 main.py 的全部逻辑

if __name__ == "__main__":
    main.main()
