import os
import json
from core.engine import SAVE_DIR, write_log, node_registry, link_registry
from core.factory import NodeFactory


def _find_attr_name_for_port(node, port_id, io_kind):
    """Find a stable symbolic name for a port id from node attributes/maps."""
    if io_kind == "input":
        pin_map = getattr(node, "in_pins", {})
        for k, v in pin_map.items():
            if v == port_id:
                return f"in_pins:{k}"
        set_map = getattr(node, "setting_pins", {})
        for k, v in set_map.items():
            if v == port_id:
                return f"setting_pins:{k}"

    for name, val in vars(node).items():
        if isinstance(val, str) and val == port_id:
            if io_kind == "input" and port_id in node.inputs:
                return name
            if io_kind == "output" and port_id in node.outputs:
                return name
    return None


def _resolve_port_from_name(node, io_kind, name):
    if not name:
        return None
    if name.startswith("in_pins:"):
        key = name.split(":", 1)[1]
        return getattr(node, "in_pins", {}).get(key)
    if name.startswith("setting_pins:"):
        key = name.split(":", 1)[1]
        return getattr(node, "setting_pins", {}).get(key)
    return getattr(node, name, None)


def _resolve_port_with_fallback(node, io_kind, saved_name=None, saved_idx=None, saved_attr=None):
    ports = list(node.outputs.keys()) if io_kind == "output" else list(node.inputs.keys())

    # 1) preferred: symbolic name
    pid = _resolve_port_from_name(node, io_kind, saved_name)
    if isinstance(pid, str) and pid in (node.outputs if io_kind == "output" else node.inputs):
        return pid

    # 2) backward compatibility: index
    if isinstance(saved_idx, int) and 0 <= saved_idx < len(ports):
        return ports[saved_idx]

    # 3) last resort: raw saved attr id (works when id stable)
    if isinstance(saved_attr, str) and saved_attr in (node.outputs if io_kind == "output" else node.inputs):
        return saved_attr

    return None

def get_save_files(): 
    return [f for f in os.listdir(SAVE_DIR) if f.endswith(".json")]

def save_graph(filename):
    from ui.dpg_manager import get_item_pos_safe, NodeUIRenderer

    # 실행 중이 아니어도 현재 UI 입력값을 state에 반영해 저장 정합성을 보장한다.
    NodeUIRenderer.sync_ui_to_state()

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
            src_node = node_registry[src_node_id]
            dst_node = node_registry[dst_node_id]
            try:
                src_idx = list(src_node.outputs.keys()).index(link['source'])
                dst_idx = list(dst_node.inputs.keys()).index(link['target'])
            except ValueError:
                # stale link entry safety guard
                continue

            data["links"].append({
                "src_node": src_node_id,
                "src_idx": src_idx,
                "dst_node": dst_node_id,
                "dst_idx": dst_idx,
                # New robust metadata (kept with index for backward compatibility)
                "src_attr": link.get('source'),
                "dst_attr": link.get('target'),
                "src_name": _find_attr_name_for_port(src_node, link.get('source'), "output"),
                "dst_name": _find_attr_name_for_port(dst_node, link.get('target'), "input"),
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
            node_type = n_data["type"]
            settings = n_data.get("settings", {})

            # 과거 버그로 GO1_DRIVER가 MT4_DRIVER로 저장된 파일을 자동 보정한다.
            if node_type == "MT4_DRIVER" and any(k in settings for k in ["vx", "vy", "vyaw", "body_height"]):
                node_type = "GO1_DRIVER"

            node = NodeFactory.create_node(node_type, n_data.get("id"))
            if node:
                id_map[n_data["id"]] = node.node_id
                NodeUIRenderer.render(node)
                set_item_pos_safe(node.node_id, n_data["pos"] if n_data["pos"] else [0,0])
                node.load_settings(settings)
                NodeUIRenderer.sync_state_to_ui(node)
                
        for l_data in data["links"]:
            if l_data["src_node"] in id_map and l_data["dst_node"] in id_map:
                src_node = node_registry[id_map[l_data["src_node"]]]
                dst_node = node_registry[id_map[l_data["dst_node"]]]
                src_attr = _resolve_port_with_fallback(
                    src_node,
                    "output",
                    saved_name=l_data.get("src_name"),
                    saved_idx=l_data.get("src_idx"),
                    saved_attr=l_data.get("src_attr"),
                )
                dst_attr = _resolve_port_with_fallback(
                    dst_node,
                    "input",
                    saved_name=l_data.get("dst_name"),
                    saved_idx=l_data.get("dst_idx"),
                    saved_attr=l_data.get("dst_attr"),
                )

                if src_attr and dst_attr:
                    add_dpg_link(src_attr, dst_attr, id_map[l_data["src_node"]], id_map[l_data["dst_node"]])
                else:
                    write_log(
                        f"Load Warn: Skipped incompatible link src={l_data.get('src_node')} dst={l_data.get('dst_node')}"
                    )
                
        write_log(f"Loaded: {filename}")
    except Exception as e: 
        write_log(f"Load Err: {e}")
