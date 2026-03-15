import dearpygui.dearpygui as dpg
from nodes.robots.mt4 import (
    MT4DriverNode, MT4UnityNode,MT4KeyboardNode, 
    MT4GravitySagNode, MT4CalibrationNode, 
    MT4TooltipNode, MT4BacklashNode
)

class NodeFactory:
    @staticmethod
    def create_node(node_type: str, node_id: str = None):
        """요청받은 type_str에 맞춰 적절한 노드 인스턴스를 생성하여 반환합니다."""
        if node_id is None:
            node_id = str(dpg.generate_uuid())
            
        node = None
        
        # MT4 특화 노드들
        if node_type == "MT4_DRIVER":
            node = MT4DriverNode(node_id)
        elif node_type == "MT4_UNITY":
            node = MT4UnityNode(node_id)
        elif node_type == "MT4_KEYBOARD":
            node = MT4KeyboardNode(node_id)
        elif node_type == "MT4_SAG":
            node = MT4GravitySagNode(node_id)
        elif node_type == "MT4_CALIB":
            node = MT4CalibrationNode(node_id)
        elif node_type == "MT4_TOOLTIP":
            node = MT4TooltipNode(node_id)
        elif node_type == "MT4_BACKLASH":
            node = MT4BacklashNode(node_id)
            
        # (여기에 추후 START, LOGIC_IF, CONSTANT 등의 공통 노드도 추가됩니다)
        
        if node:
            print(f"[Factory] Created Node: {node.label} (ID: {node_id})")
            
        return node