import os
import json
from core.engine import SAVE_DIR, write_log, node_registry, link_registry
from core.factory import NodeFactory

def get_save_files(): 
    return [f for f in os.listdir(SAVE_DIR) if f.endswith(".json")]

def save_graph(filename):
    from ui.dpg_manager import get_item_pos_safe
    
    if not filename.endswith(".json"): filename += ".json"
    filepath = os.path.join(SAVE_DIR, filename)
    data = {"nodes": [], "links": []}
    
    for nid, node in node_registry.items():
        pos = get_item_pos_safe(nid) or [0,0]
        data["nodes"].append({
            "type": node.type_str, 
            "id": nid, 
            "pos": pos, 
            "settings": node.get_settings()
        })
        
    for lid, link in link_registry.items():
        src_node_id = link['src_node_id']
        dst_node_id = link['dst_node_id']
        if src_node_id in node_registry and dst_node_id in node_registry:
            src_idx = list(node_registry[src_node_id].outputs.keys()).index(link['source'])
            dst_idx = list(node_registry[dst_node_id].inputs.keys()).index(link['target'])
            data["links"].append({
                "src_node": src_node_id, 
                "src_idx": src_idx, 
                "dst_node": dst_node_id, 
                "dst_idx": dst_idx
            })
            
    try:
        with open(filepath, 'w') as f: 
            json.dump(data, f, indent=4)
        write_log(f"Saved: {filename}")
    except Exception as e: 
        write_log(f"Save Err: {e}")

def load_graph(filename):
    from ui.dpg_manager import clear_editor, NodeUIRenderer, set_item_pos_safe, add_dpg_link
    
    if not filename.endswith(".json"): filename += ".json"
    filepath = os.path.join(SAVE_DIR, filename)
    if not os.path.exists(filepath): return
    
    clear_editor()
    
    try:
        with open(filepath, 'r') as f: 
            data = json.load(f)
            
        id_map = {}
        for n_data in data["nodes"]:
            node = NodeFactory.create_node(n_data["type"], n_data.get("id"))
            if node:
                id_map[n_data["id"]] = node.node_id
                NodeUIRenderer.render(node)
                set_item_pos_safe(node.node_id, n_data["pos"] if n_data["pos"] else [0,0])
                node.load_settings(n_data.get("settings", {}))
                NodeUIRenderer.sync_state_to_ui(node)
                
        for l_data in data["links"]:
            if l_data["src_node"] in id_map and l_data["dst_node"] in id_map:
                src_node = node_registry[id_map[l_data["src_node"]]]
                dst_node = node_registry[id_map[l_data["dst_node"]]]
                src_attr = list(src_node.outputs.keys())[l_data["src_idx"]]
                dst_attr = list(dst_node.inputs.keys())[l_data["dst_idx"]]

                add_dpg_link(src_attr, dst_attr, id_map[l_data["src_node"]], id_map[l_data["dst_node"]])
                
        write_log(f"Loaded: {filename}")
    except Exception as e: 
        write_log(f"Load Err: {e}")
