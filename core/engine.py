from enum import IntEnum
from typing import Dict, Any, List

class EngineState(IntEnum):
    OFFLINE = 0
    IDLE = 1
    RUNNING = 2

class ExecutionEngine:
    def __init__(self):
        self.state = EngineState.IDLE
        
        self.nodes: Dict[str, Any] = {}
        self.links: List[Dict[str, str]] = []
        
        print("[Engine] Data-Driven Execution Engine Initialized.")

    def add_node(self, node: Any):
        self.nodes[node.node_id] = node

    def remove_node(self, node_id: str):
        """엔진에서 노드와 관련된 모든 연결 데이터를 삭제합니다."""
        if node_id in self.nodes:
            del self.nodes[node_id]
            # 해당 노드에 연결되어 있던 모든 선(Link) 정보도 청소
            self.links = [link for link in self.links if link["src_id"] != node_id and link["dst_id"] != node_id]

    def add_link(self, link_id: str, src_id: str, src_pin: str, dst_id: str, dst_pin: str):
        """UI에서 넘어온 링크 ID를 포함하여 연결 데이터를 등록합니다."""
        self.links.append({
            "id": link_id,
            "src_id": src_id,
            "src_pin": src_pin,
            "dst_id": dst_id,
            "dst_pin": dst_pin
        })

    def remove_link(self, link_id: str):
        """지정된 ID의 연결선 데이터를 파이프라인에서 삭제합니다."""
        self.links = [link for link in self.links if link.get("id") != link_id]

    def tick(self):
        if self.state != EngineState.RUNNING:
            return

        # 1. 데이터 전달 (Data Pipeline)
        self._transfer_data()

        # 2. 노드 실행 조건 평가 및 실행
        for node_id, node in list(self.nodes.items()):
            if node_id not in self.nodes:
                continue
            if node.is_ready():
                node.execute()
                
    def _transfer_data(self):
        for link in list(self.links):
            src_node = self.nodes.get(link["src_id"])
            dst_node = self.nodes.get(link["dst_id"])

            if src_node and dst_node:
                if link["src_pin"] in src_node.outputs:
                    data_val = src_node.outputs[link["src_pin"]]
                    dst_node.inputs[link["dst_pin"]] = data_val

    def start(self):
        self.state = EngineState.RUNNING
        print("[Engine] State Changed -> RUNNING")

    def stop(self):
        self.state = EngineState.IDLE
        print("[Engine] State Changed -> IDLE")

    def shutdown(self):
        self.state = EngineState.OFFLINE
        self.nodes.clear()
        self.links.clear()
        print("[Engine] Engine Shutdown Complete.")