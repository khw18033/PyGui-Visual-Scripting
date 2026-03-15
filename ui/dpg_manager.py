import dearpygui.dearpygui as dpg
from typing import Any

class UIManager:
    def __init__(self):
        self.window_tag = "PrimaryWindow"
        self.editor_tag = "node_editor"

    def initialize(self):
        """DPG 컨텍스트 생성 및 메인 노드 에디터 창을 초기화합니다."""
        dpg.create_context()
        dpg.create_viewport(title='PyGui Visual Scripting (Refactored)', width=1280, height=800, vsync=True)
        dpg.setup_dearpygui()

        with dpg.window(tag=self.window_tag):
            dpg.add_text("Visual Scripting Framework", color=(100, 200, 255))
            dpg.add_separator()
            
            # [수정된 부분] 콜백 함수(callback, delink_callback)를 에디터에 등록합니다!
            with dpg.node_editor(tag=self.editor_tag, callback=self.link_callback, delink_callback=self.delink_callback):
                pass 

        dpg.set_primary_window(self.window_tag, True)
        dpg.show_viewport()

    # ==========================================
    # 🔗 화면에서 선을 긋거나 지울 때 호출되는 함수들
    # ==========================================
    def link_callback(self, sender, app_data):
        """노드 핀끼리 드래그해서 선을 연결할 때 DPG 화면에 선을 그립니다."""
        src_attr, dst_attr = app_data[0], app_data[1]
        
        # DPG 화면에 물리적인 선(Link) 렌더링
        link_id = dpg.add_node_link(src_attr, dst_attr, parent=sender)
        
        # (이후 Engine의 self.engine.add_link()를 호출하여 파이프라인 데이터에 등록하는 코드가 여기에 들어갑니다)
        print(f"[UI] Linked Pin({src_attr}) -> Pin({dst_attr}) | Link ID: {link_id}")

    def delink_callback(self, sender, app_data):
        """연결된 선을 선택하고 Delete 키를 눌렀을 때 선을 지웁니다."""
        link_id = app_data
        
        # DPG 화면에서 물리적인 선(Link) 삭제
        dpg.delete_item(link_id)
        
        # (이후 Engine의 파이프라인 레지스트리에서 해당 연결을 끊어내는 코드가 여기에 들어갑니다)
        print(f"[UI] Unlinked Link ID: {link_id}")

    def draw_node(self, node: Any):
        """
        BaseNode를 상속받은 노드 객체의 스키마(List)를 읽어와서 DPG 노드를 대신 그려줍니다.
        """
        # 노드에서 순수 데이터(List) 스키마만 받아옵니다.
        ui_schema = node.get_ui_schema()
        settings_schema = node.get_settings_schema()

        # DPG 화면에 노드 생성
        with dpg.node(tag=node.node_id, parent=self.editor_tag, label=node.label):
            
            # 1. 핀(Pin) 및 입출력 데이터 UI 그리기
            for pin_type, label, default_val in ui_schema:
                
                # 들어오는 흐름 (Flow In)
                if pin_type == "IN_FLOW":
                    with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input):
                        dpg.add_text(label)
                        
                # 나가는 흐름 (Flow Out)
                elif pin_type == "OUT_FLOW":
                    with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output):
                        dpg.add_text(label)
                        
                # 들어오는 데이터 핀 (Data In) 및 입력창
                elif pin_type == "IN_DATA":
                    with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input):
                        with dpg.group(horizontal=True):
                            dpg.add_text(label, color=(255, 255, 0))
                            if default_val is not None:
                                # 입력창 생성 시 고유 tag를 부여하여 나중에 값을 읽어올 수 있게 함
                                dpg.add_input_float(width=80, default_value=default_val, step=0, tag=f"{node.node_id}_{label}")
                                
                # 나가는 데이터 핀 (Data Out)
                elif pin_type == "OUT_DATA":
                    with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output):
                        dpg.add_text(label)

            # 2. 노드 설정(Settings) UI 그리기 (연결 핀이 없는 정적 파라미터)
            if settings_schema:
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                    dpg.add_spacer(height=5)
                    dpg.add_separator()
                    for param_name, default_val in settings_schema:
                        with dpg.group(horizontal=True):
                            dpg.add_text(param_name)
                            dpg.add_input_float(width=60, default_value=default_val, step=0, tag=f"{node.node_id}_set_{param_name}")

    def render_frame(self):
        """메인 루프에서 매 프레임마다 화면을 렌더링합니다."""
        dpg.render_dearpygui_frame()

    def is_running(self):
        """GUI 창이 켜져 있는지 확인합니다."""
        return dpg.is_dearpygui_running()

    def cleanup(self):
        """프로그램 종료 시 자원을 반환합니다."""
        dpg.destroy_context()