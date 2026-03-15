from abc import ABC, abstractmethod
from typing import Dict, Any, List, Tuple

class BaseNode(ABC):
    def __init__(self, node_id: str, label: str, type_str: str):
        self.node_id = node_id
        self.label = label
        self.type_str = type_str
        
        # 엔진이 꽂아줄 입력 데이터와, 노드가 계산을 마치고 내보낼 출력 데이터
        self.inputs: Dict[str, Any] = {}
        self.outputs: Dict[str, Any] = {}
        
        # 노드의 파라미터(설정값) 상태 저장용
        self.settings: Dict[str, Any] = {}

    # ==========================================
    # 🎨 UI 스키마 정의 (DPG 코드가 전혀 없는 순수 데이터)
    # UIManager가 이 리스트를 읽어와서 화면을 대신 그려줍니다.
    # ==========================================
    @abstractmethod
    def get_ui_schema(self) -> List[Tuple[str, str, Any]]:
        """
        노드의 핀(Pin)과 기본 입력창을 구성하기 위한 List 스키마를 반환합니다.
        형식: [("핀 종류(IN_FLOW/OUT_FLOW/IN_DATA/OUT_DATA)", "라벨", 기본값)]
        예시: 
        [
            ("IN_FLOW", "Flow In", None),
            ("IN_DATA", "X", 200.0),
            ("IN_DATA", "Y", 0.0),
            ("OUT_FLOW", "Flow Out", None)
        ]
        """
        pass

    @abstractmethod
    def get_settings_schema(self) -> List[Tuple[str, Any]]:
        """
        노드의 설정창(파라미터) 구성을 위한 List 스키마를 반환합니다.
        형식: [("파라미터명", 기본값)]
        예시: [("Speed", 2.0), ("Smooth", 1.0)]
        """
        pass

    # ==========================================
    # ⚙️ 코어 로직 (순수 파이썬 연산)
    # ==========================================
    @abstractmethod
    def execute(self) -> str:
        """
        self.inputs에 들어온 데이터와 self.settings를 바탕으로 연산을 수행하고,
        결과를 self.outputs에 저장합니다.
        
        반환값: 다음에 실행할 Flow 핀의 라벨 이름 (흐름이 끝났다면 None 반환)
        """
        pass

    def is_ready(self) -> bool:
        """
        [데이터 파이프라인 엔진용] 
        내 입력 핀에 필요한 데이터가 모두 들어왔는지 확인합니다.
        기본적으로 입력값이 하나라도 None이면 실행 준비가 안 된 것으로 판단합니다.
        (필요에 따라 하위 노드에서 오버라이딩 가능)
        """
        for value in self.inputs.values():
            if value is None:
                return False
        return True