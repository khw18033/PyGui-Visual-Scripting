import threading
import sys
import os
import importlib
import glob

# Append current dir to sys path for submodules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core import config
from nodes.robots.mt4 import init_mt4_serial, auto_reconnect_mt4_thread, mt4_background_logger_thread
from nodes.robots.ep01 import init_ep_network, ep_status_thread
from ui.dpg_manager import start_gui

def select_go1_module():
    """Scan and let user select go1 module variant"""
    robots_path = os.path.join(os.path.dirname(__file__), 'nodes', 'robots')
    go1_files = [os.path.basename(f)[:-3] for f in glob.glob(os.path.join(robots_path, 'go1*.py'))]
    go1_files = sorted(go1_files)
    
    if not go1_files:
        print("No go1*.py files found in nodes/robots/")
        return False
    
    print("\nAvailable Go1 modules:")
    for i, module_name in enumerate(go1_files, 1):
        print(f"  [{i}] {module_name}.py")
    
    try:
        choice = int(input("\nSelect module (number): ")) - 1
        if 0 <= choice < len(go1_files):
            selected = go1_files[choice]
            config.GO1_MODULE_NAME = selected
            
            # Update config.py file
            config_path = os.path.join(os.path.dirname(__file__), 'core', 'config.py')
            with open(config_path, 'w') as f:
                f.write(f"# Go1 Module Configuration\nGO1_MODULE_NAME = '{selected}'\n")
            
            print(f"Selected: {selected}\n")
            return True
        else:
            print("Invalid choice")
            return False
    except ValueError:
        print("Invalid input")
        return False

def import_go1_modules():
    """Dynamically import go1 modules based on config"""
    try:
        go1_module = importlib.import_module(f'nodes.robots.{config.GO1_MODULE_NAME}')
        go1_keepalive_thread = getattr(go1_module, 'go1_keepalive_thread', None)
        init_go1_connection = getattr(go1_module, 'init_go1_connection', None)
        return go1_keepalive_thread, init_go1_connection
    except ImportError as e:
        print(f"Failed to import go1 module: {e}")
        return None, None

def main():
    # Select go1 module variant
    if not select_go1_module():
        print("Continuing without go1 module...")
        go1_keepalive_thread_fn = None
        init_go1_connection_fn = None
    else:
        go1_keepalive_thread_fn, init_go1_connection_fn = import_go1_modules()
    
    # Initialize MT4 serial port immediately
    init_mt4_serial()
    
    # Launch background auto-reconnect and logger for MT4
    threading.Thread(target=auto_reconnect_mt4_thread, daemon=True).start()
    threading.Thread(target=mt4_background_logger_thread, daemon=True).start()

    if init_go1_connection_fn:
        init_go1_connection_fn()
        if go1_keepalive_thread_fn:
            threading.Thread(target=go1_keepalive_thread_fn, daemon=True).start()

    init_ep_network()
    threading.Thread(target=ep_status_thread, daemon=True).start()
    
    # Start DPG main UI
    start_gui()

if __name__ == "__main__":
    main()
