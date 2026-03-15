import dearpygui.dearpygui as dpg
# 공용 노드 & MT4 노드 모두 수입
from nodes.common import StartNode, ConstantNode, ConditionKeyNode, LogicIfNode, LogicLoopNode, PrintNode, LoggerNode
from nodes.robots.mt4 import (
    MT4DriverNode, MT4UnityNode, MT4GravitySagNode, MT4CalibrationNode, 
    MT4TooltipNode, MT4BacklashNode, MT4KeyboardNode, MT4CommandActionNode, UDPReceiverNode
)

class NodeFactory:
    @staticmethod
    def create_node(node_type: str, node_id: str = None):
        if node_id is None: node_id = str(dpg.generate_uuid())
        node = None
        
        # 공용 노드
        if node_type == "START": node = StartNode(node_id)
        elif node_type == "CONSTANT": node = ConstantNode(node_id)
        elif node_type == "COND_KEY": node = ConditionKeyNode(node_id)
        elif node_type == "LOGIC_IF": node = LogicIfNode(node_id)
        elif node_type == "LOGIC_LOOP": node = LogicLoopNode(node_id)
        elif node_type == "PRINT": node = PrintNode(node_id)
        elif node_type == "LOGGER": node = LoggerNode(node_id)
        
        # MT4 전용 노드
        elif node_type == "MT4_DRIVER": node = MT4DriverNode(node_id)
        elif node_type == "MT4_ACTION": node = MT4CommandActionNode(node_id)
        elif node_type == "MT4_KEYBOARD": node = MT4KeyboardNode(node_id)
        elif node_type == "MT4_UNITY": node = MT4UnityNode(node_id)
        elif node_type == "UDP_RECV": node = UDPReceiverNode(node_id)
        elif node_type == "MT4_SAG": node = MT4GravitySagNode(node_id)
        elif node_type == "MT4_CALIB": node = MT4CalibrationNode(node_id)
        elif node_type == "MT4_TOOLTIP": node = MT4TooltipNode(node_id)
        elif node_type == "MT4_BACKLASH": node = MT4BacklashNode(node_id)
            
        if node: print(f"[Factory] Created Node: {node.label}")
        return node