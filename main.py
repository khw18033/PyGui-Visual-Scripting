import time
import dearpygui.dearpygui as dpg

from ui.dpg_manager import UIManager
from core.engine import ExecutionEngine
from core.factory import NodeFactory

# 👇 1. 새롭게 만든 전역(Global) 통역사를 불러옵니다.
from core.input_manager import global_input_manager 

def main():
    print("[System] Visual Scripting Framework Booting...")

    # 모듈 초기화
    ui_manager = UIManager()
    engine = ExecutionEngine()

    # UI 화면 띄우기
    ui_manager.initialize()

    # --- [테스트용 기본 노드 세팅] ---
    print("[System] Setting up Default Nodes...")
    
    driver_node = NodeFactory.create_node("MT4_DRIVER")
    sag_node = NodeFactory.create_node("MT4_SAG")
    keyboard_node = NodeFactory.create_node("MT4_KEYBOARD") # 방금 만든 키보드 노드 띄우기
    
    if driver_node:
        ui_manager.draw_node(driver_node)
        engine.add_node(driver_node)
        
    if sag_node:
        ui_manager.draw_node(sag_node)
        engine.add_node(sag_node)

    if keyboard_node:
        ui_manager.draw_node(keyboard_node)
        engine.add_node(keyboard_node)
    # --------------------------------

    # 메인 파이프라인 루프 준비
    last_logic_time = time.time()
    LOGIC_RATE = 0.02  # 50Hz 엔진 업데이트 주기

    print("[System] Boot Complete. Entering Main Loop.")
    
    # 엔진 강제 시작 (테스트용)
    engine.start()

    # GUI 창이 켜져 있는 동안 무한 반복
    while ui_manager.is_running():
        current_time = time.time()
        
        if current_time - last_logic_time > LOGIC_RATE:
            
            # 👇 2. 여기가 핵심입니다! 매 틱(0.02초)마다 키보드 상태를 읽어와 장부를 갱신합니다.
            global_input_manager.poll_inputs()
            
            # 준비된 노드들 연산(execute) 실행
            engine.tick()
            
            last_logic_time = current_time
        
        # UI 렌더링
        ui_manager.render_frame()

    # 시스템 안전 종료
    engine.shutdown()
    ui_manager.cleanup()
    print("[System] Shutdown complete.")

if __name__ == "__main__":
    main()