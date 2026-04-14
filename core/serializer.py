import os
import json
import dearpygui.dearpygui as dpg
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
    if not os.path.exists(SAVE_DIR):
        return []
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

    if not isinstance(filename, str):
        write_log("Load Err: 선택된 파일이 없습니다.")
        return
    filename = filename.strip()
    if not filename:
        write_log("Load Err: 선택된 파일이 없습니다.")
        return

    if not filename.endswith(".json"):
        filename += ".json"
    filepath = os.path.join(SAVE_DIR, filename)
    if not os.path.exists(filepath):
        write_log(f"Load Err: 파일을 찾을 수 없습니다. ({filename})")
        return
    
    clear_editor()
    
    try:
        with open(filepath, 'r') as f: 
            data = json.load(f)
            
        id_map = {}
        for n_data in data.get("nodes", []):
            if not isinstance(n_data, dict):
                continue
            node_type = n_data.get("type")
            if not node_type:
                write_log("Load Warn: type 정보가 없는 노드를 건너뜁니다.")
                continue
            settings = n_data.get("settings", {})

            # 과거 버그로 GO1_DRIVER가 MT4_DRIVER로 저장된 파일을 자동 보정한다.
            if node_type == "MT4_DRIVER" and any(k in settings for k in ["vx", "vy", "vyaw", "body_height"]):
                node_type = "GO1_DRIVER"

            old_id = n_data.get("id")
            if old_id is None:
                write_log("Load Warn: id 정보가 없는 노드를 건너뜁니다.")
                continue

            # 저장 파일의 node id를 그대로 재사용하면 DPG tag 충돌이 발생할 수 있어,
            # 로드시에는 항상 새 id를 발급한다.
            node = NodeFactory.create_node(node_type)
            if not node:
                write_log(f"Load Warn: 알 수 없는 노드 타입을 건너뜁니다. ({node_type})")
                continue

            try:
                NodeUIRenderer.render(node)
                pos = n_data.get("pos")
                set_item_pos_safe(node.node_id, pos if pos else [0,0])
                node.load_settings(settings)
                NodeUIRenderer.sync_state_to_ui(node)
                id_map[str(old_id)] = node.node_id
            except Exception as node_err:
                # 개별 노드 복원 실패가 전체 로드를 중단하지 않도록 격리한다.
                try:
                    if dpg.does_item_exist(node.node_id):
                        dpg.delete_item(node.node_id)
                except Exception:
                    pass
                node_registry.pop(node.node_id, None)
                write_log(f"Load Warn: 노드 복원 실패(type={node_type}, id={old_id}) - {node_err}")
                
        for l_data in data.get("links", []):
            if not isinstance(l_data, dict):
                continue
            src_old = l_data.get("src_node")
            dst_old = l_data.get("dst_node")
            src_key = str(src_old) if src_old is not None else None
            dst_key = str(dst_old) if dst_old is not None else None
            if src_key in id_map and dst_key in id_map:
                src_node = node_registry[id_map[src_key]]
                dst_node = node_registry[id_map[dst_key]]
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
                    add_dpg_link(src_attr, dst_attr, id_map[src_key], id_map[dst_key])
                else:
                    write_log(
                        f"Load Warn: Skipped incompatible link src={l_data.get('src_node')} dst={l_data.get('dst_node')}"
                    )
                
        write_log(f"Loaded: {filename}")
    except Exception as e: 
        write_log(f"Load Err: {e}")
