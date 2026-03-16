import time
import threading
import subprocess
import dearpygui.dearpygui as dpg

from ui.dpg_manager import UIManager
from core.engine import ExecutionEngine
from core.input_manager import global_input_manager 

# --- 배경 스레드에 필요한 기존 변수들 Import ---
from nodes.robots.mt4 import mt4_dashboard, mt4_current_pos, init_mt4_serial, mt4_background_logger_thread

sys_net_str = "Loading Network..."

def network_monitor_thread():
    global sys_net_str
    while True:
        try:
            out = subprocess.check_output("ip -o -4 addr show", shell=True).decode('utf-8')
            info = [f"[{p.split()[1]}] {p.split()[3].split('/')[0]}" for p in out.strip().split('\n') if ' lo ' not in p and len(p.split()) >= 4]
            sys_net_str = "\n".join(info) if info else "Offline"
        except: pass
        time.sleep(2)

def auto_reconnect_mt4_thread():
    import os
    import serial.tools.list_ports
    while True:
        if mt4_dashboard["hw_link"] != "Online":
            port_exists = False
            if os.name == 'nt':
                if serial.tools.list_ports.comports(): port_exists = True
            else:
                if os.path.exists('/dev/ttyUSB0'): port_exists = True
                
            if port_exists:
                try: init_mt4_serial()
                except: pass
def main():
    print("[System] Visual Scripting Framework Booting...")

    # 모듈 초기화 (엔진을 UI에 주입)
    engine = ExecutionEngine()
    ui_manager = UIManager(engine)
    ui_manager.initialize()

    # 백그라운드 스레드 가동 (Answer_code.py와 100% 동일)
    init_mt4_serial()
    threading.Thread(target=auto_reconnect_mt4_thread, daemon=True).start()
    threading.Thread(target=network_monitor_thread, daemon=True).start()
    threading.Thread(target=mt4_background_logger_thread, daemon=True).start()

    last_logic_time = time.time()
    LOGIC_RATE = 0.02  

    print("[System] Boot Complete. Entering Main Loop.")

    while ui_manager.is_running():
        current_time = time.time()
        
        # 1. MT4 Dashboard UI 실시간 갱신
        if mt4_dashboard["last_pkt_time"] > 0: dpg.set_value("mt4_dash_status", f"Status: {mt4_dashboard['status']}")
        if dpg.does_item_exist("mt4_dash_latency"): dpg.set_value("mt4_dash_latency", f"Latency: {mt4_dashboard.get('latency', 0.0):.1f} ms")
        
        dpg.set_value("mt4_x", f"X: {mt4_current_pos['x']:.1f}"); dpg.set_value("mt4_y", f"Y: {mt4_current_pos['y']:.1f}")
        dpg.set_value("mt4_z", f"Z: {mt4_current_pos['z']:.1f}"); dpg.set_value("mt4_g", f"G: {mt4_current_pos['gripper']:.1f}")
        if dpg.does_item_exist("mt4_r"): dpg.set_value("mt4_r", f"R: {mt4_current_pos['roll']:.1f}°")
        
        hw_status = mt4_dashboard.get('hw_link', "Offline")
        dpg.set_value("mt4_dash_link", f"HW: {hw_status}")
        if hw_status == "Online": dpg.configure_item("mt4_dash_link", color=(0,255,0))
        elif hw_status == "Simulation": dpg.configure_item("mt4_dash_link", color=(255,200,0))
        else: dpg.configure_item("mt4_dash_link", color=(255,0,0))
        
        if dpg.does_item_exist("sys_tab_net"): dpg.set_value("sys_tab_net", sys_net_str)

        from nodes.robots.mt4 import mt4_mode, get_mt4_paths
        if dpg.does_item_exist("btn_mt4_record"):
            current_label = "Stop Recording" if mt4_mode["recording"] else "Start Recording"
            if dpg.get_item_label("btn_mt4_record") != current_label:
                dpg.set_item_label("btn_mt4_record", current_label)
                if not mt4_mode["recording"] and dpg.does_item_exist("combo_mt4_path"):
                    dpg.configure_item("combo_mt4_path", items=get_mt4_paths())

        # 2. 메인 파이프라인 연산
        if current_time - last_logic_time > LOGIC_RATE:
            ui_manager.sync_ui_to_nodes()
            has_keyboard_consumers = any(
                node.type_str in ("MT4_KEYBOARD", "COND_KEY")
                for node in engine.nodes.values()
            )
            if has_keyboard_consumers:
                global_input_manager.poll_inputs()
            else:
                global_input_manager.clear_keys()
            engine.tick()
            last_logic_time = current_time
        
        ui_manager.render_frame()

    engine.shutdown()
    ui_manager.cleanup()
    print("[System] Shutdown complete.")

if __name__ == "__main__":
    main()