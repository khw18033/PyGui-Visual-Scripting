import threading
import sys
import os

# Append current dir to sys path for submodules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from nodes.robots.mt4 import init_mt4_serial, auto_reconnect_mt4_thread, mt4_background_logger_thread
from nodes.robots.go1 import init_go1_network, go1_keepalive_thread
from ui.dpg_manager import start_gui

def main():
    # Initialize MT4 serial port immediately
    init_mt4_serial()
    
    # Launch background auto-reconnect and logger for MT4
    threading.Thread(target=auto_reconnect_mt4_thread, daemon=True).start()
    threading.Thread(target=mt4_background_logger_thread, daemon=True).start()

    init_go1_network()
    threading.Thread(target=go1_keepalive_thread, daemon=True).start()
    
    # Start DPG main UI
    start_gui()

if __name__ == "__main__":
    main()
