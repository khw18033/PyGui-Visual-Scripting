import dearpygui.dearpygui as dpg
import time
import os
import socket
import json
from abc import ABC, abstractmethod

# ================= [전역 설정] =================
node_registry = {}  # {node_id: NodeInstance}
link_registry = {}  # {link_id: {source: id, target: id}}

# ================= [1. 기반 클래스] =================
class BaseNode(ABC):
    def __init__(self, node_id, label):
        self.node_id = node_id
        self.label = label
        self.inputs = {}      
        self.outputs = {}     
        self.output_data = {} 

    @abstractmethod
    def build_ui(self):
        pass

    @abstractmethod
    def execute(self):
        pass

    def fetch_input_data(self, input_attr_id):
        target_link = None
        for link in link_registry.values():
            if link['target'] == input_attr_id:
                target_link = link
                break
        
        if not target_link: return None 

        source_attr_id = target_link['source']
        source_node_id = dpg.get_item_parent(source_attr_id)
        
        if source_node_id in node_registry:
            source_node = node_registry[source_node_id]
            return source_node.output_data.get(source_attr_id)
        return None

# ================= [2. 노드 클래스 구현] =================

class StartNode(BaseNode):
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="시작 (START)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_id:
                dpg.add_text("출력 흐름")
            self.outputs[out_id] = "Flow"

    def execute(self):
        print("[시스템] 시작 노드 실행")
        return self.outputs

class UDPReceiverNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "UDP 수신 (RECV)")
        self.port_input = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.is_bound = False
        self.data_out_id = None 

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_id:
                dpg.add_text("입력 흐름")
            self.inputs[in_id] = "Flow"

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.port_input = dpg.add_input_int(label="Port", width=100, default_value=6000)

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as d_out:
                dpg.add_text("데이터 출력 (String)")
            self.outputs[d_out] = "Data"
            self.data_out_id = d_out 
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out:
                dpg.add_text("출력 흐름")
            self.outputs[f_out] = "Flow"

    def execute(self):
        port = dpg.get_value(self.port_input)
        
        if not self.is_bound:
            try:
                self.sock.bind(('0.0.0.0', port))
                self.is_bound = True
                print(f"[UDP] 포트 {port} 열림")
            except Exception as e:
                print(f"[오류] 포트 바인딩 실패: {e}")
                return self.outputs

        try:
            data, addr = self.sock.recvfrom(4096)
            decoded_data = data.decode()
            print(f"[UDP] 데이터 수신됨 ({len(data)} bytes)")
            self.output_data[self.data_out_id] = decoded_data
            
        except BlockingIOError:
            print("[UDP] 들어온 데이터 없음 (Skip)")
            self.output_data[self.data_out_id] = None 
        except Exception as e:
            print(f"[오류] {e}")

        return self.outputs

class PrintNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "출력 (PRINT)")
        self.input_field = None
        self.data_in_id = None 

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_id:
                dpg.add_text("입력 흐름")
            self.inputs[in_id] = "Flow"

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d_in:
                dpg.add_text("데이터 입력 (Any)")
            self.inputs[d_in] = "Data"
            self.data_in_id = d_in

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.input_field = dpg.add_input_text(label="기본 메시지", width=120, default_value="Test")

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_id:
                dpg.add_text("출력 흐름")
            self.outputs[out_id] = "Flow"

    def execute(self):
        received_data = self.fetch_input_data(self.data_in_id)
        
        if received_data is not None:
            print(f"🖨️ [출력] (Data 수신): {received_data}")
        else:
            text = dpg.get_value(self.input_field)
            print(f"🖨️ [출력] (기본 Text): {text}")
            
        return self.outputs

# ================= [3. 팩토리 & 실행 엔진] =================
class NodeFactory:
    @staticmethod
    def create_node(node_type):
        node_id = dpg.generate_uuid()
        node = None
        if node_type == "START": node = StartNode(node_id, "START")
        elif node_type == "PRINT": node = PrintNode(node_id)
        elif node_type == "UDP_RECV": node = UDPReceiverNode(node_id)
            
        if node:
            node.build_ui()
            node_registry[node_id] = node
            return node
        return None

def execute_graph():
    print("\n--- [스크립트 실행] ---")
    
    start_node = None
    for node in node_registry.values():
        if isinstance(node, StartNode):
            start_node = node
            break
            
    if not start_node:
        print("[오류] START 노드가 없습니다.")
        return

    current_node = start_node
    while current_node:
        outputs = current_node.execute()
        
        next_node = None
        for out_attr_id, out_type in outputs.items():
            if out_type == "Flow":
                for link in link_registry.values():
                    if link['source'] == out_attr_id:
                        target_node_id = dpg.get_item_parent(link['target'])
                        if target_node_id in node_registry:
                            next_node = node_registry[target_node_id]
                        break
            if next_node: break 
            
        current_node = next_node
        time.sleep(0.05)

    print("--- [실행 종료] ---")

# ================= [★ 중요: 삭제 기능 추가] =================
def delete_selection(sender, app_data):
    """Del 키를 누르면 선택된 노드와 링크를 삭제하는 함수"""
    # 1. 선택된 링크 삭제
    selected_links = dpg.get_selected_links("node_editor")
    for link_id in selected_links:
        dpg.delete_item(link_id)
        if link_id in link_registry:
            del link_registry[link_id]
            
    # 2. 선택된 노드 삭제
    selected_nodes = dpg.get_selected_nodes("node_editor")
    for node_id in selected_nodes:
        dpg.delete_item(node_id)
        if node_id in node_registry:
            del node_registry[node_id]
    
    # 로그 출력 (확인용)
    if selected_nodes or selected_links:
        print(f"[삭제] 노드 {len(selected_nodes)}개, 링크 {len(selected_links)}개 삭제됨")

# ================= [GUI 이벤트 핸들러] =================
def link_cb(sender, app_data):
    if len(app_data) == 3: src, dst = app_data[1], app_data[2]
    else: src, dst = app_data[0], app_data[1]
    
    link_id = dpg.add_node_link(src, dst, parent=sender)
    link_registry[link_id] = {'source': src, 'target': dst}

def del_link_cb(sender, app_data):
    # 링크를 우클릭 등으로 지웠을 때 (기본 기능)
    dpg.delete_item(app_data)
    if app_data in link_registry: del link_registry[app_data]

def add_node_cb(sender, app_data, user_data):
    NodeFactory.create_node(user_data)

# ================= [메인 윈도우 구성] =================
dpg.create_context()

# 한글 폰트
font_path = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
with dpg.font_registry():
    if os.path.exists(font_path):
        with dpg.font(font_path, 18) as kr_font:
            dpg.add_font_range_hint(dpg.mvFontRangeHint_Default)
            dpg.add_font_range_hint(dpg.mvFontRangeHint_Korean)
        dpg.bind_font(kr_font)

# ★ 키보드 입력 감지기 등록 (Del 키)
with dpg.handler_registry():
    dpg.add_key_press_handler(dpg.mvKey_Delete, callback=delete_selection)

with dpg.window(label="Visual Scripting Tool V3 (Delete Support)", width=900, height=700):
    
    with dpg.group(horizontal=True):
        dpg.add_button(label="START 추가", callback=add_node_cb, user_data="START")
        dpg.add_button(label="PRINT 추가", callback=add_node_cb, user_data="PRINT")
        dpg.add_button(label="UDP 수신 추가", callback=add_node_cb, user_data="UDP_RECV")
        dpg.add_spacer(width=50)
        dpg.add_button(label="▶ 스크립트 실행", callback=execute_graph, width=150)

    dpg.add_separator()
    dpg.add_text("Tip: [Delete] 키를 눌러 선택한 노드나 연결선을 삭제할 수 있습니다.")

    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb):
        pass

dpg.create_viewport(title='PyGui Editor V3', width=900, height=700)
dpg.setup_dearpygui()
dpg.show_viewport()
dpg.start_dearpygui()
dpg.destroy_context()