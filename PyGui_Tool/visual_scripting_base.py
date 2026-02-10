import dearpygui.dearpygui as dpg
import time

# ================= [데이터 구조] =================
# 노드와 링크 정보를 저장할 저장소
nodes = {}       # {node_id: {type: "PRINT", ...}}
links = {}       # {link_id: {source: attr_id, target: attr_id}}
node_counter = 0 # 노드 ID 발급용

# ================= [실행 엔진 (핵심)] =================
def execute_graph():
    """
    Start 노드를 찾아 연결된 순서대로 로직을 실행하는 함수
    (교수님이 원하시는 '시퀀스 실행' 기능)
    """
    print("--- [Execution Start] ---")
    
    # 1. Start 노드 찾기
    current_node_id = None
    for nid, info in nodes.items():
        if info['type'] == "START":
            current_node_id = nid
            break
            
    if current_node_id is None:
        print("Error: 'Start' 노드가 없습니다.")
        return

    # 2. 링크를 타고 다음 노드로 이동하며 실행
    while current_node_id is not None:
        node_info = nodes[current_node_id]
        node_type = node_info['type']
        
        # --- 노드별 기능 실행 (여기에 나중에 로봇 코드가 들어감) ---
        if node_type == "START":
            print("▶️ Start")
            
        elif node_type == "PRINT":
            # 입력창에서 텍스트 가져오기
            text = dpg.get_value(node_info['input_tag'])
            print(f"🖨️ Print: {text}")
            
        elif node_type == "DELAY":
            sec = dpg.get_value(node_info['input_tag'])
            print(f"⏳ Waiting {sec} seconds...")
            time.sleep(sec) # 실제 딜레이
            
        # 3. 다음 노드 찾기 (Output 속성에 연결된 링크 찾기)
        output_attr = node_info['out_attr']
        next_link_id = None
        
        # 모든 링크 중 현재 노드의 output에서 시작하는 링크 검색
        for lid, link_data in links.items():
            if link_data['source'] == output_attr:
                next_link_id = lid
                break
        
        if next_link_id:
            # 링크의 목적지(target) 속성이 속한 노드 ID 찾기
            target_attr = links[next_link_id]['target']
            current_node_id = dpg.get_item_parent(target_attr)
        else:
            print("--- [End of Chain] ---")
            current_node_id = None # 더 이상 연결된 노드 없음

# ================= [GUI 이벤트 콜백] =================
def link_callback(sender, app_data):
    # 노드 연결 시 호출됨 (선 그리기)
    link_id = dpg.add_node_link(app_data[0], app_data[1], parent=sender)
    links[link_id] = {'source': app_data[0], 'target': app_data[1]}

def del_link_callback(sender, app_data):
    # 연결 선 삭제 시 호출됨
    dpg.delete_item(app_data)
    if app_data in links:
        del links[app_data]

def add_node(sender, app_data, user_data):
    global node_counter
    node_type = user_data
    node_counter += 1
    
    # 노드 생성
    with dpg.node(parent="node_editor", label=node_type, tag=f"node_{node_counter}") as new_node:
        
        # 노드별 속성(Attribute) 정의
        if node_type == "START":
            # Start는 Output만 있음
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_attr:
                dpg.add_text("Flow Out")
            nodes[new_node] = {'type': "START", 'out_attr': out_attr}
            
        elif node_type == "PRINT":
            # Print는 Input(Flow) + 입력창 + Output(Flow)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input):
                dpg.add_text("Flow In")
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                input_field = dpg.add_input_text(label="Message", width=100, default_value="Hello")
                
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_attr:
                dpg.add_text("Flow Out")
            
            nodes[new_node] = {'type': "PRINT", 'out_attr': out_attr, 'input_tag': input_field}
            
        elif node_type == "DELAY":
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input):
                dpg.add_text("Flow In")
                
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                input_field = dpg.add_input_float(label="Seconds", width=100, default_value=1.0)
                
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_attr:
                dpg.add_text("Flow Out")
                
            nodes[new_node] = {'type': "DELAY", 'out_attr': out_attr, 'input_tag': input_field}

# ================= [메인 GUI 구성] =================
dpg.create_context()

with dpg.window(label="Visual Scripting Tool", width=800, height=600):
    
    # 1. 상단 툴바 (노드 추가 버튼들)
    with dpg.group(horizontal=True):
        dpg.add_button(label="Add START", callback=add_node, user_data="START")
        dpg.add_button(label="Add PRINT", callback=add_node, user_data="PRINT")
        dpg.add_button(label="Add DELAY", callback=add_node, user_data="DELAY")
        dpg.add_spacer(width=50)
        dpg.add_button(label="▶ RUN SCRIPT", callback=execute_graph, width=100)

    dpg.add_separator()

    # 2. 노드 에디터 영역
    with dpg.node_editor(tag="node_editor", callback=link_callback, delink_callback=del_link_callback):
        pass # 처음엔 비어있음

dpg.create_viewport(title='PyGui Visual Scripting', width=800, height=600)
dpg.setup_dearpygui()
dpg.show_viewport()
dpg.start_dearpygui()
dpg.destroy_context()