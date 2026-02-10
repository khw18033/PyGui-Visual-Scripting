import dearpygui.dearpygui as dpg
import time
import os

# ================= [데이터 구조] =================
# 노드와 링크 정보를 저장할 저장소
nodes = {}       # {node_id: {type: "PRINT", ...}}
links = {}       # {link_id: {source: attr_id, target: attr_id}}

# ================= [실행 엔진 (핵심 로직)] =================
def execute_graph():
    """
    Start 노드를 찾아 연결된 순서대로 로직을 실행하는 함수
    """
    print("\n--- [Execution Start] ---")
    
    # 1. Start 노드 찾기
    current_node_id = None
    for nid, info in nodes.items():
        if info['type'] == "START":
            current_node_id = nid
            break
            
    if current_node_id is None:
        print("❌ Error: 'START' 노드가 없습니다.")
        return

    # 2. 링크를 타고 다음 노드로 이동하며 실행
    while current_node_id is not None:
        # ID로 노드 정보 가져오기 (try-except로 안전장치)
        try:
            node_info = nodes[current_node_id]
        except KeyError:
            print(f"⚠️ Node ID {current_node_id} 정보를 찾을 수 없습니다.")
            break

        node_type = node_info['type']
        
        # --- [노드별 기능 실행] ---
        if node_type == "START":
            print("🚀 시작 (Start)")
            
        elif node_type == "PRINT":
            # 입력창에서 텍스트 가져오기
            text = dpg.get_value(node_info['input_tag'])
            print(f"🖨️ 출력: {text}")
            
        elif node_type == "DELAY":
            sec = dpg.get_value(node_info['input_tag'])
            print(f"⏳ 대기: {sec}초...")
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
            print("--- [Execution Finished] ---")
            current_node_id = None # 더 이상 연결된 노드 없음

# ================= [GUI 이벤트 콜백] =================
def link_callback(sender, app_data):
    # app_data: (link_id, attr1, attr2)
    # 0번은 링크 ID, 1번과 2번이 연결된 속성들
    link_id = dpg.add_node_link(app_data[1], app_data[2], parent=sender)
    links[link_id] = {'source': app_data[1], 'target': app_data[2]}

def del_link_callback(sender, app_data):
    # 연결 선 삭제 시 호출됨
    dpg.delete_item(app_data)
    if app_data in links:
        del links[app_data]

def add_node(sender, app_data, user_data):
    node_type = user_data
    
    # [수정] tag를 지정하지 않고, 리턴받은 ID(new_node)를 key로 사용
    with dpg.node(parent="node_editor", label=node_type) as new_node:
        
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
                input_field = dpg.add_input_text(label="Message", width=120, default_value="Hello Robot")
                
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

# ★ [한글 폰트 적용 로직]
font_path = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
with dpg.font_registry():
    if os.path.exists(font_path):
        with dpg.font(font_path, 18) as kr_font:
            dpg.add_font_range_hint(dpg.mvFontRangeHint_Default)
            dpg.add_font_range_hint(dpg.mvFontRangeHint_Korean)
        dpg.bind_font(kr_font)
        print("[System] 한글 폰트 로드 성공")
    else:
        print(f"[System] 폰트 파일을 찾을 수 없습니다: {font_path}")
        print("         (sudo apt install fonts-nanum 명령어로 설치 필요)")

with dpg.window(label="Visual Scripting Tool", width=800, height=600):
    
    # 1. 상단 툴바
    with dpg.group(horizontal=True):
        dpg.add_button(label="➕ START 추가", callback=add_node, user_data="START")
        dpg.add_button(label="➕ PRINT 추가", callback=add_node, user_data="PRINT")
        dpg.add_button(label="➕ DELAY 추가", callback=add_node, user_data="DELAY")
        dpg.add_spacer(width=50)
        dpg.add_button(label="▶️ 스크립트 실행 (RUN)", callback=execute_graph, width=150)

    dpg.add_separator()
    dpg.add_text("노드를 추가하고 점끼리 드래그하여 연결하세요. [Del]키로 연결 삭제 가능.")

    # 2. 노드 에디터 영역
    with dpg.node_editor(tag="node_editor", callback=link_callback, delink_callback=del_link_callback):
        pass 

dpg.create_viewport(title='PyGui Visual Scripting', width=800, height=600)
dpg.setup_dearpygui()
dpg.show_viewport()
dpg.start_dearpygui()
dpg.destroy_context()