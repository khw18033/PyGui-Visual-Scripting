import time
import dearpygui.dearpygui as dpg

from ui.dpg_manager import UIManager
from core.engine import ExecutionEngine
from core.input_manager import InputManager
from core.factory import NodeFactory

def main():
    print("[System] Visual Scripting Framework Booting...")

    # 1. 아키텍처 핵심 모듈 인스턴스화 (결합도 최소화)
    ui_manager = UIManager()
    engine = ExecutionEngine()
    input_manager = InputManager()

    # 2. UI 초기화 (DPG Context 생성)
    ui_manager.initialize()

    # --- [테스트용 더미 데이터 세팅] ---
    # 실제 환경에서는 GUI의 "Add Node" 버튼 콜백에서 이 작업이 이루어집니다.
    print("[System] Setting up Default Nodes...")
    
    # 팩토리를 통해 노드 생성
    driver_node = NodeFactory.create_node("MT4_DRIVER")
    sag_node = NodeFactory.create_node("MT4_SAG")
    
    # 생성된 노드를 UI에 그리고, 엔진에 등록
    if driver_node:
        ui_manager.draw_node(driver_node)
        engine.add_node(driver_node)
        
    if sag_node:
        ui_manager.draw_node(sag_node)
        engine.add_node(sag_node)
    # --------------------------------

    # 3. 메인 파이프라인 루프
    last_logic_time = time.time()
    LOGIC_RATE = 0.02  # 50Hz 주기

    print("[System] Boot Complete. Entering Main Loop.")
    
    # 엔진 강제 시작 (테스트용)
    engine.start()

    while ui_manager.is_running():
        current_time = time.time()
        
        if current_time - last_logic_time > LOGIC_RATE:
            # 1단계: 통역사가 키보드/마우스 상태를 싹 읽어서 장부에 기록
            input_manager.poll_inputs()
            
            # (추후 로봇 노드들이 input_manager.get_key('Z') 등을 호출하여 사용)
            
            # 2단계: 데이터 배달 및 준비된 노드들 연산(execute) 실행
            engine.tick()
            
            last_logic_time = current_time
        
        # 3단계: 가벼워진 UI 매니저는 화면 렌더링만 묵묵히 수행
        ui_manager.render_frame()

        

    # 4. 시스템 안전 종료
    engine.shutdown()
    ui_manager.cleanup()
    print("[System] Shutdown complete.")

if __name__ == "__main__":
    main()