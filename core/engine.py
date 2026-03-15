from enum import IntEnum
from typing import Dict, Any, List

# 💡 교수님 조언 적용: "Offline" 같은 문자열 비교 대신 Enum과 정수형 상태 사용
class EngineState(IntEnum):
    OFFLINE = 0
    IDLE = 1
    RUNNING = 2

class ExecutionEngine:
    def __init__(self):
        self.state = EngineState.IDLE
        
        # 엔진이 관리할 노드와 연결선(Link) 레지스트리
        self.nodes: Dict[str, Any] = {}
        self.links: List[Dict[str, str]] = []
        
        print("[Engine] Data-Driven Execution Engine Initialized.")

    def add_node(self, node: Any):
        """엔진에 노드 객체를 등록합니다."""
        self.nodes[node.node_id] = node

    def add_link(self, src_id: str, src_pin: str, dst_id: str, dst_pin: str):
        """노드 간의 데이터/흐름 연결선을 등록합니다."""
        self.links.append({
            "src_id": src_id,
            "src_pin": src_pin,
            "dst_id": dst_id,
            "dst_pin": dst_pin
        })

    def tick(self):
        """
        main.py의 메인 루프에서 주기적으로 호출되는 핵심 로직.
        상태 및 데이터 기반으로 노드를 평가하고 비동기적으로 실행합니다.
        """
        if self.state != EngineState.RUNNING:
            return

        # 1. 데이터 전달 (Data Pipeline)
        # 엔진이 모든 선(Link)을 타고 이전 노드의 출력값을 다음 노드의 입력값으로 밀어넣습니다.
        self._transfer_data()

        # 2. 노드 실행 조건 평가 및 실행
        for node_id, node in self.nodes.items():
            # "내 입력 핀에 필요한 데이터가 모두 들어왔는가?"를 확인
            if node.is_ready():
                # 데이터가 모두 모였으면 순수 파이썬 로직 덩어리인 execute() 실행!
                flow_out = node.execute()
                
                # (선택) 실행이 끝난 후, 소모된 일회성 제어 신호(Trigger)는 초기화할 수 있습니다.
                # 데이터(좌표값 등)는 유지하고, 제어 흐름만 끊어주는 등의 처리를 여기서 고도화합니다.
                
    def _transfer_data(self):
        """
        등록된 모든 링크를 순회하며 데이터를 복사하여 전달합니다.
        이 로직 덕분에 노드들은 서로의 존재를 모른 채 자신의 입력 포트만 바라보고 연산할 수 있습니다.
        """
        for link in self.links:
            src_node = self.nodes.get(link["src_id"])
            dst_node = self.nodes.get(link["dst_id"])

            if src_node and dst_node:
                # 소스 노드의 출력 데이터를 타겟 노드의 입력 데이터로 복사
                if link["src_pin"] in src_node.outputs:
                    data_val = src_node.outputs[link["src_pin"]]
                    dst_node.inputs[link["dst_pin"]] = data_val

    def start(self):
        """엔진 실행 시작"""
        self.state = EngineState.RUNNING
        print("[Engine] State Changed -> RUNNING")

    def stop(self):
        """엔진 일시 정지"""
        self.state = EngineState.IDLE
        print("[Engine] State Changed -> IDLE")

    def shutdown(self):
        """엔진 완전 종료 및 자원 해제"""
        self.state = EngineState.OFFLINE
        self.nodes.clear()
        self.links.clear()
        print("[Engine] Engine Shutdown Complete.")