import os
from collections import deque
from datetime import datetime
from enum import Enum, auto

class HwStatus(Enum):
    OFFLINE = auto()
    ONLINE = auto()
    SIMULATION = auto()

class PortType(Enum):
    FLOW = auto()
    DATA = auto()

node_registry = {}
link_registry = {}
is_running = False
SAVE_DIR = "Node_File_MT4"
if not os.path.exists(SAVE_DIR): 
    os.makedirs(SAVE_DIR)
system_log_buffer = deque(maxlen=50)

_id_counter = 1000000

def generate_uuid():
    global _id_counter
    _id_counter += 1
    return f"uid_{_id_counter}"

def write_log(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")
    system_log_buffer.append(f"[{timestamp}] {msg}")

def execute_graph_once():
    start_node = next((n for n in node_registry.values() if n.type_str == "START"), None)
    
    for node in node_registry.values():
        if node.type_str in ["COND_KEY", "MT4_DRIVER", "GO1_DRIVER", "EP_DRIVER", "VIDEO_SRC", "VIS_FISHEYE", "VIS_ARUCO", "VIS_FLASK", "MT4_UNITY", "GO1_UNITY", "UDP_RECV", "LOGGER", "CONSTANT", "MT4_SAG", "MT4_CALIB", "MT4_TOOLTIP", "MT4_BACKLASH", "MT4_KEYBOARD", "GO1_KEYBOARD", "EP_KEYBOARD", "EP_CAM_SRC", "EP_CAM_STREAM"]:
            try: node.execute()
            except Exception as e: print(f"[{node.label}] Error: {e}")

    if not start_node: return

    current_node = start_node
    steps = 0; MAX_STEPS = 100 
    while current_node and steps < MAX_STEPS:
        result = current_node.execute()
        next_out_id = None
        if result is not None:
            if isinstance(result, (int, str)): next_out_id = result
            elif isinstance(result, dict):
                next_out_id = next((k for k, v in result.items() if v == PortType.FLOW), None)
        next_node = None
        if next_out_id:
            target_link = next((l for l in link_registry.values() if l['source'] == next_out_id), None)
            if target_link: next_node = node_registry.get(target_link['dst_node_id'])
        current_node = next_node; steps += 1
