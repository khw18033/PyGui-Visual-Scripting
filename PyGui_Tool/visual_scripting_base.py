import dearpygui.dearpygui as dpg
import time
import os

# ================= [데이터 구조] =================
nodes = {}       
links = {}       

# ================= [실행 엔진] =================
def execute_graph():
    print("\n--- [Execution Start] ---")
    
    # 1. Start 노드 찾기
    current_node_id = None
    for nid, info in nodes.items():
        if info['type'] == "START":
            current_node_id = nid
            break
            
    if current_node_id is None:
        print("[Error] 'START' Node not found.")
        return

    # 2. 링크를 타고 다음 노드로 이동하며 실행
    while current_node_id is not None:
        try:
            node_info = nodes[current_node_id]
        except KeyError:
            print(f"[Error] Node ID {current_node_id} not found.")
            break

        node_type = node_info['type']
        
        # --- [노드별 기능 실행] ---
        if node_type == "START":
            print("[System] Start")
            
        elif node_type == "PRINT":
            text = dpg.get_value(node_info['input_tag'])
            print(f"[Print] {text}")
            
        elif node_type == "DELAY":
            sec = dpg.get_value(node_info['input_tag'])
            print(f"[Delay] Waiting {sec}s...")
            time.sleep(sec) 
            
        # 3. 다음 노드 찾기
        output_attr = node_info['out_attr']
        next_link_id = None
        
        for lid, link_data in links.items():
            if link_data['source'] == output_attr:
                next_link_id = lid
                break
        
        if next_link_id:
            target_attr = links[next_link_id]['target']
            current_node_id = dpg.get_item_parent(target_attr)
        else:
            print("--- [Execution Finished] ---")
            current_node_id = None 

# ================= [GUI 이벤트 콜백] =================
def link_callback(sender, app_data):
    link_id = dpg.add_node_link(app_data[1], app_data[2], parent=sender)
    links[link_id] = {'source': app_data[1], 'target': app_data[2]}

def del_link_callback(sender, app_data):
    dpg.delete_item(app_data)
    if app_data in links:
        del links[app_data]

def add_node(sender, app_data, user_data):
    node_type = user_data
    
    with dpg.node(parent="node_editor", label=node_type) as new_node:
        
        if node_type == "START":
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_attr:
                dpg.add_text("Flow Out")
            nodes[new_node] = {'type': "START", 'out_attr': out_attr}
            
        elif node_type == "PRINT":
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

# 한글 폰트 설정
font_path = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
with dpg.font_registry():
    if os.path.exists(font_path):
        with dpg.font(font_path, 18) as kr_font:
            dpg.add_font_range_hint(dpg.mvFontRangeHint_Default)
            dpg.add_font_range_hint(dpg.mvFontRangeHint_Korean)
        dpg.bind_font(kr_font)
        print("[System] Korean font loaded.")
    else:
        print(f"[System] Font not found: {font_path}")

with dpg.window(label="Visual Scripting Tool", width=800, height=600):
    
    # 1. 상단 툴바
    with dpg.group(horizontal=True):
        dpg.add_button(label="Add START", callback=add_node, user_data="START")
        dpg.add_button(label="Add PRINT", callback=add_node, user_data="PRINT")
        dpg.add_button(label="Add DELAY", callback=add_node, user_data="DELAY")
        dpg.add_spacer(width=50)
        dpg.add_button(label="RUN SCRIPT", callback=execute_graph, width=150)

    dpg.add_separator()
    dpg.add_text("Add nodes and connect dots. Press [Del] to remove links.")

    # 2. 노드 에디터 영역
    with dpg.node_editor(tag="node_editor", callback=link_callback, delink_callback=del_link_callback):
        pass 

dpg.create_viewport(title='PyGui Visual Scripting', width=800, height=600)
dpg.setup_dearpygui()
dpg.show_viewport()
dpg.start_dearpygui()
dpg.destroy_context()