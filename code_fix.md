# 코드 수정 내역

날짜: 2026-03-15
프로젝트: PyGui-Visual-Scripting

## 수정된 문제

1. 연결선이 있는 노드를 삭제하면 크래시(core dump) 발생
- 원인: 그래프 항목 삭제 시 노드/링크 삭제 순서와 엔진 순회가 변경 상황에 충분히 안전하지 않았음.
- 조치:
  - DPG 링크/노드 삭제 전 존재 여부를 확인하도록 방어 코드 추가.
  - 노드 삭제 전에 연결된 링크를 명시적으로 먼저 제거.
  - 엔진 순회를 리스트 스냅샷 기반으로 변경하여 순회 중 변경에 안전하게 처리.
- 파일:
  - `ui/dpg_manager.py`
  - `core/engine.py`

2. 저장한 그래프를 불러오면 연결선이 보이지 않는 문제
- 원인: Serializer가 `node.settings` 대신 `node.state`를 저장하고 있었고, 링크 복원이 콜백 경로에 의존해 누락될 수 있었음.
- 조치:
  - 저장/불러오기 모두 `node.settings`를 일관되게 사용하도록 수정.
  - 로드 시 `dpg.add_node_link(...)`로 링크를 직접 생성하고 `engine.add_link(...)`로 엔진에 즉시 등록.
  - 로드 전 그래프 초기화 시 `pin_label_map`의 이전 상태를 정리.
- 파일:
  - `core/serializer.py`

3. Unity 연결 불가 문제(불필요한 JSON 입력창 의심)
- 원인: Unity 노드의 JSON 입력 핀 기본값이 `""`여서 편집 가능한 텍스트 입력창이 생성됨.
- 조치:
  - Unity JSON 입력 기본값을 `None`으로 변경하여 불필요한 텍스트 입력창 생성 방지.
  - UDP 바인드 실패 시 상태를 바인드 성공으로 표시하던 처리도 함께 수정.
- 파일:
  - `nodes/robots/mt4.py`

4. Keyboard 노드가 없어도 키보드로 로봇이 움직이는 문제
- 원인: 그래프 구성과 무관하게 매 틱 전역 키보드 폴링을 수행함.
- 조치:
  - 메인 루프에서 키 입력 소비 노드(`MT4_KEYBOARD`, `COND_KEY`)가 있을 때만 키보드 폴링 수행.
  - 해당 노드가 없으면 키 상태를 명시적으로 초기화.
- 파일:
  - `main.py`

5. 파일명 입력 등 텍스트 입력 중에도 제어 키가 동작하는 문제
- 원인: 입력 중 키 차단 조건이 모든 활성 편집 상태를 충분히 포괄하지 못함.
- 조치:
  - `dpg.is_any_item_active()` 기반 입력 중 가드 추가.
  - 포커스된 위젯 타입 판별을 모든 `Input*` 위젯으로 확장.
  - `clear_keys()` 헬퍼를 추가하고 입력 중 상태에서 즉시 호출.
- 파일:
  - `core/input_manager.py`

## 참고
- 현재 환경의 정적 분석에서는 외부 패키지(`dearpygui`, `serial`) import 미해결 경고가 보이지만, 위 기능 수정 사항은 코드에 반영 완료됨.

---

## 추가 수정 기록

### [2026-03-15 17:44:16] Unity 연결 실패 및 저장 오류 대응

1. Unity 연결 실패 수정 (`nodes/robots/mt4.py`)
- 문제 분석:
  - `Answer_code.py` 대비 Unity 연동 보조 로직(파일 목록 요청 응답, Unity 대상 IP 갱신, 상태 피드백 송신)이 누락되어 Unity 측 핸드셰이크/상태 연동이 약해진 상태였음.
  - UI에서 바꾼 UDP 설정값(`port`, `ip`)이 노드 런타임 설정으로 동기화되지 않아 실제 실행에는 기본값만 반영되는 구조였음.
- 수정 내용:
  - `MT4_UNITY_IP`, `MT4_FEEDBACK_PORT` 전역값 추가.
  - `send_unity_ui(...)` 함수 복원: Unity UI 포트(5007)로 상태/로그/파일목록 메시지 송신.
  - `get_mt4_paths()` 함수 분리: `REQ_FILES` 명령에 즉시 응답 가능하도록 경로 목록 함수 상단 공용화.
  - `MT4UnityNode.execute()`에 `CMD:REQ_FILES` 처리 추가.
  - `COLLISION`, `LOG_SUCCESS`, `LOG_FAIL` 처리 시 Unity UI 알림 송신 로직 추가.
  - `UDPReceiverNode.execute()`에서 설정값으로 `MT4_UNITY_IP` 갱신.
  - `UDPReceiverNode.execute()`에서 로봇 상태 피드백 JSON을 Unity 피드백 포트(5005)로 주기 송신.

2. 저장 시 `get_item_state` 관련 오류 수정 (`core/serializer.py`)
- 문제 분석:
  - 엔진에는 남아 있지만 DPG에서 이미 삭제된 노드/링크가 존재할 때 `dpg.get_item_pos(...)` 호출 내부에서 `get_item_state` 오류가 발생할 수 있음.
- 수정 내용:
  - 저장 시 `dpg.does_item_exist(nid)`로 노드 유효성 검증 후 위치 조회.
  - 유효하지 않은(stale) 노드는 저장 대상에서 제외하고 `engine.remove_node(...)`로 정리.
  - 링크도 DPG 존재 여부 및 양 끝 노드 유효성을 검사하여 stale 링크 제외/정리.

3. UI 값 동기화 누락 보완 (`ui/dpg_manager.py`, `main.py`)
- 문제 분석:
  - 노드의 입력 위젯/설정 위젯 값이 실제 `node.inputs`, `node.settings`로 주기 반영되지 않아 UDP/IP/Port 및 각종 설정이 동작하지 않는 구조였음.
- 수정 내용:
  - `UIManager.sync_ui_to_nodes()` 메서드 추가.
  - 매 틱 `ui_manager.sync_ui_to_nodes()` 호출 후 엔진 실행하도록 메인 루프 순서 수정.
  - 결과적으로 UDP 설정, 드라이버 설정, 기타 노드 파라미터가 UI 변경값을 즉시 반영하도록 개선.

### [2026-03-15 19:01:45] 링크 복원 누락 및 Core Driver 좌표 되돌림 현상 수정

1. 저장 파일 로드 시 연결선 미표시 재수정 (`core/serializer.py`)
- 문제 분석:
  - 저장 JSON의 노드 ID 타입(문자열/정수) 불일치가 발생하면 `id_map` 조회가 실패하여 링크 복원이 건너뛰어질 수 있었음.
  - 과거 저장 파일은 `src_pin`/`dst_pin` 대신 `src_idx`/`dst_idx`를 사용하는 구 포맷일 수 있어, 현재 로더가 링크 정보를 해석하지 못하는 경우가 있었음.
- 수정 내용:
  - 저장 시 노드 ID와 링크의 `src_node`/`dst_node`를 문자열로 통일 저장.
  - 로드 시에도 노드 ID 비교를 문자열 기준으로 통일.
  - 구 포맷(`src_idx`, `dst_idx`) 링크 복원 fallback 추가:
    - 노드 UI 스키마에서 입력/출력 핀 라벨 순서를 재구성해 인덱스를 라벨로 역매핑.
    - 역매핑 성공 시 현재 방식과 동일하게 `dpg.add_node_link(...)`로 복원.

2. 제어 직후 Core Driver 기본 좌표로 되돌아가는 문제 수정 (`ui/dpg_manager.py`)
- 문제 분석:
  - `sync_ui_to_nodes()`가 링크가 없는 `MT4_DRIVER` 입력 핀에 대해 UI 기본값(X=200, Y=0, Z=120 등)을 매 틱 `node.inputs`에 다시 주입했고,
  - 그 결과 Unity/키보드/수동 제어로 바뀐 목표값이 다음 틱에 기본 좌표로 재설정되는 현상이 발생했음.
- 수정 내용:
  - 입력 핀별로 "연결선 유입 여부"를 먼저 검사하도록 변경.
  - `MT4_DRIVER`의 비연결 입력 핀은 기본적으로 `node.inputs[label] = None`을 유지해 드라이버 내부 fallback(`mt4_target_goal`)을 사용하도록 수정.
  - 사용자가 해당 입력 필드를 직접 편집 중(focus)일 때만 입력값을 반영하도록 예외 처리.
  - 편집 중이 아닐 때는 현재 `mt4_target_goal` 값을 드라이버 입력 UI에 반영해 화면 표시와 런타임 상태를 일치시킴.

### [2026-03-15 19:16:14] 저장 로드 크래시/링크 미표시 및 Unity 방향키 미동작 추가 수정

1. 연결된 그래프를 유지한 상태에서 로드 시 튕김 + 링크 미표시 (`core/serializer.py`, `ui/dpg_manager.py`)
- 문제 분석:
  - 기존 그래프(노드/링크)가 많은 상태에서 항목별 삭제를 반복하면 DPG 내부 상태 변경 타이밍과 콜백(`delink_callback`)이 겹치며 불안정해질 수 있었음.
  - 로드 직후 링크 생성 시점에도 콜백이 개입하면 엔진 링크 목록과 UI 상태가 어긋날 가능성이 있었음.
- 수정 내용:
  - 로드 초기화를 개별 삭제 루프 대신 `node_editor`의 자식 일괄 삭제(`children_only=True`) 방식으로 변경.
  - `UIManager`에 `is_bulk_loading` 플래그를 추가하고, 대량 로드 중 `delink_callback` 동작을 무시하도록 가드.
  - 로드 함수에 `try/finally`로 bulk-loading 상태를 명확히 종료 처리.

2. Unity 방향키 신호 수신(지연값 변화) 대비 로봇 미동작 (`nodes/robots/mt4.py`)
- 문제 분석:
  - 수신 패킷이 JSON이 아닌 레거시 문자열 포맷(`type:...,extra:...`)일 경우 기존 파서(`json.loads`)에서 무시되어 이동 명령이 적용되지 않았을 가능성이 높았음.
  - Unity 측 구현에 따라 방향키가 `MOVE` 좌표가 아닌 `KEY`/`DIRECTION` 형태로 전달될 수 있는데, 기존 코드에서 호환 처리가 부족했음.
- 수정 내용:
  - `parse_unity_packet(...)` 함수를 추가하여 JSON + 키-값 문자열 포맷을 모두 파싱하도록 확장.
  - `MT4UnityNode.execute()`에서 파싱 결과를 기준으로 `CMD`/`MOVE`/`KEY`/`DIRECTION` 유형을 처리하도록 보강.
  - `KEY`/`DIRECTION`의 `UP/DOWN/LEFT/RIGHT/WASD/QE/ROLL` 이벤트를 목표값 증분 제어로 매핑.

3. Manual Control 시 Roll이 Unity에 반영되지 않는 문제 (`nodes/robots/mt4.py`)
- 문제 분석:
  - UDP 피드백 JSON에 `roll` 항목이 빠져 Unity에서 회전 상태를 동기화할 수 없었음.
- 수정 내용:
  - UDP 피드백 페이로드에 `roll` 값을 추가하여 Unity 반영 누락 해소.

### [2026-03-15 19:21:36] Answer_code.py 기준 로드/실행 흐름 추가 정렬

1. 파일 저장/불러오기 버튼 동작을 안전 래퍼로 변경 (`ui/dpg_manager.py`)
- 문제 분석:
  - 기존에는 버튼에서 Serializer를 직접 호출해 로드 시점의 엔진 상태 정리(실행 중지/버튼 라벨 동기화)가 보장되지 않았음.
- 수정 내용:
  - `handle_save_graph()` / `handle_load_graph()` 메서드 추가.
  - LOAD 버튼은 `handle_load_graph()`를 통해 실행되며, `Answer_code.py` 흐름과 유사하게 로드 전 엔진을 `stop()`하고 버튼 라벨을 `RUN SCRIPT`로 강제 동기화.
  - SAVE 후 파일 목록 콤보를 즉시 갱신하도록 보강.

2. 로드 초기화/링크 복원 경로를 Answer_code 방식에 가깝게 재정렬 (`core/serializer.py`)
- 문제 분석:
  - 직전 수정에서 적용했던 `children_only=True` 일괄 삭제 방식은 환경에 따라 오히려 불안정할 수 있고, Answer_code의 검증된 순차 삭제 흐름과 차이가 있었음.
- 수정 내용:
  - 로드 시 기존 링크/노드를 `engine.links`, `engine.nodes` 기반으로 순차 삭제(존재 확인 후 삭제)하도록 변경.
  - 링크 복원 결과를 `restored/skipped` 카운트로 출력해, 누락 시 원인 추적이 가능하도록 진단 로그 추가.
  - `is_bulk_loading` 가드는 유지해 delink 콜백 간섭을 방지.

### [2026-03-15 19:40:10] 노드 로드 시 크래시 및 키보드 제어 잠김 현상 수정

1. 저장된 그래프 로드 시 프로그램 튕김(Core Dump) 및 선 누락 현상 수정 (`core/serializer.py`)
- 문제 분석:
  - DPG 엔진 특성상 노드를 지우기 전에 연결된 선(Link)을 먼저 지워야 하나, 로드(`LOAD`) 과정에서 기존 그래프를 초기화할 때 삭제 순서가 꼬여 이미 삭제된 노드의 핀을 참조하려다 충돌(Segfault)이 발생함.
  - 과거에 저장된 JSON 파일(`mt4_keyboard.json`)을 확인한 결과, 핀 데이터가 고정된 문자열 라벨이 아닌 세션마다 무작위로 발급되는 랜덤 정수 ID로 저장되어 있어 로드 시 핀 매칭에 실패하고 선이 누락됨.
- 수정 내용:
  - `load_graph` 내부의 기존 그래프 초기화 로직을 수정하여 모든 선(Link)을 먼저 완벽히 삭제한 후 노드를 삭제하도록 순서를 엄격히 강제함.
  - 일시적인 랜덤 정수 ID가 아닌, 고정된 문자열 기반의 아키텍처로 완전히 전환함(구버전 랜덤 ID 기반 JSON 파일은 호환되지 않으므로 새로 연결 후 저장 필요).

2. 키보드 제어 시 수치만 오르고 로봇이 움직이지 않는 현상 수정 (`nodes/robots/mt4.py`)
- 문제 분석:
  - `MT4KeyboardNode` 실행 시 입력이 감지되면 `mt4_manual_override_until = time.time() + 0.5`가 호출되어 전역 시스템에 "수동 조작 잠금(Override)"을 걸고 있었음.
  - 이로 인해 하드웨어를 제어하는 `MT4_DRIVER` 노드가 "수동 조작 중이니 외부 노드의 입력 핀 데이터는 무시하라"고 판단하여, 정작 키보드 노드가 핀으로 보낸 목표 좌표 데이터까지 무시해버리는 논리적 모순이 발생함.
- 수정 내용:
  - `MT4KeyboardNode` 내부의 전역 수동 제어 잠금(`mt4_manual_override_until`) 트리거 코드를 완전히 제거.
  - 키보드 입력 발생 시 `mt4_target_goal`을 기준으로 좌표를 계산하되, 하드웨어 우선권을 뺏지 않고 오직 출력 핀(Output Data)으로만 순수하게 타겟 데이터를 전달하도록 역할(SRP)을 분리 및 수정함.

### [2026-03-15 20:03:48] Answer_code.py 기준 100% 동작 정렬(인코딩 복구판)

1. 하이브리드 실행 엔진 복구 (`core/engine.py`)
- 수정 내용:
  - 기존의 완전 데이터 기반 `is_ready()` 일괄 실행 대신, `Answer_code.py`와 동일하게
    - 독립 동작 노드(Driver/Unity/UDP/Keyboard 등) 우선 실행,
    - 이후 StartNode 기반 Flow 체인 실행(최대 step 제한)
    구조로 `tick()` 로직을 재구성.

2. MT4 Driver 입력 변경 감지 복구 (`nodes/robots/mt4.py`)
- 수정 내용:
  - `inputs_changed` 조건을 도입해 입력이 실제로 변했을 때만 목표값 갱신.
  - 수동/자동 제어 경계에서 목표 좌표가 기본값으로 되돌아가던 문제를 완화.

3. Unity 처리 로직 정렬 (`nodes/robots/mt4.py`)
- 수정 내용:
  - Unity 메시지 처리 경로를 `Answer_code.py` 방식에 맞춰 정렬.
  - MOVE/CMD 처리 우선순위 및 출력 핀 갱신 흐름을 원본 구조와 유사하게 맞춤.

4. Keyboard/Action 노드 수동 오버라이드 정렬 (`nodes/robots/mt4.py`)
- 수정 내용:
  - 키보드 및 액션 이벤트 시 `mt4_manual_override_until` 갱신 타이밍을 원본 흐름과 맞춤.
  - 조작 이후 타겟값 유지 및 전달 안정성 개선.

5. Driver UI 동기화 정렬 (`ui/dpg_manager.py`)
- 수정 내용:
  - 링크가 없는 Driver 입력 핀은 현재 `mt4_target_goal` 값을 UI에 반영하도록 정렬.
  - 사용자 편집 중인 필드는 즉시 입력값을 반영하고, 비편집 시 런타임 상태와 UI를 동기화.

### [2026-03-15 20:24:00] Serializer 들여쓰기(Indentation) 오류 수정

1. 코드 갱신 중 발생한 구문 오류 해결 (`core/serializer.py`)
- 문제 분석:
  - 이전 수정 코드를 파일에 적용(복사/붙여넣기)하는 과정에서 `load_graph` 등 메서드 선언부(`@classmethod`)의 들여쓰기가 클래스 내부의 다른 규격과 미세하게 어긋나면서 파이썬 인터프리터에서 `IndentationError`를 발생시킴.
- 수정 내용:
  - `GraphSerializer` 클래스 내 모든 메서드(`save_graph`, `load_graph`, `get_save_files` 등)의 들여쓰기(공백 4칸)를 완벽하게 정렬한 전체 코드본으로 파일을 덮어씌워 구문 에러를 해결함.

### [2026-03-15 20:30:15] 노드 삭제 크래시 및 파이프라인 데이터 흐름 단절 수정

1. 연결된 노드 삭제 시 프로그램 튕김(Core Dump) 현상 수정 (`ui/dpg_manager.py`)
- 문제 분석:
  - DPG UI에서 선택된 노드 ID는 정수(`int`)형으로 반환되나, 엔진 내부의 링크 레지스트리에서는 문자열(`str`)형으로 관리되고 있었음.
  - 타입 불일치(`"123" != 123`)로 인해, 노드 삭제 전 연결된 선(Link)을 찾아 먼저 지우는 방어 로직이 작동하지 않았음. 결국 선이 남은 상태로 노드가 강제 삭제되며 DPG 내부 메모리 충돌(Segfault)이 발생함.
- 수정 내용:
  - `delete_selection` 콜백 내에서 DPG가 반환한 노드 ID를 명시적으로 문자열 변환(`str(nid)`)하여 엔진의 링크 데이터와 완벽하게 매칭되도록 수정.

2. Unity 및 키보드 제어 시 로봇 미동작 현상 수정 (`nodes/robots/mt4.py`)
- 문제 분석:
  - 리팩토링된 엔진 아키텍처는 모든 입력 핀(Flow In 포함)에 데이터가 들어와야 다음 노드가 실행(Ready)되는 구조임.
  - 하지만 핵심 제어 노드들(`MT4DriverNode`, `MT4UnityNode`, `MT4KeyboardNode`, `UDPReceiverNode`)이 연산을 마친 후 `Flow Out` 핀으로 출력 신호(`True`)를 내보내지 않아, 하위 연결 노드들이 영구적인 대기 상태에 빠지는 '파이프라인 굶주림(Starvation)' 현상이 발생함.
  - 이로 인해 하드웨어 통신을 담당하는 드라이버 노드가 단 한 번도 실행되지 못해 로봇이 멈춰있었음.
- 수정 내용:
  - 해당 4개 노드의 `__init__` 초기화 단계 및 `execute()` 메서드 반환 직전에 `self.outputs["Flow Out"] = True` 코드를 추가하여, 파이프라인의 제어 흐름이 막힘없이 연속적으로 순환되도록 개통함.

### [2026-03-15 21:10:00] 노드 삭제 크래시 영구 해결 및 Unity/키보드 데이터 파이프라인 정상화

1. 엔진 및 UI 간 ID 타입 불일치로 인한 삭제 크래시 해결 (`core/engine.py`, `ui/dpg_manager.py`)
- 문제 분석:
  - DPG UI 프레임워크는 클릭/선택된 노드 및 링크 ID를 정수(`int`)로 반환하는 반면, 엔진 내부 레지스트리와 저장 파일(JSON)은 문자열(`str`)을 기준으로 작동함.
  - 이로 인해 `delete_selection` 콜백에서 연결된 선을 찾을 때 타입 불일치(`123 != "123"`)가 발생하여 선을 하나도 찾지 못함. 결과적으로 선이 그대로 남은 상태에서 노드만 강제로 삭제되면서 DPG 내부 메모리 참조 오류(Core Dump)가 발생함.
- 수정 내용:
  - `core/engine.py`의 데이터 관리 로직(`add_node`, `remove_node`, `_transfer_data` 등) 전반에 걸쳐 명시적인 `str()` 형변환 방어 코드를 추가하여 타입 안정성을 강제함.
  - `ui/dpg_manager.py`의 `delete_selection` 콜백에서도 탐색 전 DPG ID를 `str(nid)`로 캐스팅하여 엔진의 링크 데이터와 완벽하게 매칭되도록 수정, 튕김 현상을 영구적으로 차단함.

2. Unity 레거시 패킷 호환성 추가 및 키보드 노드 전역 변수 교착 상태 해결 (`nodes/robots/mt4.py`)
- 문제 분석:
  - 유니티에서 전송하는 방향키 제어 데이터가 순수 JSON이 아닌 특수 문자열(`type:KEY,val:UP` 등) 포맷이어서 기존 `json.loads` 파서가 에러를 뱉고 명령을 무시함.
  - `MT4KeyboardNode`가 데이터 파이프라인(핀 출력)을 거치지 않고 직접 전역 목표 좌표(`mt4_target_goal`)를 덮어씌움. 이로 인해 최종 제어를 담당하는 `MT4DriverNode`가 "입력 핀 데이터와 현재 전역 목표값이 동일하므로 움직일 필요가 없다"고 오판하여 통신을 건너뜀.
- 수정 내용:
  - `parse_unity_packet` 헬퍼 함수를 추가하여 JSON 규격과 쉼표 기반 레거시 문자열 포맷을 모두 파싱할 수 있도록 하위 호환성을 확보함. 유니티의 방향키 이벤트(`KEY`/`DIRECTION`)를 좌표 증분 로직에 매핑.
  - `MT4KeyboardNode`가 더 이상 전역 변수를 직접 훼손하지 않고, 순수하게 자신의 연산 결과를 출력 핀(`Target X` 등)으로만 내보내도록 데이터 흐름의 단방향 원칙을 엄격하게 복구함.
---

## [2026-03-16 17:00:00 (KST 기준 현재 시간 반영)] mt4.py - MT4UnityNode 로봇 미작동 버그 수정
- **수정 사유:** Unity에서 수신된 데이터를 바탕으로 로봇 위치값을 갱신할 때 MT4UnityNode의 execute() 메서드가 잘못된 변수(self.output_data, self.out_x 등)를 참조하여 예외(AttributeError)를 발생시키고 있었음. 또한 예외가 	ry-except: pass로 무시되어 디버깅을 어렵게 했음. 추가적으로 엔진 파이프라인의 다음 노드를 실행시키기 위한 Flow Out 반환값이 문자열이 아닌 딕셔너리로 설정되어 파이프라인 흐름이 끊기는 문제가 있었음.
- **수정 사항:**
  1. self.output_data[self.out_x] 등의 잘못된 프로퍼티 참조를 프레임워크 규격에 맞게 self.outputs["Target X"] 형식의 딕셔너리 키-값 할당으로 변경함.
  2. Override 모드와 일반 MOVE 수신 모드 모두 동일하게 self.outputs를 갱신하도록 수정함.
  3. 메서드 마지막의 
eturn self.outputs 구문을 다음 노드 흐름을 실행하기 위해 
eturn "Flow Out"으로 수정하고, self.outputs["Flow Out"] = True로 파이프라인 개통 상태를 유지하도록 변경함.

---

## [2026-03-16 17:15:00 (KST 기준 현재 시간 반영)] mt4.py - 로봇 미작동 및 시리얼 연결 버그 수정
- **수정 사유:** MT4UnityNode 데이터 수신부가 수정되었음에도 불구하고, 로봇 하드웨어에 G-Code 명령이 실질적으로 전달되지 않는 명령어 캐싱 로직 오류 (사일런트 통신 단절)가 발견됨. mt4.py의 MT4DriverNode에서 로봇으로 명령을 보낼 때, 실제 시리얼 포트에 데이터를 보내지도 못했으면서 self.last_cmd = cmd를 맵핑해버리는 위치 오류가 존재함. 이로 인해 최초 통신 지연이나 일시적 포트 단절 후 복구되어도 새로운 명령을 받아들이지 못함.
- **수정 사항:** MT4DriverNode.execute() 내의 시리얼 송신부에서 self.last_cmd = cmd 할당 라인을 실제 시리얼 송신(ser.write())이 성공하는 	ry 블록 내부로 이동시켜, 통신이 정상일 때만 이전 명령어 상태를 갱신하도록 바로잡음.

---

## [2026-03-16 17:30:00 (KST 기준 현재 시간 반영)] mt4.py - DearPyGui UI 종속성 완벽 제거 (의존성 분리)
- **수정 사유:** 비즈니스/하드웨어 제어 로직을 담당하는 핵심 파일인 mt4.py 내부에 UI 라이브러리(dearpygui) 객체와 메서드(# dpg.get_value 등)가 강하게 결합되어 있어, 추후 프로젝트가 백엔드/프론트엔드 분리 혹은 다른 GUI 프레임워크로 넘어갈 시 치명적인 의존성(Dependency) 문제가 발생할 위험성이 높았음.
- **수정 사항:**
  1. mt4.py 내부에서 UI를 조작하거나 값을 가져오는 모든 콜백 함수(mt4_manual_control_callback, mt4_move_to_coord_callback, 	oggle_mt4_record, play_mt4_path 등)를 파라미터를 명시적으로 받는 순수 논리 함수로 리팩토링함.
  2. mt4.py 상단의 import dearpygui.dearpygui as dpg 구문 및 관련 코드를 일괄 제거하여 완전한 UI 독립성(Agnostic)을 확보함.
  3. ui/dpg_manager.py에 UI의 상태값을 읽고 해당 순수 논리 함수들에 값을 넘겨주는 "UI 전용 중간 콜백 래퍼(_ui_...)" 함수들을 작성하여, UI 조작은 오직 UI 레이어 안에서만 수행되도록 책임을 분리함.
  4. 상태 변화(예: 녹화 시작/정지 버튼 텍스트 변경)에 따른 UI 동기화는 렌더링 측루프 코드인 main.py의 UI 틱 부분에서 모델 상태(mt4_mode)를 폴링(Polling)하여 UI를 안전하게 변경하도록 개선함.
