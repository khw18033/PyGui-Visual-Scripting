import dearpygui.dearpygui as dpg
from typing import Any
from core.factory import NodeFactory
from core.serializer import GraphSerializer
# 기존 맨 윗줄 import에 mt4_homing_callback을 껴 넣어주세요.
from nodes.robots.mt4 import mt4_manual_control_callback, mt4_move_to_coord_callback, toggle_mt4_record, play_mt4_path, mt4_homing_callback

class UIManager:
    def __init__(self, engine):
        self.engine = engine  # 링크 처리를 위해 엔진과 직접 연결
        self.window_tag = "PrimaryWindow"
        self.editor_tag = "node_editor"
        self.pin_label_map = {}  # UI 핀 ID와 노드 핀 이름 매핑
        self.is_bulk_loading = False

    def initialize(self):
        dpg.create_context()
        
        # Delete 키 삭제 콜백 등록
        with dpg.handler_registry(): 
            dpg.add_key_press_handler(dpg.mvKey_Delete, callback=self.delete_selection)

        with dpg.window(tag=self.window_tag):
            # 1. 상단 대시보드 탭 (기존과 동일한 레이아웃)
            with dpg.tab_bar():
                with dpg.tab(label="MT4 Dashboard"):
                    with dpg.group(horizontal=True):
                        with dpg.child_window(width=250, height=130, border=True):
                            dpg.add_text("MT4 Status", color=(150,150,150)); 
                            dpg.add_text("Status: Idle", tag="mt4_dash_status", color=(0,255,0))
                            dpg.add_text(f"HW: Offline", tag="mt4_dash_link", color=(255,0,0))
                            dpg.add_text("Latency: 0.0 ms", tag="mt4_dash_latency", color=(255,255,0))
                        with dpg.child_window(width=350, height=130, border=True):
                            dpg.add_text("Manual Control", color=(255,200,0))
                            with dpg.group(horizontal=True):
                                dpg.add_button(label="X+", width=60, callback=mt4_manual_control_callback, user_data=('x', 10)); dpg.add_button(label="X-", width=60, callback=mt4_manual_control_callback, user_data=('x', -10))
                                dpg.add_text("|"); dpg.add_button(label="Y+", width=60, callback=mt4_manual_control_callback, user_data=('y', 10)); dpg.add_button(label="Y-", width=60, callback=mt4_manual_control_callback, user_data=('y', -10))
                            with dpg.group(horizontal=True):
                                dpg.add_button(label="Z+", width=60, callback=mt4_manual_control_callback, user_data=('z', 10)); dpg.add_button(label="Z-", width=60, callback=mt4_manual_control_callback, user_data=('z', -10))
                                dpg.add_text("|"); dpg.add_button(label="G+", width=60, callback=mt4_manual_control_callback, user_data=('gripper', 5)); dpg.add_button(label="G-", width=60, callback=mt4_manual_control_callback, user_data=('gripper', -5))
                            with dpg.group(horizontal=True):
                                dpg.add_button(label="R+", width=60, callback=mt4_manual_control_callback, user_data=('roll', 5)); dpg.add_button(label="R-", width=60, callback=mt4_manual_control_callback, user_data=('roll', -5))
                        with dpg.child_window(width=300, height=130, border=True):
                            dpg.add_text("Direct Coord", color=(0,255,255))
                            with dpg.group(horizontal=True):
                                dpg.add_text("X"); dpg.add_input_int(tag="input_x", width=50, default_value=200, step=0)
                                dpg.add_text("Y"); dpg.add_input_int(tag="input_y", width=50, default_value=0, step=0)
                            with dpg.group(horizontal=True):
                                dpg.add_text("Z"); dpg.add_input_int(tag="input_z", width=50, default_value=120, step=0)
                                dpg.add_text("G"); dpg.add_input_int(tag="input_g", width=50, default_value=40, step=0)
                                dpg.add_text("R"); dpg.add_input_int(tag="input_r", width=50, default_value=0, step=0)
                            with dpg.group(horizontal=True):
                                dpg.add_button(label="Move", width=100, callback=mt4_move_to_coord_callback)
                                dpg.add_button(label="Homing", width=100, callback=mt4_homing_callback)
                        with dpg.child_window(width=150, height=130, border=True):
                            dpg.add_text("Coords", color=(0,255,255))
                            dpg.add_text("X: 0", tag="mt4_x"); dpg.add_text("Y: 0", tag="mt4_y")
                            dpg.add_text("Z: 0", tag="mt4_z"); dpg.add_text("G: 0", tag="mt4_g")
                            dpg.add_text("R: 0.0", tag="mt4_r")
                        with dpg.child_window(width=200, height=130, border=True):
                            dpg.add_text("Record & Play", color=(255,100,200))
                            dpg.add_input_text(tag="path_name_input", default_value="my_path", width=130)
                            dpg.add_button(label="Start Recording", tag="btn_mt4_record", width=130, callback=lambda s,a,u: toggle_mt4_record())
                            dpg.add_combo(items=[], tag="combo_mt4_path", width=130)
                            dpg.add_button(label="Play Selected", width=130, callback=play_mt4_path)

                with dpg.tab(label="Files & System"):
                    with dpg.group(horizontal=True):
                        with dpg.child_window(width=650, height=130, border=True):
                            dpg.add_text("File Manager", color=(0,255,255))
                            with dpg.group(horizontal=True):
                                dpg.add_text("Save:"); dpg.add_input_text(tag="file_name_input", default_value="my_graph", width=120)
                                dpg.add_button(label="SAVE", callback=self.handle_save_graph, width=60)
                                dpg.add_spacer(width=20)
                                dpg.add_text("Load:"); dpg.add_combo(items=GraphSerializer.get_save_files(), tag="file_list_combo", width=120)
                                dpg.add_button(label="LOAD", callback=self.handle_load_graph, width=60)
                                dpg.add_button(label="Refresh", callback=lambda: dpg.configure_item("file_list_combo", items=GraphSerializer.get_save_files()), width=60)
                        with dpg.child_window(width=400, height=130, border=True):
                            dpg.add_text("Network Info", color=(100,200,255))
                            dpg.add_text("Loading...", tag="sys_tab_net", color=(180,180,180))

            dpg.add_separator()
            
            # 2. 노드 생성 메뉴바
            with dpg.group():
                with dpg.group(horizontal=True):
                    dpg.add_text("Nodes:", color=(200,200,200))
                    for n in ["START", "COND_KEY", "LOGIC_IF", "LOGIC_LOOP", "MT4_ACTION", "CONSTANT", "PRINT", "MT4_DRIVER"]:
                        dpg.add_button(label=n.replace("MT4_", ""), callback=lambda s,a,u: self.create_and_draw(u), user_data=n)
                    dpg.add_spacer(width=30)
                    dpg.add_text("Adv. Tools:", color=(255,200,0))
                    for n in ["MT4_KEYBOARD", "MT4_UNITY", "UDP_RECV", "MT4_SAG", "MT4_CALIB", "MT4_TOOLTIP", "MT4_BACKLASH"]:
                        dpg.add_button(label=n.replace("MT4_", ""), callback=lambda s,a,u: self.create_and_draw(u), user_data=n)
                dpg.add_spacer(width=30)
                dpg.add_button(label="RUN SCRIPT", tag="btn_run", callback=self.toggle_run, width=150)
                
            # 3. 메인 노드 에디터
            with dpg.node_editor(tag=self.editor_tag, callback=self.link_callback, delink_callback=self.delink_callback): pass 

        dpg.create_viewport(title='PyGui Visual Scripting (Refactored Complete)', width=1280, height=800, vsync=True)
        dpg.setup_dearpygui()
        dpg.set_primary_window(self.window_tag, True)
        dpg.show_viewport()

    def create_and_draw(self, node_type):
        node = NodeFactory.create_node(node_type)
        if node:
            self.draw_node(node)
            self.engine.add_node(node)

    def draw_node(self, node: Any):
        with dpg.node(tag=node.node_id, parent=self.editor_tag, label=node.label):
            for pin_type, label, default_val in node.get_ui_schema():
                # ★ DPG가 멋대로 번호를 붙이지 못하도록, 불러올 때 매칭할 수 있는 고정 태그 생성!
                pin_tag = f"{node.node_id}_{label}"
                self.pin_label_map[pin_tag] = label 
                
                if pin_type == "IN_FLOW":
                    with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=pin_tag): dpg.add_text(label)
                elif pin_type == "OUT_FLOW":
                    with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=pin_tag): dpg.add_text(label)
                elif pin_type == "IN_DATA":
                    with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=pin_tag):
                        with dpg.group(horizontal=True):
                            dpg.add_text(label, color=(255, 255, 0))
                            if default_val is not None: 
                                # ★ 해결: 문자열(IP)은 길게(120), 정수형(Port)은 소수점 없이(int) 그림
                                if isinstance(default_val, str):
                                    dpg.add_input_text(width=120, default_value=default_val, tag=f"val_{node.node_id}_{label}")
                                elif isinstance(default_val, int):
                                    dpg.add_input_int(width=70, default_value=default_val, step=0, tag=f"val_{node.node_id}_{label}")
                                else:
                                    dpg.add_input_float(width=70, default_value=default_val, step=0, tag=f"val_{node.node_id}_{label}")
                elif pin_type == "OUT_DATA":
                    with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=pin_tag): dpg.add_text(label)

            if node.get_settings_schema():
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                    dpg.add_spacer(height=5); dpg.add_separator()
                    for param_name, default_val in node.get_settings_schema():
                        with dpg.group(horizontal=True):
                            dpg.add_text(param_name)
                            # ★ 해결: 여기도 마찬가지로 정수/문자열/실수 크기 분리
                            if isinstance(default_val, str):
                                dpg.add_input_text(width=120, default_value=default_val, tag=f"{node.node_id}_set_{param_name}")
                            elif isinstance(default_val, int):
                                dpg.add_input_int(width=60, default_value=default_val, step=0, tag=f"{node.node_id}_set_{param_name}")
                            else:
                                dpg.add_input_float(width=60, default_value=default_val, step=0, tag=f"{node.node_id}_set_{param_name}")

    def link_callback(self, sender, app_data):
        src, dst = app_data[0], app_data[1]
        src_node = dpg.get_item_parent(src); dst_node = dpg.get_item_parent(dst)
        lid = dpg.add_node_link(src, dst, parent=sender)
        
        # ★ 추가: 엔진에는 복잡한 DPG 태그가 아닌 'Target X' 같은 순정 라벨을 넘겨줍니다.
        src_label = self.pin_label_map.get(src, src)
        dst_label = self.pin_label_map.get(dst, dst)
        self.engine.add_link(lid, src_node, src_label, dst_node, dst_label)

    def delink_callback(self, sender, app_data):
        if self.is_bulk_loading:
            return
        lid = app_data
        if dpg.does_item_exist(lid):
            dpg.delete_item(lid)
        self.engine.remove_link(lid)

    def delete_selection(self, sender, app_data):
        selected_links = list(dpg.get_selected_links(self.editor_tag))
        selected_nodes = list(dpg.get_selected_nodes(self.editor_tag))

        for lid in selected_links:
            if dpg.does_item_exist(lid):
                dpg.delete_item(lid)
            self.engine.remove_link(lid)

        # DPG 내부 상태 안정성을 위해 노드 삭제 전에 연결선을 명시적으로 정리합니다.
        for nid in selected_nodes:
            connected = [
                link for link in list(self.engine.links)
                if link["src_id"] == nid or link["dst_id"] == nid
            ]
            for link in connected:
                lid = link.get("id")
                if lid is not None and dpg.does_item_exist(lid):
                    dpg.delete_item(lid)
                self.engine.remove_link(lid)

            if dpg.does_item_exist(nid):
                dpg.delete_item(nid)
            self.engine.remove_node(nid)

    def toggle_run(self, sender, app_data):
        """엔진의 실행/정지 상태를 토글하는 콜백"""
        if dpg.get_item_label(sender) == "RUN SCRIPT":
            self.engine.start()
            dpg.set_item_label(sender, "STOP SCRIPT")
        else:
            self.engine.stop()
            dpg.set_item_label(sender, "RUN SCRIPT")

    def handle_save_graph(self, sender=None, app_data=None):
        filename = dpg.get_value("file_name_input") if dpg.does_item_exist("file_name_input") else "my_graph"
        GraphSerializer.save_graph(filename, self.engine)
        if dpg.does_item_exist("file_list_combo"):
            dpg.configure_item("file_list_combo", items=GraphSerializer.get_save_files())

    def handle_load_graph(self, sender=None, app_data=None):
        filename = dpg.get_value("file_list_combo") if dpg.does_item_exist("file_list_combo") else ""
        if not filename:
            return

        # Answer_code와 동일하게 로드 전 실행 상태를 멈추고 진행합니다.
        self.engine.stop()
        if dpg.does_item_exist("btn_run"):
            dpg.set_item_label("btn_run", "RUN SCRIPT")

        GraphSerializer.load_graph(filename, self.engine, self)

    def sync_ui_to_nodes(self):
        # 노드 UI 위젯 값을 매 프레임 노드 런타임 상태로 동기화합니다.
        focused_item = dpg.get_focused_item()
        driver_label_to_key = {
            "X": "x",
            "Y": "y",
            "Z": "z",
            "Roll": "roll",
            "Gripper": "gripper",
        }

        for node in self.engine.nodes.values():
            for pin_type, label, default_val in node.get_ui_schema():
                if pin_type != "IN_DATA":
                    continue

                has_incoming_link = any(
                    link.get("dst_id") == node.node_id and link.get("dst_pin") == label
                    for link in self.engine.links
                )
                value_tag = f"val_{node.node_id}_{label}"

                # MT4 드라이버는 링크 입력이 없을 때 고정 기본값으로 덮어쓰지 않고,
                # 현재 타겟 상태를 UI에 반영합니다. (Answer_code.py의 UniversalRobotNode.execute 동기화 로직과 동일)
                if node.type_str == "MT4_DRIVER" and label in driver_label_to_key:
                    if dpg.does_item_exist(value_tag):
                        if focused_item == value_tag: # 유저가 직접 입력 중
                            node.inputs[label] = dpg.get_value(value_tag)
                        else:
                            from nodes.robots.mt4 import mt4_target_goal
                            curr_val = float(mt4_target_goal[driver_label_to_key[label]])
                            dpg.set_value(value_tag, curr_val)
                            
                            # 만약 링크가 연결되어 있지 않은 상태라면, UI의 값(=방금 동기화한 현재 타겟) 혹은 None을 주입
                            if not has_incoming_link:
                                node.inputs[label] = curr_val
                    continue

                if not has_incoming_link:
                    if default_val is not None and dpg.does_item_exist(value_tag):
                        node.inputs[label] = dpg.get_value(value_tag)

            for param_name, _ in node.get_settings_schema():
                setting_tag = f"{node.node_id}_set_{param_name}"
                if dpg.does_item_exist(setting_tag):
                    node.settings[param_name] = dpg.get_value(setting_tag)

    def render_frame(self): dpg.render_dearpygui_frame()
    def is_running(self): return dpg.is_dearpygui_running()
    def cleanup(self): dpg.destroy_context()

    