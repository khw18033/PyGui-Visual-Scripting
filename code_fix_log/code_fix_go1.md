### [2026-03-17 20:42:00] Go1 로봇 및 비전(Vision) 시스템 모듈화 이식 (SRP 및 비동기 적용)
- 문제 분석:
  - 기존 통합 코드(`visual_scripting_Int_v11.py`)에 하드코딩되어 있던 Go1 제어 로직과 카메라(OpenCV) 기능들을 새로운 모듈형 아키텍처로 이식할 필요가 있었음.
  - 특히 기존의 카메리 노드는 영상 획득, 왜곡 보정, 마커 탐지, 브로드캐스팅 등을 한 번에 처리하여 단일 책임 원칙(SRP)에 위배되었음.
  - 또한 OpenCV의 프레임 읽기(`read()`)나 Flask 서버 구동이 메인 스레드에서 동기적(Blocking)으로 실행될 경우, 네트워크 지연 발생 시 전체 GUI가 멈춰버리는(Freeze) 치명적인 위험이 존재함.
- 조치 방안:
  - 단일 책임 원칙(SRP)을 적용하여 비전 파이프라인을 `VideoSourceNode`, `FisheyeUndistortNode`, `ArUcoDetectNode`, `FlaskStreamNode`로 잘게 쪼개어 독립적인 데이터 흐름(Data Flow) 노드로 재설계함.
  - 영상 스트림 획득 및 Flask HTTP 서버를 백그라운드 데몬 스레드(Thread)로 분리하여 메인 GUI 루프의 실시간성을 완벽히 보장함.
  - `nodes/robots/go1.py`를 신설하여 Go1 전용 네트워크 제어 노드(`Go1RobotDriver`, `Go1ActionNode`)를 순수 로직으로 구현함.
  - `ui/dpg_manager.py`에 기존 MT4 로직과 파일 매니저 시스템을 100% 보존한 상태에서, Go1 전용 대시보드 탭과 노드 생성 버튼을 성공적으로 증축함.
- 수정 및 추가 파일:
  - `nodes/robots/go1.py` (신규)
  - `ui/dpg_manager.py` (수정: Go1 대시보드 및 비전 UI 렌더러 추가)
  - `core/factory.py` (수정: 신규 노드 생성기 등록)
  - `main.py` (수정: Go1 통신용 백그라운드 스레드 시작 로직 추가)

### [2026-03-18 11:14:13] Go1 로봇 연결 및 작동 테스트 가이드
- **네트워크 연결 확인**:
  - PC와 Go1 로봇을 동일한 네트워크에 연결합니다 (기본 고정 IP: `192.168.12.1`, UDP 포트: `8080`).
  - 프로그램 우측 하드웨어(HW) 상태 패널에서 Go1의 통신 연결 상태가 `ONLINE`인지 확인합니다.
- **기본 제어 테스트 (Go1 Action)**:
  - 바탕화면 우클릭 노드 메뉴에서 `Go1 Action` 노드를 생성합니다.
  - 속성 탭에서 로봇의 동작(`Stand Up`, `Lie Down`, `Walk Mode`, `Dance`)을 선택합니다.
  - Flow 핀을 연결하고 노드를 실행하여 실제 로봇이 해당 동작을 수행하는지 확인합니다.
- **주행 이동 테스트 (Go1 Driver UI)**:
  - Go1 대시보드 UI에 노출되는 제어 슬라이더(`Vx(전후)`, `Vy(좌우)`, `Yaw(회전)`)를 마우스나 컨트롤러로 조작합니다.
  - `Speed` 설정값에 따라 로봇의 이동 속도가 적절하게 제어되는지 확인합니다. (비정상 작동 시 즉시 슬라이더를 0으로 맞추거나 멈춤 동작을 수행하세요)
- **비전 파이프라인 (카메라 스트리밍) 구성 빛 검증**:
  1. `Video Source` 노드를 배치하고 기본 RTSP 주소(`rtsp://192.168.12.1:8554/live`)가 맞는지 확인한 후 스트리밍을 활성화(is_running) 합니다.
  2. 왜곡 보정을 위해 `Video Source` 노드의 Data 출력 핀을 `Fisheye Undistort` 노드의 입력 핀에 연결합니다.
  3. 마커 탐지를 위해 `ArUco Detect` 노드를 연결하여 영상 내 인식 테스트를 진행합니다.
  4. 최종 결과를 외부로 모니터링하려면 파이프라인 마지막에 `UDP/HTTP Broadcast` 노드를 연결하고 웹 브라우저(`http://localhost:5000/video_feed`)에서 실시간 출력 영상을 확인합니다.

### [2026-03-18 11:14:13] Go1 로봇 및 비전 시스템 전용 노드 상세 설명
- **Go1 Action 노드 (`GO1_ACTION`)**:
  - **입출력**: Flow 입력(Trigger), Flow 출력(Next)
  - **기능**: 지정된 동작명(Stand Up, Lie Down, Walk Mode, Dance)을 문자열 형태의 UDP 커맨드로 Go1에게 직접 전송하는 흐름 제어용 노드입니다.
- **Go1 Driver 노드 (`Go1RobotDriver`)**:
  - **기능**: 주행을 위한 노드로 Vx(전/후), Vy(좌/우), Yaw(회전) 속도값을 실시간으로 Go1에 전송합니다. 초당 20번(20Hz) 한계 스로틀링을 두어 네트워크 과부하를 막는 로직이 탑재되어 있습니다.
- **Video Source 노드 (RTSP/Webcam) (`VIDEO_SRC`)**:
  - **입출력**: Data 출력(Frame 영상)
  - **기능**: 지정된 RTSP 주소나 웹캠 URL로 붙어서 실시간으로 프레임을 가져옵니다. 통신 지연으로 인해 프로그램이 멈추는 것을 방지하기 위해 별도의 백그라운드 스레드로 동작합니다.
- **Fisheye Undistort 노드 (`VIS_FISHEYE`)**:
  - **입출력**: Data 입력(원본 Frame), Data 출력(보정된 Frame)
  - **기능**: 로봇의 어안렌즈 왜곡을 평면화하는 노드로, `Calib_data` 폴더의 보정 파일(`K1.npy`, `D1.npy`)을 읽어들여 이미지 내의 렌즈 왜곡을 실시간으로 펴서 출력합니다.
- **ArUco Detect 노드 (V4/V5) (`VIS_ARUCO`)**:
  - **입출력**: Data 입력(프레임), Data 출력(마커 마스킹 프레임), Data 출력(마커 검출좌표 JSON 정보)
  - **기능**: 넘겨받은 이미지 상에 있는 ArUco 마커를 스캔합니다. 테두리 및 마커 아이디를 사진 위에 그려주며, 감지된 식별 고유 ID값과 이미지 상(X, Y) 픽셀 중심점 데이터값을 배열로 분리해 추출해줍니다.
- **UDP/HTTP Broadcast 노드 (`VIS_FLASK`)**:
  - **입출력**: Data 입력(최종 프로세싱 플로우를 거친 Frame)
  - **기능**: 최종 프레임을 Flask 프레임워크를 이용해 웹 영상 스트림으로 쏘아줍니다. 내부적으로 데몬 스레드가 백그라운드에서 동작하여 타 기기나 브라우저 화면(`http://localhost:5000/video_feed`)에서도 확인할 수 있게 합니다.

### [2026-03-31 00:00:00] Go1_DS.py 로직 기반 모듈 재이식 (현재 아키텍처 정합)
- 문제 분석:
  - `nodes/robots/go1.py`가 비어 있어 `NodeFactory`/`UI Renderer`가 기대하는 Go1 노드 클래스 및 전역 심볼을 제공하지 못했고, 실행 시 Import/런타임 오류 위험이 있었음.
  - 기존 `Go1_DS.py`의 핵심 동작(주행 인텐트 유지, Unity 텔레옵 수신, 비전 파이프라인 분리, Flask 스트리밍)을 현재 모듈 구조(`BaseNode` + `NodeFactory` + `engine`)로 맞춰 재구성할 필요가 있었음.
  - 엔진의 flowless 실행 목록에 `VIS_FISHEYE`, `VIS_ARUCO`가 누락되어 비전 중간 노드가 자동 실행되지 않는 문제가 있었음.
- 조치 방안:
  - `nodes/robots/go1.py`를 신규 구현하여 다음 요소를 통합함:
    - 전역 상태/심볼: `go1_sock`, `GO1_IP`, `GO1_PORT`, `go1_target_vel`, `go1_dashboard`, `go1_unity_data`, `get_go1_rtsp_url()`
    - 백그라운드 통신: `go1_keepalive_thread()` (20Hz 송신, Unity UDP 수신/타임아웃, 상태 브로드캐스트)
    - 제어 노드: `Go1RobotDriver`, `Go1ActionNode`, `Go1KeyboardNode`, `Go1UnityNode`
    - 비전 노드: `VideoSourceNode`, `FisheyeUndistortNode`, `ArUcoDetectNode`, `FlaskStreamNode`
  - `core/engine.py`의 주기 실행 대상에 `VIS_FISHEYE`, `VIS_ARUCO`를 추가하여 비전 파이프라인이 실제로 프레임 데이터를 연쇄 처리하도록 수정함.
- 수정 파일:
  - `nodes/robots/go1.py` (신규 구현)
  - `core/engine.py` (flowless 실행 목록 보완)

### [2026-03-31 00:00:01] Go1_DS.py 수준 완전 이식 보강 (대시보드/연결/UI 동작 동등화)
- 문제 분석:
  - 1차 이식 이후 Go1 기능은 동작했지만, `Go1_DS.py` 대비 다음이 부족했음:
    - Unity IP/Teleop 사용/ArUco 송신 토글 UI 및 상태 반영
    - 대시보드의 Odometry/Reason/Latency/Battery/ArUco/Network 표시
    - 키보드 원샷 제어(Stop, Yaw Align, Yaw Reset) 반영
    - 시작 시 Go1 대상 IP 확인 기반 연결 절차
- 조치 방안:
  - `nodes/robots/go1.py`를 DS 제어 흐름 기준으로 재정비:
    - `go1_node_intent`, `go1_state`, `go1_unity_data`, `aruco_settings`, `camera_state` 추가
    - `go1_keepalive_thread()`를 Unity timeout/우선순위 제어/이유(reason)/지연(ms) 계산 로직으로 확장
    - `init_go1_connection()` + 콘솔 IP 확인 절차 추가(비대화형 환경은 기본값 자동 사용)
    - `go1_estop_callback()` 및 Go1 Action/Keyboard/Unity 노드를 DS 동작 의미와 동일하게 조정
    - ArUco 감지 데이터 UDP(JSON, port 5008) 송신 연동
  - `ui/dpg_manager.py` Go1 전용 UI를 DS 형태로 확장:
    - Go1 Dashboard 패널 확장(상태, 오도메트리, 명령, 네트워크, 배터리, ArUco, E-Stop)
    - Go1 노드 렌더러 및 상태동기화 확장(`GO1_ACTION`, `GO1_UNITY`, `GO1_KEYBOARD`)
    - Go1 실행 중지 시 intent/velocity 안전 초기화
  - `main.py`에 Go1 초기 연결 절차 호출 추가 후 백그라운드 스레드 시작
- 수정 파일:
  - `nodes/robots/go1.py` (재구성)
  - `ui/dpg_manager.py` (Go1 UI/동기화 보강)
  - `main.py` (Go1 연결 초기화 단계 추가)

### [2026-03-31 00:00:02] 접속 불가 원인 대응 (SDK 경로/초기 연결 절차 정합)
- 문제 분석:
  - `Go1_DS.py`와 달리 SDK 경로 기준점이 `nodes/robots`로 계산되어, `robot_interface` 탐색 경로가 달라질 수 있었음.
  - Go1 IP 입력 절차가 터미널 상태에 따라 생략되어 실제 장비 IP 대신 기본값으로 고정될 수 있었음.
- 조치 방안:
  - Unitree SDK 경로를 프로젝트 루트 기준(`.../unitree_legged_sdk/lib/python/<arch>`)으로 수정.
  - Go1 IP 확인을 DS와 동일하게 항상 수행하고, 콘솔 미지원 시 `EOF` 처리로 기본값 사용.
  - 초기화 단계에서 `Go1 SDK Ready/Missing` 로그를 출력해 Simulation fallback 원인을 즉시 확인 가능하게 개선.

### [2026-03-31 14:30:00] 카메라 프레임 저장 노드 구현 (Go1_DS.py 저장 로직 이식)
- 문제 분석:
  - `VideoSourceNode`, `FisheyeUndistortNode`, `ArUcoDetectNode`, `FlaskStreamNode`는 존재했으나, **프레임을 지정된 폴더에 저장하는 노드가 없음**.
  - Go1_DS.py의 `CameraControlNode`처럼 JPEG 파일로 연속 저장하고, 타이머/Start/Stop 제어하는 기능이 누락되어 있었음.
  - 데이터 플로우 기반 아키텍처에서는 저장 기능을 별도 노드로 분리하는 것이 `SRP` 준수.
- 조치 방안:
  - **`VideoFrameSaveNode` 신규 구현**:
    - 입력: `VideoSourceNode`의 Frame 데이터
    - UI: 저장 폴더 경로, 지속 시간(타이머), Start/Stop 체크박스
    - 기능: 입력 프레임을 `cv2.imwrite()`로 `frame_XXXXXX.jpg` 형식으로 저장
    - 상태: `camera_save_state` 전역변수로 저장 진행 상황(Stopped/Running), 프레임 카운트, 타이머 관리
  - **`nodes/robots/go1.py` 개선**:
    - 임포트 추가: `subprocess`, `glob`, `datetime`, `deque`
    - 전역 변수 추가: `camera_save_state`, `camera_save_queue` (향후 GStreamer 확장 대비)
    - `VideoFrameSaveNode` 클래스 신규 추가
  - **`core/factory.py` 등록**:
    - `VideoFrameSaveNode` 임포트 및 `VIS_SAVE` 타입 등록
  - **`ui/dpg_manager.py` UI 통합**:
    - `NodeUIRenderer._render_video_save()` 렌더러 메서드 신규 구현
    - `sync_ui_to_state()`, `sync_state_to_ui()`에 `VIS_SAVE` 처리 추가
    - 노드 생성 버튼 메뉴에 "SAVE" 버튼 추가
- 파이프라인 구성 예시:
  ```
  Video Source → [Fisheye Undistort] → [ArUco Detect] → [Video Save] → [Flask Stream]
                       (선택)                  (선택)          (폴더 저장)   (웹 스트리밍)
  ```
- 수정 及 신규 파일:
  - `nodes/robots/go1.py` (임포트, 전역변수, `VideoFrameSaveNode` 추가)
  - `core/factory.py` (`VideoFrameSaveNode` 임포트 및 등록)
  - `ui/dpg_manager.py` (렌더러, 동기화, 버튼 추가)
- 수정 파일:
  - `nodes/robots/go1.py`

### [2026-03-31 15:05:00] Go1 카메라 로직을 Go1_DS.py 방식으로 전환
- 문제 분석:
  - 기존 `VIDEO_SRC`는 RTSP 직접 연결(`cv2.VideoCapture`) 방식이라, `Go1_DS.py`의 카메라 제어 흐름(나노 SSH 제어 + GStreamer 수신 + JPG 파일 기반 처리)과 달랐음.
- 조치 방안:
  - `nodes/robots/go1.py`
    - `camera_command_queue`, `GO1_CAMERA_NANOS`, `CAMERA_CONFIG`, `camera_worker_thread()` 추가.
    - `init_go1_connection()`에서 카메라 워커 스레드를 1회 자동 시작하도록 변경.
    - `VideoSourceNode`를 DS 방식으로 변경:
      - Start 시 `START_CMD(target_ip, folder, duration)` 큐에 등록
      - Stop 시 `STOP` 큐에 등록
      - 출력 프레임은 폴더의 최신 JPG 파일(안정성을 위해 최근 2번째 파일)에서 읽어 Data 출력
  - `ui/dpg_manager.py`
    - `VIDEO_SRC` UI를 `Target IP`, `Folder`, `Timer(s)`, `Start Stream`으로 변경.
    - `sync_ui_to_state`, `sync_state_to_ui`를 신규 필드(`target_ip`, `folder`, `duration`)에 맞게 수정.
  - `core/engine.py`
    - `VIS_SAVE`를 flowless 실행 목록에 추가하여 데이터 노드로 주기 실행되도록 보완.
- 수정 파일:
  - `nodes/robots/go1.py`

### [2026-03-31 21:42:44] 그래프 저장/불러오기 Go1·EP01 호환성 수정
- 문제 분석:
  - 저장은 `node.state` 기준인데, 실행 중이 아닐 때는 UI 값이 `state`로 동기화되지 않아 Go1/EP01 노드 설정이 기본값으로 저장되는 문제가 있었음.
  - 일부 Go1/EP01 렌더러가 매 렌더마다 임의 Flow 입력 포트를 새로 생성해 포트 안정성이 떨어졌고, 불러오기 시 링크 인덱스 기반 복원과 충돌 가능성이 있었음.
- 조치 방안:
  - `core/serializer.py`
    - `save_graph()` 시작 시 `NodeUIRenderer.sync_ui_to_state()`를 호출하도록 변경.
    - 실행/정지 상태와 무관하게 현재 UI 값이 저장 JSON에 반영되도록 보장.
  - `nodes/robots/go1.py`
    - `Go1KeyboardNode`, `Go1UnityNode`에 고정 `in_flow` 입력 포트 추가.
  - `ui/dpg_manager.py`
    - `GO1_KEYBOARD`, `GO1_UNITY`, `GO1_ACTION`, `EP_ACTION` 렌더러에서 임의 포트 생성 제거.
    - 각 노드의 고정 포트(`node.in_flow`)를 사용하도록 변경.
- 기대 효과:
  - Go1/EP01 노드의 콤보/입력값이 저장 파일에 정확히 반영됨.
  - 불러오기 후 노드 설정과 링크 연결의 재현성이 향상됨.
- 수정 파일:
  - `core/serializer.py`
  - `nodes/robots/go1.py`
  - `ui/dpg_manager.py`

### [2026-03-31 21:51:29] 카메라 스트리밍 프레임워크 고도화 (타이머/파일제한/자동종료)
- 문제 분석:
  - 현재 `VideoSourceNode`의 타이머는 단순 경과 시간 로그만 하고, 타이머 완료 시 자동 종료 기능이 없었음.
  - 타이머 비활성화 시 이전에 저장된 이미지 파일이 누적되기만 하고, 개수 제한이 없어 저장공간이 무한정 증가할 수 있었음.
  - `VideoFrameSaveNode`가 Flow in 포트가 없어 노드 그래프 흐름 제어가 불가능했음.
  - `camera_worker_thread()`의 START_CMD에서 타이머 정보를 전달받지 않아, 타이머 완료 시 UI 상태 동기화가 어려웠음.

- 조치 방안:
  - **`nodes/robots/go1.py`**:
    - `VideoSourceNode.__init__()`에 두 가지 새로운 상태 옵션 추가:
      - `use_timer`: 타이머 기능 ON/OFF 토글 (기본값: True)
      - `max_frames`: 타이머 비활성화 시 유지할 최대 파일 개수 (기본값: 100)
    - `VideoSourceNode.execute()`에서 `use_timer=False`이고 파일 개수가 `max_frames` 초과 시, 오래된 파일부터 자동 삭제하도록 로직 추가.
    - START_CMD 명령에 5번째 파라미터로 `use_timer` 정보 추가.
    - camera_state 전역변수에 `use_timer` 초기화.
    
    - **`VideoFrameSaveNode` 개선**:
      - `__init__()`에 `in_flow` 입력 포트 추가 (Flow in - 노드 실행 트리거 용도).
      - `execute()`에서 `duration > 0`일 때의 타이머 완료 로직을 개선:
        - 타이머 만료 시 `is_saving=False`로 자동 변경
        - 주 실행 루프의 저장 프레임 카운트 초기화
      
    - **`camera_worker_thread()` 강화**:
      - START_CMD에서 5개 파라미터(`use_timer` 포함) 추출.
      - `camera_state['use_timer']` 설정.
      - Timer ON(`use_timer=True`) 상태에서 경과 시간 체크: 타이머 완료 시
        - `[Cam Timer]` 완료 로그 출력
        - GStreamer 수신 프로세스 자동 종료
        - `camera_state['status'] = 'Stopped'`로 변경
        - **모든 노드의 자동 동기화**: `node_registry`에서 `VIDEO_SRC`와 `VIS_SAVE` 노드를 찾아 `is_running/is_saving` 플래그를 False로 자동 설정
      - Timer OFF(`use_timer=False`) 상태에서는 매 10초마다 `[Cam Running]` 로그만 출력하고 지속.
  
  - **`ui/dpg_manager.py`**:
    - `_render_video_src()` UI 개선:
      - 기존: `Target IP`, `Folder`, `Timer(s)`, `Start Stream`
      - 추가: `Use Timer` 체크박스 (타이머 ON/OFF 토글)
      - 추가: `Max Frames` 정수 입력 필드 (타이머 OFF 시 최대 유지 파일 개수)
    - `sync_ui_to_state()` VIDEO_SRC 분기에 두 가지 새 필드 동기화 추가:
      - `node.state['use_timer'] = dpg.get_value(node.ui_use_timer)`
      - `node.state['max_frames'] = dpg.get_value(node.ui_max_frames)`
    - `sync_state_to_ui()` VIDEO_SRC 분기에 역 동기화 추가:
      - `dpg.set_value(node.ui_use_timer, node.state.get('use_timer', True))`
      - `dpg.set_value(node.ui_max_frames, node.state.get('max_frames', 100))`
    
    - `_render_video_save()` UI 개선:
      - Flow in 속성 추가 (노드 실행 흐름 제어)
      - 레이아웃: [Flow In] → [Frame In] → [설정] → [Frame Out], [Flow Out]

- 기대 효과:
  1. **타이머 ON 시**: 지정 시간 경과 후 카메라와 저장이 자동으로 중지되어 리소스 낭비 방지
  2. **타이머 OFF 시**: 무한 스트리밍하되, 저장 폴더 크기 제한으로 저장공간 고갈 회피
  3. **상태 동기화**: 타이머 자동 완료 시 UI가 자동으로 "Start Stream" 체크박스를 해제하여 사용자가 즉시 인식 가능
  4. **Video Save 노드 연결성**: Flow in 포트 추가로 START 노드와의 순차적 연결 가능 → 일관된 노드 그래프 구조

- 수정 파일:
  - `nodes/robots/go1.py` (VideoSourceNode 옵션, camera_worker_thread 자동 종료, VideoFrameSaveNode Flow in)
  - `ui/dpg_manager.py` (_render_video_src UI 개선, sync 메서드 추가)

### [2026-03-31 22:04:37] VideoSave 중심 기능 재배치 + GO1_DRIVER 타입 복구
- 문제 분석:
  - 직전 수정에서 타이머/Max Frames 기능이 `Video Source`로 들어가 있었고, 요구사항상 해당 기능은 `Video Save`에 있어야 했음.
  - `GO1_DRIVER` 노드를 생성해도 내부 클래스(`UniversalRobotNode`)가 타입을 `MT4_DRIVER`로 고정 저장해, 저장/불러오기 후 MT4 Driver로 바뀌는 오류가 발생했음.

- 조치 방안:
  - `nodes/robots/go1.py`
    - `VideoSourceNode`에서 다음 항목 제거:
      - `duration`, `use_timer`, `max_frames`
      - 타이머 기반 자동 종료/파일 삭제 로직
    - `VideoSourceNode`는 스트리밍 시작/중지 및 프레임 출력만 담당하도록 단순화.
    - `VideoFrameSaveNode`에 기능 이관:
      - `use_timer`(ON/OFF), `max_frames` 상태값 추가
      - Timer ON: `duration` 경과 시 저장 자동 종료 + `VIDEO_SRC` 자동 정지
      - Timer OFF: 저장 파일 개수가 `max_frames` 초과 시 오래된 파일부터 삭제
    - 저장 상태 전환 로직을 정리해 반복 "저장 시작/완료" 로그가 발생하지 않도록 보완.
    - `camera_worker_thread()`는 스트림 제어 전담으로 복귀(타이머 판단 제거).

  - `ui/dpg_manager.py`
    - `VIDEO_SRC` UI/동기화에서 `Duration`, `Use Timer`, `Max Frames` 제거.
    - `VIS_SAVE` UI/동기화에 `Duration`, `Use Timer`, `Max Frames` 추가.

  - `nodes/robots/mt4.py`
    - `UniversalRobotNode` 생성자에 `node_label`, `node_type` 인자를 추가해 타입 하드코딩(`MT4_DRIVER`) 제거.

  - `core/factory.py`
    - 드라이버 생성 시 타입 명시:
      - MT4: `MT4_DRIVER`
      - Go1: `GO1_DRIVER`
      - EP: `EP_DRIVER`

  - `core/serializer.py`
    - 로드 호환성 마이그레이션 추가:
      - 저장 파일의 타입이 `MT4_DRIVER`여도, 설정 키에 `vx/vy/vyaw/body_height`가 있으면 `GO1_DRIVER`로 자동 보정 후 생성.
      - 기존에 잘못 저장된 파일도 불러오기 시 자동 복구 가능.

- 기대 효과:
  1. 타이머/Max Frames 관리 책임이 `Video Save`로 일원화되어 노드 역할이 명확해짐.
  2. 저장 타이머 완료 시 스트리밍까지 함께 정지되어 실제 운용 흐름과 일치.
  3. 신규 저장 파일에서 `GO1_DRIVER` 타입이 올바르게 유지됨.
  4. 과거 잘못 저장된 파일도 로드 시 자동으로 `GO1_DRIVER`로 복원됨.

- 수정 파일:
  - `nodes/robots/go1.py`
  - `ui/dpg_manager.py`
  - `nodes/robots/mt4.py`
  - `core/factory.py`
  - `core/serializer.py`

### [2026-03-31 22:18:18] Run Script/Stop 단일 제어화 + VIS_SAVE 로그 정리
- 문제 분석:
  - 타이머 OFF 상태에서 `Max Frames` 정리와 STOP 시 저장 중단이 불안정했던 원인은 `Start Saving` 체크박스 상태(`is_saving`)에 저장 로직이 종속돼 있었기 때문.
  - `Video Source`도 `Start Stream` 체크박스(`is_running`)를 별도로 사용해, 사용자 의도인 "Run Script/Stop 단일 기준"과 동작이 분리돼 있었음.
  - `[VIS_SAVE] N개 저장됨` 로그는 실제 파일 개수와 체감 차이가 생겨 오해를 유발함.

- 조치 방안:
  - `nodes/robots/go1.py`
    - `VideoSourceNode.execute()` 실행 조건을 `engine_module.is_running`으로 변경.
    - `VideoFrameSaveNode.execute()` 실행 조건을 `engine_module.is_running`으로 변경.
    - `VideoFrameSaveNode`에서 `is_saving` 상태 의존 제거.
    - STOP 전환 시 저장 중단 로그는 유지하되, 프레임 개수 표기는 제거.
    - 타이머 완료 로그의 프레임 개수 표기 제거.
    - 10프레임 간격 저장 개수 로그 제거.
    - 타이머 완료 후 자동 재시작 방지를 위해 `VideoSourceNode._auto_stopped_by_timer` 런타임 플래그 추가.
      - 타이머 완료 시 `_auto_stopped_by_timer=True`
      - 다음 `Run Script` 시작 시 플래그 해제하여 재실행 가능

  - `ui/dpg_manager.py`
    - `VIDEO_SRC`에서 `Start Stream` 체크박스 제거.
    - `VIS_SAVE`에서 `Start Saving` 체크박스 제거.
    - `sync_ui_to_state`/`sync_state_to_ui`에서 `VIDEO_SRC.is_running`, `VIS_SAVE.is_saving` 동기화 제거.
    - `toggle_exec()`에서 STOP 시 카메라 워커에 `('STOP', '')` 명령을 즉시 큐잉해 수신 프로세스가 끊기도록 보강.
    - `toggle_exec()`에서 RUN 시작 시 타이머 자동정지 플래그를 초기화.

- 기대 효과:
  1. Start/Stop 기준이 완전히 `Run Script/Stop`으로 통일됨.
  2. STOP 버튼 즉시 저장/스트리밍 중단 안정성 향상.
  3. Timer OFF 시 `Max Frames` 정책이 실제 저장 루프에서 일관되게 적용.
  4. 혼동을 주던 `[VIS_SAVE] 저장 개수` 로그 제거로 로그 가독성 개선.

- 수정 파일:
  - `nodes/robots/go1.py`
  - `ui/dpg_manager.py`

### [2026-03-31 22:26:08] VIS_SAVE 타이머 반복 재시작 방지 + MaxFrames 삭제 안정화
- 문제 분석:
  - Timer ON에서 `[VIS_SAVE] 타이머 완료` 후 `_save_start_time`만 초기화되고 `Run Script`는 계속 ON 상태라, 같은 실행 세션에서 즉시 다시 `저장 시작`으로 재진입하는 반복 로그가 발생함.
  - Timer OFF에서 MaxFrames가 기대대로 줄지 않던 원인은 저장 폴더가 소스 수신 폴더(`go1_front`)와 동일했고, 삭제 대상이 `*.jpg` 전체여서 GStreamer가 쓰는 파일과 섞이며 삭제 실패가 묻혔을 가능성이 큼.

- 조치 방안:
  - `nodes/robots/go1.py`
    - `VideoFrameSaveNode`에 `_timer_completed_this_run` 플래그 추가.
      - Timer 완료 시 `True`로 설정해 동일 Run 세션에서 재시작 차단.
      - `Stop` 후 재실행 시(`engine_module.is_running=False -> True`) 자동 리셋.
    - MaxFrames 삭제 대상을 `*.jpg`에서 `frame_*.jpg`로 한정해 VIS_SAVE가 생성한 파일만 관리.
    - 삭제 실패 예외를 완전 무시하지 않고, 첫 실패 케이스를 로그로 출력하도록 추가:
      - `[VIS_SAVE] MaxFrames 삭제 실패(예시): <file> (<error>)`
    - 기본 저장 폴더를 `Captured_Images/go1_saved`로 변경해 소스 수신 파일(`front_*.jpg`)과 저장 파일(`frame_*.jpg`)을 분리.
      - `camera_save_state['folder']` 기본값도 동일하게 정합화.

  - `ui/dpg_manager.py`
    - `VIS_SAVE` 폴더 UI 기본값을 `Captured_Images/go1_saved`로 변경.
    - `sync_state_to_ui()`의 `VIS_SAVE` 폴더 기본 fallback도 `go1_saved`로 통일.

- 권한(삭제 Permission) 탐구 결과:
  - 코드상 삭제 로직은 `os.remove()`로 정상 구현되어 있으며, 권한 자체가 없어 항상 실패하는 구조는 아님.
  - 다만 Windows에서 파일이 타 프로세스에 의해 사용 중일 때(예: GStreamer가 쓰는 파일) `PermissionError`가 발생할 수 있음.
  - 이번 수정으로 삭제 대상을 VIS_SAVE 산출물(`frame_*.jpg`)만으로 분리했기 때문에, 파일 잠금/권한 충돌 가능성을 크게 줄였고, 실패 시 로그로 원인 확인 가능.

- 기대 효과:
  1. Timer ON에서 "타이머 완료 ↔ 저장 시작" 반복 로그 현상 제거.
  2. Timer OFF에서 MaxFrames가 VIS_SAVE 저장 파일 기준으로 안정적으로 동작.
  3. 권한/잠금 문제가 실제로 있을 경우 로그로 즉시 원인 파악 가능.

- 수정 파일:
  - `nodes/robots/go1.py`
  - `ui/dpg_manager.py`

### [2026-03-31 22:38:37] 최하단 재기록: VIS_SAVE 타이머/강제 STOP 재시작 불능 수정
- 정정 사유:
  - 직전 변경 이력이 파일 최하단이 아닌 위치에 기록되어, 문서 누적 규칙(항상 맨 아래 append)을 위반함.

- 핵심 수정 요약:
  - `core/engine.py`
    - flowless 자동 실행 목록에서 `VIS_SAVE` 제거 (중복 실행 경로 차단)
  - `nodes/robots/go1.py`
    - `VideoSourceNode`에 `_last_frame` 캐시 추가 (프레임 공백 구간에서도 저장 지속)
    - `VideoFrameSaveNode`에 `_prune_saved_frames()` 추가
    - Timer OFF에서도 주기적으로 MaxFrames 정리 수행
  - `ui/dpg_manager.py`
    - `toggle_exec()`에서 RUN/STOP 전환 시 `VIDEO_SRC`/`VIS_SAVE` 런타임 상태 강제 초기화

- 기대 효과:
  1. Timer ON에서 저장 시작/완료 반복 로그 및 저장 미진행 현상 완화
  2. Timer OFF에서 MaxFrames 정리 안정성 향상
  3. 타이머 중 STOP 후 다음 RUN에서 시작 불가 현상 완화

- 수정 파일:
  - `core/engine.py`
  - `nodes/robots/go1.py`
  - `ui/dpg_manager.py`

### [2026-04-01 10:15:00] VideoFrameSaveNode MaxFrames 초과 시 파일 삭제 미작동 근본 원인 제거
- 문제 분석:
  - **타이머 OFF 상태에서 Max Frames 수를 초과한 파일들이 삭제되지 않는 현상** 발생.
  - 원인: `_prune_saved_frames()` 호출이 프레임 저장 성공 여부에 종속되어 있었음.
    1. 첫 번째 호출: `if self._save_start_time is not None and not use_timer:` (프레임 로드 **전**)
    2. 두 번째 호출: 프레임 저장 성공 시에만 실행 (프레임 로드 **후**, `if success` 블록 **내부**)
  - 만약 프레임이 `None`이거나 `cv2.imwrite()` 실패 시, 두 번째 호출이 실행되지 않음.
  - 특히 첫 번째 호출 시점에는 아직 새 프레임이 저장되지 않았으므로:
    - 이미 100개(max_frames) 파일이 있으면 `len(files) <= max_frames` 조건에 걸려 정리 안 함
    - 이후 프레임 저장으로 101개가 되어도 다음 실행까지는 위상이 맞지 않음
  - **결과**: 파일 개수가 max_frames을 초과하는 상태가 지속.

- 조치 방안:
  - `nodes/robots/go1.py` `VideoFrameSaveNode.execute()`
    - 기존 두 개의 분산된 호출을 **하나의 단일 호출**로 통합
    - 위치: 지정된 폴더에 프레임이 저장되는 Try 블록 **완료 후**에만 호출
    - **조건**: `if self._save_start_time and not use_timer:` (프레임 저장 성공/실패 무관)
    - 효과: 매 실행마다 안정적으로 MaxFrames 정리 수행 보장
    
    ```python
    # 기존 문제 코드:
    if self._save_start_time is not None and not use_timer:
        self._prune_saved_frames(folder, max_frames)  # 1차 호출 (저장 전)
    
    # ... 프레임 저장 로직 ...
    if frame is not None and HAS_CV2 and self._save_start_time is not None:
        try:
            # 파일 저장
            if success:
                if not use_timer:
                    self._prune_saved_frames(folder, max_frames)  # 2차 호출 (저장 성공 시만)
    
    # 수정 후 (단일 호출):
    if frame is not None and HAS_CV2 and self._save_start_time is not None:
        # 파일 저장
        try:
            ...
        except Exception as e:
            ...
    
    # 타이머 OFF인 경우, 프레임 저장 성공 여부와 무관하게 항상 max_frames 초과 파일 정리
    if self._save_start_time and not use_timer:
        self._prune_saved_frames(folder, max_frames)
    ```

- 기대 효과:
  1. 타이머 OFF에서 MaxFrames가 **매 실행마다 안정적으로 적용**됨.
  2. 프레임이 간헐적으로 None이거나 저장 실패해도 **정리 로직은 항상 실행**.
  3. 저장 폴더의 파일 개수가 max_frames 범위 내에서 일정하게 유지.

- 수정 파일:
  - `nodes/robots/go1.py` (VideoFrameSaveNode.execute() 로직 재정리)

### [2026-04-01 11:40:00] VIS_SAVE 타이머 종료 불능 수정 + 카메라 STOP 강제화 + 종료 로그 추가
- 문제 분석:
  - `Use Timer`를 켜고 `Duration`을 설정해 실행해도, 시간이 지나도 이미지 저장이 계속되는 현상이 발생.
  - 원인 1: 타이머 만료 판정이 `VideoFrameSaveNode.execute()` 주기/상태 동기화에 의존하여, 실행 순서나 조건에 따라 만료 처리 누락 가능성이 있었음.
  - 원인 2: 타이머 만료 후 `STOP` 명령이 전달되어도, 로컬 GStreamer 수신 프로세스가 항상 확실히 종료되지 않아 파일 생성이 지속될 수 있었음.

- 조치 방안:
  - `nodes/robots/go1.py`
    - 카메라 워커(`camera_worker_thread`)에서 타이머를 직접 감시하도록 변경.
      - `duration > 0`일 때 시작 시각을 기록하고, 만료 시 즉시 `STOP` 경로로 진입.
      - 만료 시 로그 추가: `[Cam Timer] 카메라 타이머 종료`.
    - 수신 프로세스 핸들(`camera_receiver_proc`) 추적 추가.
      - `START_CMD` 처리 전에 기존 수신 프로세스가 있으면 `terminate/kill`로 정리 후 재시작.
      - `STOP` 처리 시 `terminate -> wait -> kill` 순으로 강제 종료하고, 기존 `pkill`은 보조 수단으로 유지.
    - `START_CMD`의 폴더 인자가 비어 있을 때 안전 기본 경로로 치환하도록 방어 로직 추가.
    - `VideoSourceNode`가 `START_CMD`를 보낼 때, `VIS_SAVE` 상태의 `use_timer/duration` 값을 읽어 전달하도록 보강.
    - `use_timer` 값이 문자열(`"true"/"false"`)로 들어오는 경우도 정상 판정하도록 파싱 보강.

- 기대 효과:
  1. Timer ON + Duration 경과 시 저장 파이프라인이 자동으로 종료됨.
  2. 타이머 만료 후에도 수신 프로세스가 남아 저장이 지속되는 문제를 차단.
  3. 종료 시점이 로그로 명확히 확인 가능.

- 수정 파일:
  - `nodes/robots/go1.py`

### [2026-04-01 11:55:00] 텍스트 입력 중 로봇 키보드 제어 오동작 차단
- 문제 분석:
  - 키보드 제어 실행 중 폴더명/파일명 등 텍스트 입력창에 포커스가 있을 때도 제어 키 입력이 로봇 제어로 함께 전달되는 현상이 발생.
  - 기존 포커스 예외 처리가 특정 입력창에만 한정되어 있어, 노드 속성 입력창(예: 저장 폴더)에서는 차단되지 않았음.

- 조치 방안:
  - `ui/dpg_manager.py`
    - 전체 UI 아이템을 순회하여 `input_text` 타입 포커스 여부를 감지하는 헬퍼 추가.
    - 기존 제한적 포커스 조건을 전역 텍스트 입력 포커스 체크로 교체.

- 기대 효과:
  1. 텍스트 입력 중 `W/A/S/D` 등 키를 눌러도 로봇 제어가 동작하지 않음.
  2. 폴더명/경로 입력 시 의도치 않은 로봇 이동 방지.

- 수정 파일:
  - `ui/dpg_manager.py`

### [2026-04-02 18:36:25] VIS_SAVE Max Frames 동작 조건 확인 및 정렬/인덱스 안정화
- 문제 분석:
  - 요구사항은 **Max Frames 정리 기능이 타이머 OFF 상태에서만 동작**해야 함.
  - 기존 실행 흐름 점검 결과, `VideoFrameSaveNode.execute()`에서 Max Frames 정리 호출은 `not use_timer` 조건으로 제한되어 있었음.
  - 다만 폴더 내 오래된 파일 판별과 저장 인덱스 초기화 방식이 환경에 따라 불안정할 여지가 있어, 실사용에서 개수 유지가 기대와 다르게 보일 가능성이 있었음.

- 조치 방안:
  - `nodes/robots/go1.py`의 `VideoFrameSaveNode`에 아래 보강 적용:
    - 파일명(`front_000001.jpg`)에서 프레임 인덱스를 추출하는 `_extract_frame_index()` 추가.
    - 저장 시작 시 기존 폴더 파일을 스캔해 다음 인덱스를 맞추는 `_sync_frame_index_from_folder()` 추가.
    - `_prune_saved_frames()` 정렬 기준을 생성시간 단독 기준에서 **파일명 인덱스 우선**으로 개선.
    - `max_frames` 입력값이 문자열/실수여도 안전하게 파싱되도록 방어 로직 추가.
  - 동작 조건 재확인:
    - Max Frames 정리는 `if self._save_start_time is not None and not use_timer:`에서만 수행됨.
    - 즉, 타이머 ON에서는 Max Frames 삭제 로직이 실행되지 않음.

- 기대 효과:
  1. 타이머 OFF에서만 오래된 파일 삭제가 수행되어 요구사항과 정확히 일치.
  2. 시간이 지나도 저장 파일 수가 `max_frames` 범위로 안정적으로 유지.
  3. 실행 재시작 후에도 인덱스 충돌/역정렬 가능성이 줄어 정리 동작 신뢰성 향상.

- 수정 파일:
  - `nodes/robots/go1.py`


### [2026-04-02 18:42:00] Server Sender 노드 구현 (Go1_DS.py → go1.py 이식)
- 문제 분석:
  - 기존 Go1_DS.py의 **Server Sender 기능** (비동기 HTTP 멀티파트 이미지 업로드)이 go1.py에 구현되지 않음.
  - 사용자 요구:  Server Sender 기능을 go1.py에 구현하고 dpg_manager.py/factory.py 등 통합
  
- 조치 방안:
  - 
odes/robots/go1.py 수정:
    - 임포트: import asyncio, import aiohttp (선택사항, 없으면 기능 비활성화)
    - 글로벌 변수: sender_state, sender_command_queue, multi_sender_active, TARGET_FPS, INTERVAL, _SENDER_MANAGER_STARTED
    - 함수 추가 (4종):
      - send_image_async(): 파일 읽기 → aiohttp multipart/form-data 구성 → 2초 타임아웃 HTTP POST
      - camera_async_worker(): CAMERA_CONFIG 폴더 모니터링 → 최신 파일 감지 → 중복 방지 → 비동기 업로드 (10Hz)
      - start_async_loop(): asyncio 이벤트루프 생성/실행
      - sender_manager_thread(): 송신 명령 큐 감시 → START/STOP 처리
    - 클래스 추가: ServerSenderNode
      - 입력: Flow In
      - 출력: Flow Out
      - 상태: ction (Start Sender/Stop Sender), server_url (기본: http://192.168.1.100:5001/upload)
      - execute(): action 변경 감지 → 명령 큐잉
    - init_go1_connection(): HAS_AIOHTTP 확인 후 sender_manager_thread() daemon 시작
  
  - core/factory.py 수정:
    - ServerSenderNode import 추가
    - create_node(): elif node_type == GO1_SERVER_SENDER: node = ServerSenderNode(node_id)
  
- 설계 특징:
  1. 비동기: aiohttp + asyncio로 메인 스레드 블로킹 방지
  2. 다중 카메라: CAMERA_CONFIG 크기만큼 병렬 워커 생성
  3. 중복 방지: 최신 파일 경로 추적
  4. 안전 종료: multi_sender_active 플래그
  5. Flow 제어: VideoFrameSaveNode와 체인 연결 가능
  
- 파이프라인:
  `
  [Video Source] → [Video Save] → [Server Sender] → [후속노드]
  `

- 성능 파라미터:
  - TARGET_FPS = 10 (초당 10회 업로드 시도)
  - INTERVAL = 0.1초 (100ms)
  - HTTP timeout = 2.0초

- 수정 파일:
  - 
odes/robots/go1.py (함수/클래스 추가)
  - core/factory.py (ServerSenderNode 등록)

### [2026-04-02 18:43:00] Server Sender 노드 생성 UI 버튼 추가
- 문제 분석:
  - Server Sender 노드의 백엔드 구현(go1.py 클래스, factory.py 등록, dpg_manager.py 렌더러/동기화)은 완료되었으나, 사용자가 시각 에디터에서 노드를 생성할 UI 버튼이 없었음.
  - 기존 Go1 노드들(GO1_ACTION, GO1_KEYBOARD, GO1_UNITY, VIDEO_SRC 등)은 모두 dpg_manager.py의 노드 메뉴에 생성 버튼이 있었음.

- 조치 방안:
  - `ui/dpg_manager.py`
    - 노드 생성 메뉴의 "Go1 & Vision" 그룹에서 "GO1 ACTION" 버튼 바로 다음에 "GO1 SENDER" 버튼 추가.
    - 버튼 콜백: `add_node_cb` (기존 다른 노드와 동일)
    - 사용자 데이터: `"GO1_SERVER_SENDER"` (factory.py에 등록된 노드 타입 정확히 일치).

- 기대 효과:
  1. 사용자가 노드 메뉴의 "GO1 SENDER" 버튼을 클릭하면 VideoFrameSaveNode처럼 Server Sender 노드를 생성 가능.
  2. 노드 그래프에서 [Video Save] → [Server Sender] 체인 연결로 이미지 업로드 파이프라인 구성 가능.
  3. 모든 진행도 추적 (분석/이식/통합/버튼 추가) 완료되어, 사용자가 즉시 기능 테스트 가능.

- 수정 파일:
  - `ui/dpg_manager.py` (900줄 근처, GO1 ACTION 다음에 GO1 SENDER 버튼 추가)

### [2026-04-02 19:00:00] Go1 Dashboard 하단 3패널 완전 제거
- 문제 분석:
  - Go1 Dashboard 하단의 3개 패널(Manual Control, Direct Speed & Actions, Speeds)을 더 이상 사용하지 않아 UI 단순화가 필요했음.
  - 패널 UI만 제거하고 갱신 코드를 남기면 존재하지 않는 태그에 `set_value`가 호출되어 런타임 오류 위험이 있음.

- 조치 방안:
  - `ui/dpg_manager.py`
    - Go1 Dashboard 하단 `with dpg.group(horizontal=True):` 내부의 아래 3개 child window 블록을 모두 삭제:
      1. `Manual Control`
      2. `Direct Speed & Actions`
      3. `Speeds`
    - 삭제 후 블록 구조 유지를 위해 `pass`만 남겨 레이아웃 문법 안정성 확보.
    - 제거된 `Speeds` 태그(`go1_dash_vx`, `go1_dash_vy`, `go1_dash_vyaw`)에 대한 주기 갱신 3줄도 함께 삭제.

- 기대 효과:
  1. Go1 Dashboard 하단 3패널이 완전히 사라져 UI가 단순해짐.
  2. 삭제된 태그 참조로 인한 런타임 오류 가능성 제거.

- 수정 파일:
  - `ui/dpg_manager.py`

### [2026-04-05 21:00:00] GO1 Server Sender 끊김 완화 적용 + 서버 호환성 복구 (해결 완료)
- 문제 분석:
  - `TARGET_FPS` 상향 후에도 송출 중 이전 프레임이 섞여 보이거나 끊김이 심하게 발생.
  - 원인 후보로 파일 선택 기준(`getctime`)의 불안정성, 쓰기 중 파일 업로드 가능성, 업로드 예외 가시성 부족이 확인됨.
  - 1차 완화 과정에서 업로드 파일명을 가변(`{camera_id}_{frame_tag}.jpg`)으로 변경했더니, 서버 측 파일명 규칙과 맞지 않아 연결/업로드 실패가 재발함.

- 조치 방안:
  - `nodes/robots/go1.py`
    - 유지:
      - `TARGET_FPS = 30` 유지 (전송 주기 개선)
      - 기존 서버 연결 구조(`ServerSenderNode` → `sender_command_queue` → `sender_manager_thread`)는 변경하지 않음
    - 끊김 완화 로직 추가:
      - 파일명 인덱스 기반 최신 프레임 선택 함수 추가 (`front_000001.jpg` 패턴 파싱)
      - 업로드 직전 파일 안정화 체크 추가 (짧은 간격으로 파일 크기 2회 확인)
      - `camera_async_worker()`에서 최신 파일 선택 시 인덱스 우선 로직 적용, 인덱스 파싱 불가 시 기존 방식 fallback
      - 업로드 타임아웃을 `2.0s -> 3.5s`로 완화
      - 업로드/워커 예외를 로그로 남기도록 변경 (`[Server Sender] upload error`, `[Server Sender] worker error`)
    - 서버 호환성 복구:
      - 업로드 파일명은 다시 고정 규칙으로 원복: `{camera_id}_calib.jpg`
      - 가변 파일명(`frame_tag`) 관련 시그니처/호출 제거

- 기대 효과:
  1. 서버 연결 호환성을 유지한 상태에서 송출 안정성(프레임 선택/완성 파일 업로드) 향상
  2. 전송 실패 시 로그로 즉시 원인 추적 가능
  3. 기존 큐/스레드 기반 제어 구조를 유지해 런타임 회귀 위험 최소화

- 최종 상태:
  - 서버 연결 정상 동작 확인
  - `TARGET_FPS = 30` 유지
  - 끊김 완화 로직은 유지하되, 서버 비호환을 유발한 파일명 가변화는 제거

- 수정 파일:
  - `nodes/robots/go1.py`

### [2026-04-09 19:54:39] Video Source/Save UI 상태 매칭 및 기본 경로 분리 (no container to pop 대응 점검 포함)
- 문제 분석:
  - 최근 기능 변경으로 `VideoSourceNode`에 `receiver_folder` 상태가 추가되었으나, UI(`dpg_manager.py`)에 입력 필드/동기화가 없어 코드-UI 상태 불일치가 발생할 수 있었음.
  - `VIDEO_SRC` 기본 수신 폴더와 `VIS_SAVE` 기본 저장 폴더가 모두 `Captured_Images/go1_front`로 같아, 기본 설정에서 프레임 입력/저장 루프(폭 축소 -> 1px 이미지)가 재발할 수 있었음.
  - 사용자 보고의 `no container to pop`은 DPG 컨테이너 스택 문제 신호로, 렌더 분기 누락/불일치 점검이 필요했음.
- 조치 방안:
  - `ui/dpg_manager.py`
    - `VIDEO_SRC` UI에 `Receiver Folder` 입력 필드 추가.
    - `sync_ui_to_state()`에 `receiver_folder` 동기화 추가.
    - `sync_state_to_ui()`에 `receiver_folder` 역동기화 추가.
    - `VIS_SAVE` UI 기본 폴더를 `Captured_Images/go1_saved`로 변경.
    - `VIS_SAVE` state->UI 기본값도 `Captured_Images/go1_saved`로 변경.
  - `nodes/robots/go1.py`
    - `VideoFrameSaveNode` 기본 저장 폴더를 `Captured_Images/go1_saved`로 변경.
- 기대 효과:
  1. 기능 변경 시 UI/state 불일치로 인한 오동작 가능성 감소.
  2. 기본 설정 상태에서 입력/출력 경로가 분리되어 프레임 재귀 축소 문제 재발 방지.
  3. VIDEO_SRC 관련 상태가 UI에서 명시적으로 관리되어 원인 추적이 쉬워짐.
- 수정 파일:
  - `ui/dpg_manager.py`
  - `nodes/robots/go1.py`

### [2026-04-09 20:00:11] DPG Alias 충돌 방지 및 노드 렌더 실패 롤백 보강 (Alias already exists / No container to pop 대응)
- 문제 분석:
  - 노드 추가 시 `dpg.node(tag=node.node_id)`에서 기존 alias(tag)와 충돌하면 `Alias already exists`가 먼저 발생하고, context manager 정리 단계에서 `No container to pop` 예외가 연쇄 발생함.
  - 기존 `add_node_cb`는 `NodeFactory.create_node()` 후 즉시 렌더하여, 충돌/렌더 실패 시 `node_registry`가 오염될 수 있었음.
  - `generate_uuid()`는 단순 증가값 반환이라, 특정 복원/충돌 시나리오에서 기존 레지스트리 키와 중복될 여지가 있었음.
- 조치 방안:
  - `ui/dpg_manager.py`
    - `add_node_cb()` 보강:
      - 노드 생성 직후 `dpg.does_item_exist(node.node_id)`로 alias 충돌 선검사.
      - 충돌 시 새 uid 재생성 후 `node.node_id` 재할당 + `node_registry` 키 재매핑.
      - 렌더 예외 발생 시 해당 노드 레지스트리 롤백(pop) 후 로그 출력.
  - `core/engine.py`
    - `generate_uuid()` 보강:
      - 생성 uid가 `node_registry`, `link_registry`에 없는지 확인 후 반환하도록 변경.
- 기대 효과:
  1. 노드 추가 시 alias 중복으로 인한 즉시 실패 예방.
  2. 렌더 실패 후 레지스트리 일관성 유지.
  3. `Alias already exists` -> `No container to pop` 연쇄 오류 재발 가능성 감소.
- 수정 파일:
  - `ui/dpg_manager.py`
  - `core/engine.py`

### [2026-04-09 20:08:42] Go1 Server Sender 미송출 수정 (VIS_SAVE 경로 연동 + Start 재트리거 보강)
- 문제 분석:
  - 이미지 보정/저장은 정상인데 서버 송출이 멈추는 현상은 송신 워커가 실제 저장 결과 폴더와 다른 경로를 감시할 때 발생 가능.
  - 기존 `ServerSenderNode.execute()`는 액션 값 변경시에만 START/STOP 큐를 넣어, `Start Sender` 상태 고정 후 재실행/재시작 시 송신이 재개되지 않을 수 있었음.
- 조치 방안:
  - `nodes/robots/go1.py`
    - `sender_manager_thread()` START 처리 시 업로드 원본 폴더를 `VIS_SAVE.folder` 우선으로 동기화.
    - `VIS_SAVE`가 없을 경우 `camera_save_state['folder']` 또는 `Captured_Images/go1_saved` fallback 사용.
    - START 로그에 실제 감시 폴더를 함께 출력해 진단성 향상.
    - `ServerSenderNode.execute()`를 의도상태 기반으로 보강:
      - 토글 변경이 없어도 `Start Sender` + 비활성 상태면 START 재요청.
      - `Stop Sender` + 활성 상태면 STOP 재요청.
      - 0.5초 쿨다운으로 중복 큐 삽입 방지.
- 기대 효과:
  1. 보정/저장 결과 폴더 기준으로 서버 송출이 일관되게 동작.
  2. 재실행/재시작 시 액션 콤보를 다시 바꾸지 않아도 송신 자동 복구.
  3. 폴더 불일치/상태 불일치 원인을 로그에서 즉시 확인 가능.
- 수정 파일:
  - `nodes/robots/go1.py`

### [2026-04-09 20:12:30] Fix Log 누락 점검 및 동기화 확인
- 점검 범위(금일 반영분):
  - Video Source/Save UI 상태 매칭 + 기본 경로 분리
  - DPG alias 중복 방지 + 렌더 실패 롤백
  - Server Sender 미송출 수정(VIS_SAVE 경로 연동 + Start 재트리거)
- 점검 결과:
  - 위 3건 모두 `code_fix_go1.md`에 기록되어 있으며, 추가 누락 항목 없음.
- 추가 조치:
  - 본 점검 결과를 최하단에 append하여 최신 상태 기준점을 명시.
- 관련 파일:
  - `code_fix_log/code_fix_go1.md`

### [2026-04-09 20:35:20] ArUco/Fisheye 노드 최근 수정 이력 보강 (누락분 반영)
- 문제 분석:
  - `code_fix_go1.md`에 ArUco/보정 노드의 "초기 이식" 이력은 있었으나, 최근 실제 동작 보정(자세 추정, JSON 저장, 반화면 crop 조건) 관련 상세 변경 이력이 별도 섹션으로 누락되어 추적성이 떨어졌음.

- 조치 방안:
  - `nodes/robots/go1.py`
    - `ArUcoDetectNode` 보강:
      - `cv2.solvePnP()` 기반으로 마커 pose(`x/y/z`)를 산출하도록 정리.
      - `marker_size_m` 상태값을 반영해 object points 생성 크기 동기화.
      - `input_undistorted` 옵션을 추가해, 보정 입력 사용 시 왜곡계수 0(`zero_dist_coeffs`)으로 pose 계산하도록 분기.
      - `draw_axes`, `draw_overlay_text` 옵션으로 축/텍스트 오버레이 표시 제어.
      - `json_path` 상태값 기반 JSON 파일 저장(상위 경로 자동 생성) + 저장 실패 로그 추가.
    - `FisheyeUndistortNode` 보강:
      - `crop_enabled`, `crop_mode(left_half/custom_ratio)`, `crop_ratio` 상태를 추가.
      - 반화면 crop은 "보정 enabled(use_calib=True)"일 때만 적용되도록 조건화하여, 보정 OFF 상태 원본 프레임이 의도치 않게 잘리지 않도록 수정.
  - `ui/dpg_manager.py`
    - ArUco UI/동기화 항목 반영:
      - `Marker Size (m)`, `Input Already Undistorted`, `Draw Axes`, `Draw Overlay Text`, `JSON Path`.
      - `sync_ui_to_state()` / `sync_state_to_ui()` 양방향 동기화 보강.
    - Fisheye UI/동기화 항목 반영:
      - `Crop Enabled`, `Crop Mode`, `Crop Ratio`.
      - `sync_ui_to_state()` / `sync_state_to_ui()` 양방향 동기화 보강.

- 기대 효과:
  1. ArUco 결과가 픽셀 중심점 기반이 아닌 실제 pose(`x/y/z`) 기준으로 일관되게 산출됨.
  2. Undistort 파이프라인 사용 여부에 따라 pose 계산 왜곡계수가 맞게 적용되어 정확도 편차를 완화.
  3. 보정 OFF 시 반화면 crop이 자동 비적용되어 원본 시야가 유지됨.
  4. ArUco JSON 산출물 경로를 UI에서 직접 관리 가능하고, 저장 실패 시 즉시 원인 추적 가능.

- 수정 파일:
  - `nodes/robots/go1.py`
  - `ui/dpg_manager.py`

### [2026-04-10 00:00:00] AP 모드 HW Online/배터리 미연동 판정 로직 수정
- 문제 분석:
  - 기존 `go1_keepalive_thread()`는 `udp.Recv()/GetRecv()` 성공만으로 Online을 판단하지 않고,
    `tick` 또는 IMU 합계값 변화가 있을 때만 수신 시각(`last_go1_recv_time`)을 갱신하고 있었음.
  - AP 모드에서는 패킷 수신이 유지되어도 값 변화가 미미한 구간이 발생할 수 있어,
    실제 연결 상태와 무관하게 Dashboard가 `Offline`으로 떨어지고 배터리가 `-1`로 유지되는 현상이 발생.

- 조치 방안:
  - `nodes/robots/go1.py`
    - `udp.GetRecv(state)` 성공 시점마다 `last_go1_recv_time = tnow`로 갱신하도록 수정.
    - `tick/imu 변화 기반` 수신 판정 코드를 제거.
    - 수신 예외(`except`)에서도 `go1_state['battery'] = -1`로 명시해 상태 일관성 보강.

- 기대 효과:
  1. AP/STA 환경과 무관하게 실제 수신 성공 기준으로 HW Online 판정이 안정화됨.
  2. Dashboard `HW`가 불필요하게 Offline으로 흔들리는 현상이 완화됨.
  3. 배터리 값이 연결 상태와 동기화되어 정상 갱신/리셋 흐름이 명확해짐.

- 수정 파일:
  - `nodes/robots/go1.py`

### [2026-04-10 00:10:00] AP 모드 우선 선택 프롬프트 및 기본 IP 자동 할당 추가
- 문제 분석:
  - 기존 초기화 흐름은 무조건 IP 입력부터 받아서, AP 모드에서 사용해야 하는 기본 IP `192.168.123.161`을 먼저 선택할 수 없었음.
  - 이 때문에 AP 접속 환경에서 잘못된 IP를 먼저 넣게 되거나, 기본 STA IP 흐름으로 진행되어 연결 실패로 이어질 수 있었음.

- 조치 방안:
  - `nodes/robots/go1.py`
    - `init_go1_connection()` 시작 시 AP 모드 여부를 먼저 묻는 `_prompt_go1_ap_mode()` 추가.
    - `y/yes` 선택 시 `GO1_IP = 192.168.123.161`로 자동 할당 후 바로 진행.
    - `n/no` 선택 시 기존 `_prompt_go1_ip()` 입력 흐름으로 이어지도록 유지.

- 기대 효과:
  1. 실행 직후 AP 접속 여부를 먼저 결정할 수 있어 사용자 입력 순서가 직관적으로 바뀜.
  2. AP 모드에서는 기본 접속 IP가 자동으로 들어가므로 연결 실패 가능성을 줄임.
  3. STA 모드는 기존처럼 수동 IP 확인 흐름을 유지해 호환성을 보존함.

- 수정 파일:
  - `nodes/robots/go1.py`

### [2026-04-10 00:20:00] AP 프롬프트만 유지하고 연결 분기 단순화
- 문제 분석:
  - AP 선택을 추가한 뒤에도 별도의 AP 보조 함수와 로그가 남아 있어, 초기 연결 흐름이 불필요하게 분리되어 보였음.
  - 사용 의도는 "처음에 AP 여부만 묻고, 이후 연결 로직은 기존 방식대로"였음.

- 조치 방안:
  - `nodes/robots/go1.py`
    - `_prompt_go1_ap_mode()`를 제거하고, `init_go1_connection()` 안에서만 AP 여부를 먼저 질문하도록 단순화.
    - `y/yes`일 때만 `GO1_IP = 192.168.123.161`으로 바로 설정.
    - `n/no`일 때는 기존 `_prompt_go1_ip()` 흐름으로 그대로 진행.
    - AP 선택 전용 로그(`Go1 AP Mode Selected`)는 제거해 연결 로그를 원래 흐름에 맞춤.

- 기대 효과:
  1. 실행 초반에는 AP 여부만 묻고, 나머지 IP 확인 로직은 기존대로 유지됨.
  2. AP와 STA 연결이 같은 계열의 흐름으로 처리되어 동작 예측이 쉬워짐.
  3. 불필요한 AP 전용 보조 로직이 사라져 초기화 코드가 더 단순해짐.

- 수정 파일:
  - `nodes/robots/go1.py`

### [2026-04-12 00:00:00] mode_Test.cpp 특수동작(9~13) Go1 모듈 이식 + Dashboard 버튼 연동
- 문제 분석:
  - `mode_Test.cpp`의 특수 퍼포먼스 동작(backflip/jumpYaw/straightHand/dance1/dance2)은 C++ 테스트 코드에만 있고,
    현재 Python 모듈(`nodes/robots/go1.py`)과 Dashboard(`ui/dpg_manager.py`)에서는 직접 실행 경로가 없었음.
  - Dashboard에서 버튼으로 실행하려면, 단순 UDP 문자열 전송이 아니라 Go1 keepalive 제어 루프 내부에서
    `mode1 선행 → mode 9~13 트리거 → 완료 감지/타임아웃 → 복귀` 시퀀스를 상태머신으로 운용해야 함.

- 조치 방안:
  - `nodes/robots/go1.py`
    - 전역 상태 추가:
      - `GO1_SPECIAL_ACTIONS` (동작명↔모드번호/타임아웃/복귀전략)
      - `go1_special_queue`, `go1_special_state`
      - `go1_dashboard['special']` 상태 문자열
    - API 추가:
      - `request_go1_special_action(action_name)`
      - Dashboard/노드에서 호출 시 큐에 적재하고 로그 남김
    - `go1_keepalive_thread()` 확장:
      - 특수동작 상태머신(`prep_stand → trigger → wait_done → recover`) 추가
      - 완료 감지는 수신 `state.mode` 기준으로 수행, 타임아웃 시 복구 경로 강제 진행
      - 복귀 규칙:
        - mode 9/10/11: 착지 대기 → mode8(Recovery) → mode1(Stand)
        - mode 12/13: 착지 대기 → mode0(Idle)
      - 특수동작 중에는 일반 주행 명령(vx/vy/wz)을 0으로 고정해 충돌 방지
    - `Go1ActionNode` 모드 확장:
      - `Backflip`, `Jump Yaw`, `Straight Hand`, `Dance 1`, `Dance 2` 추가

  - `ui/dpg_manager.py`
    - `go1_action_callback()` 확장:
      - `SPECIAL_*` user_data를 받으면 `request_go1_special_action()` 호출
    - Go1 Dashboard UI 확장:
      - `Special: ...` 상태 텍스트 추가
      - 특수동작 버튼 5개 추가:
        - `Backflip`, `JumpYaw`, `StraightHand`, `Dance1`, `Dance2`
    - Run Script STOP 시 정리 보강:
      - `go1_dashboard['special'] = 'Idle'`
      - `go1_special_queue.clear()`
    - Go1 Action 노드 콤보 목록에도 특수동작 5종 추가

- 기대 효과:
  1. Dashboard 버튼만으로 mode 9~13 특수동작을 즉시 실행 가능.
  2. C++ 테스트 코드와 유사한 안전 시퀀스(선행 자세/완료 감지/복귀)를 Python 런타임에서 재현.
  3. 특수동작 실행 중 일반 주행 명령 간섭을 차단해 동작 안정성 향상.

- 수정 파일:
  - `nodes/robots/go1.py`
  - `ui/dpg_manager.py`
  - `code_fix_log/code_fix_go1.md`

### [2026-04-12 00:20:00] Backflip 단독 실패 원인 수정 (특수모드 재트리거 제거)
- 문제 분석:
  - 사용자 테스트에서 `JumpYaw/StraightHand/Dance1/Dance2`는 동작하지만 `Backflip`만 실패.
  - 원인 후보를 C++ 원본(`mode_Test.cpp`)과 Python 이식(`go1.py`) 비교한 결과:
    1. C++는 mode9를 200ms 단발 트리거 후 `waitUntilDone` 동안 추가 송신 없이 수신 상태만 폴링.
    2. Python 이식은 `wait_done` 단계에서도 `target_mode=9`를 계속 송신해 재트리거/조건 충돌 가능.
    3. 특수동작 중 명령 기본값이 `footRaiseHeight=0.08` 등 일반주행 베이스를 유지해 백플립에 불리할 수 있음.

- 조치 방안:
  - `nodes/robots/go1.py`
    - `wait_done` 단계에서 `target_mode=1`로 변경.
      - 즉, 0.2초 트리거 이후에는 mode를 계속 밀지 않고 C++와 동일하게 완료 대기만 수행.
    - 특수동작 active 구간에서 cmd 파라미터를 중립값으로 고정:
      - `gaitType=0`, `speedLevel=0`, `footRaiseHeight=0.0`, `bodyHeight=0.0`
      - `euler=[0,0,0]`, `velocity=[0,0]`, `yawSpeed=0.0`, `reserve=0`

- 기대 효과:
  1. Backflip이 단발 트리거 조건으로 안정적으로 실행될 확률 향상.
  2. 특수모드 중 일반 보행 파라미터 간섭 제거.
  3. C++ 테스트 코드 실행 방식과 Python 런타임 동작이 더 정확히 일치.

- 수정 파일:
  - `nodes/robots/go1.py`
  - `code_fix_log/code_fix_go1.md`

### [2026-04-12 00:35:00] 특수동작 안정화 2차 보정 (wait_done 송신 억제 + Backflip 트리거 보강)
- 문제 분석:
  - 사용자 실측 결과:
    - Backflip: 미동작
    - JumpYaw: 점프 후 눕는 증상
    - StraightHand: 정상
    - Dance1/2: 코드 변경 후 비정상
  - 원인 추정:
    - C++ 원본은 특수모드 트리거 후 `waitUntilDone` 동안 추가 송신을 하지 않음.
    - Python 루프는 주기 송신 구조라, 대기 중 명령이 동작을 간섭할 수 있음.
    - Backflip은 트리거 민감도가 높아 20Hz 루프 기준 0.2초(약 10패킷)가 부족할 가능성 존재.

- 조치 방안:
  - `nodes/robots/go1.py`
    - 액션별 트리거 시간 파라미터 추가:
      - `backflip.trigger_sec = 0.4`
      - 나머지 특수동작은 `0.2` 유지
    - `wait_done` 단계 송신 억제:
      - `special_runtime.active and phase == 'wait_done'`일 때 `udp.Send()` 스킵
      - C++의 "트리거 후 대기(무송신)" 흐름에 가깝게 정렬

- 기대 효과:
  1. Backflip 트리거 인식률 향상.
  2. Dance1/2 및 JumpYaw 대기 구간에서 명령 간섭 감소.
  3. 특수동작 완료 감지와 복귀 시퀀스 안정성 개선.

- 수정 파일:
  - `nodes/robots/go1.py`
  - `code_fix_log/code_fix_go1.md`

### [2026-04-12 00:50:00] Go1 Dashboard UI 정리 (Special Motions 1줄 배치 + CAUTION2 안내문 교체)
- 문제 분석:
  - Go1 Dashboard에서 `Special Motions` 패널이 2번째 줄에 있어, 상단 상태 패널들과 분리되어 시인성이 떨어졌음.
  - `CAUTION2`가 의미 없는 플레이스홀더 텍스트로 남아 있었음.

- 조치 방안:
  - `ui/dpg_manager.py`
    - `Special Motions` 패널을 상단 첫 번째 `horizontal group`으로 이동해 Go1 Dashboard 전체를 1줄 배치로 정리.
    - 패널 폭/높이를 재조정해 동일 행에서 레이아웃이 유지되도록 수정.
    - `CAUTION2` 문구를 아래 영어 안내로 교체:
      - `Check current mode and run only after the previous action is complete.`

- 기대 효과:
  1. Go1 Dashboard 주요 정보와 특수동작 버튼을 한 줄에서 확인 가능.
  2. 실행 순서 관련 주의사항이 명확해져 오조작 위험 감소.

- 수정 파일:
  - `ui/dpg_manager.py`
  - `code_fix_log/code_fix_go1.md`

### [2026-04-03] GO1 Server Sender 핵심 발견 사항 및 최적화 포인트
- 문제 분석:
  - Server Sender 구현의 3가지 숨겨진 위험요소 발견:
    1) 최신 프레임 선택이 `os.path.getctime` 기준이라 파일시스템 타이밍 이슈 가능
    2) 업로드 파일명이 고정(`{camera_id}_calib.jpg`)되어 클라이언트 캐시 충돌 가능
    3) 예외 무시로 인해 드롭 원인 은닉
    4) GStreamer 수신과 Video Save가 동일 폴더 쓰기로 경합 위험
    5) 표시 프레임이 의도적으로 1프레임 늦음

- 조치 방안 및 개선점:
  - 파일 선택: 생성시간 외에도 파일명 인덱스 기반 선택 fallback 추가 예정
  - 업로드 파일명: 타임스탬프 또는 시퀀스 번호 포함 권장
  - 예외 처리: 주요 구간 `except Exception: pass`를 `except Exception as e: write_log(...)` 형태로 개선
  - 폴더 분리: GStreamer와 Video Save 폴더를 분리하거나 동기화 메커니즘 필요
  - 캐싱: `_last_frame` 메커니즘 검토 (안정성 vs 지연)

- 근거 코드:
  - 최신 파일 선택: `valid_files.append((os.path.getctime(f), f))`, `_, latest_file = max(valid_files)`
  - 고정 파일명: `filename=f"{camera_id}_calib.jpg"`
  - 예외 무시: 라인 448, 483, 493 등에서 `except Exception: pass`
  - 표시 프레임: `target_file = files[-2]` (최신이 아닌 이전 프레임)

- 수정 파일:
  - `nodes/robots/go1.py`

### [2026-04-XX] Go1 Avoid 그래프 작동 로직 분석 및 정리
- 목적:
  - PyGui에서 Go1 카메라 이미지 서버 송출 + 서버로부터 전달받은 JSON 기반 회피 로직의 전체 흐름을 문서화.
  - 카메라 프레임 수집/저장/업로드 축 + 조종 입력/서버 JSON 수신/자동 회피 축의 두 축 흐름 분석.

- 핵심 노드 역할 정리:
  - `GO1_SERVER_SENDER`: 저장된 프레임을 서버로 업로드하는 비동기 관리 노드
    - 통신: HTTP POST, multipart/form-data, aiohttp 라이브러리
    - 동작: START/STOP 명령 큐 처리 → 카메라별 워커 스레드 생성 → 저장 폴더 감시 → 새 파일 감지 → 비동기 업로드
    - 파일 감시: 폴더 기반 JPG 파일 모니터링, 파일 크기 안정화 확인 후 업로드
    - 상태: `sender_state['status']`, `multi_sender_active` 플래그로 관리
  
  - `GO1_SERVER_JSON_RECV`: 서버 JSON 폴링 및 로봇 명령 변환
    - 통신: HTTP GET 폴링 또는 파일 읽기 모드
    - 동작: 폴링 주기마다 JSON 조회 → 파싱 → 백업 저장 → direction 해석 → 방향 명령 주입
    - Direction 해석: JSON 내 `cmd`, `command`, `direction`, `action` 필드 검사, `left`/`right`/`front`/`back`/`stop` 판정
    - 방향 명령 주입: `_inject_direction_motion()`을 통해 기존 제어 경로 재사용, 펄스형(지속시간 제한) 명령
    - 정지 처리: `stop` 방향은 E-STOP hold 상태(2초) 전환 → 안전 우선 정책
    - 상태 출력: `raw_json`, `seq`, `ts`, `vx`, `vy`, `wz`, `stop`, `confidence`, `connected`, `fresh`, `status`
  
  - `GO1_AUTO_AVOIDANCE`: 서버 JSON의 `detections` 분석 및 자동 회피
    - 입력: JSON 전체 또는 payload, 핵심 판단 기준은 `has_near_obstacle`, `detections`, `risk_level`, `name`, `bbox_xyxy`, `rel_depth`
    - 동작: JSON 파싱 → 중복 실행 방지 → `has_near_obstacle` 체크 → `risk_level==near` 필터링 → `name==person` 우선 → bbox 중심 위치 판정 → 방향 기반 회피 명령 주입
    - 위치 판정: 이미지 너비 464px 기준, `center_x = (x1+x2)/2`, 화면 중심=232.0, `center_x<232`→오른쪽 회피, `center_x>232`→왼쪽 회피
    - 회피 주입: `SERVER_JSON_RECV`의 `_inject_direction_motion()` 재사용, 동일 시그니처는 중복 주입 차단
    - 안전 폴백: bbox가 없거나 계산 실패 시 즉시 정지 명령
    - 상태 출력: `status` (SAFE/NEAR_OBSTACLE_NONE/NEAR_PERSON_MISSING/MOVE_LEFT/MOVE_RIGHT/STOP_SENT), `has_near_obstacle`, `near_count`, `person_found`, `person_id`, `person_rel_depth`

- 흐름의 폐루프 특성:
  - 영상 업로드 (GO1_SERVER_SENDER) → 서버 분석 → JSON 생성 → 수신 (GO1_SERVER_JSON_RECV) → 회피 판단 (GO1_AUTO_AVOIDANCE) → 명령 주입 → 로봇 움직임 → 다음 프레임 촬영

- 그래프 설정값 해석:
  - 카메라 저장: VIDEO_SRC.target_ip=192.168.50.63, receiver_folder=Captured_Images/go1_front, VIS_SAVE.folder=/dev/shm/test11111, VIS_SAVE.duration=10.0, max_frames=300
  - 서버 송신: GO1_SERVER_SENDER.server_url=http://210.110.250.33:5001/upload
  - 조종/수신: GO1_KEYBOARD.keys=WASD, JSON 폴링 주기/타임아웃/신선도 설정

- 수정 파일:
  - `nodes/robots/go1.py`

### [2026-04-27] Depth Anything V2 통합 분석 및 구현 로드맵
- 문제 분석:
  - Go1.py 기반 Depth Anything V2 추가 시 제어 구조/성능/안전성 관점의 통합 지점 정의 필요.
  - 기존 비전 파이프라인(VideoSource → Fisheye → ArUco → Flask → Save)에 Depth 노드를 어떻게 조화시킬지 설계 필요.

- 현재 go1.py 구조:
  - 제어 상태 허브: `go1_node_intent` (주행 의도 입력), `go1_state` (제어 상태 출력), `go1_dashboard` (표시용 집계)
  - 주 제어 루프: go1_keepalive_thread()에서 상시 동작, 그래프 틱은 50Hz (0.02s)
  - 현재 비전 파이프라인: VideoSourceNode → FisheyeUndistortNode → ArUcoDetectNode → FlaskStreamNode, VideoFrameSaveNode

- 추천 통합 방식: VIS_DEPTH_DA2 신규 노드 추가
  - 권장 체인: Video Source → Fisheye Undistort → **Depth Anything V2** → (ArUco/Flask/Save 또는 제어 분기)
  - 이유: 원본/보정 노드 재사용 가능, BaseNode 데이터 포트 기반 철학 유지, 결과 프레임과 수치 동시 제공
  
- 신규 노드 I/O 설계:
  - 입력: `in_frame` (보정 완료 BGR 프레임)
  - 출력: `out_depth_raw` (float32 HxW), `out_depth_vis` (BGR uint8 시각화), `out_risk_json` (위험도 JSON), `out_flow`
  - 상태: `model_variant` (vits/vitb/vitl), `device` (auto/cuda/cpu), `input_size` (기본 518), `roi_mode` (center_bottom), ROI 좌표, 임계값, 연속프레임조건, JSON 저장경로

- 모델 로딩/추론 구조 (50Hz 제어루프 보호):
  - 1회 초기화: 모델 로드
  - Execute 주기: 최신 프레임을 비동기 큐에만 전달 (즉시 반환)
  - 별도 추론 스레드: 마지막 프레임 처리, 결과 캐시 갱신
  - Execute 반환: 캐시된 최신 결과 즉시 전달
  - 효과: 50Hz 그래프 틱 비보호, 추론 지연에도 제어 루프 안정성 유지

- 제어 연결 (단계적 적용):
  - 1단계: go1_state, go1_dashboard에 위험도만 표시, 자동 정지 비활성
  - 2단계: 위험 연속 N프레임 검출 시 go1_node_intent stop 설정, 히스테리시스 적용 (정지/해제 임계값 분리)
  - 안전: 기존 즉시 정지 경로와 충돌 없이 합류 설계

- 출력 데이터 및 파일/네트워크 경로:
  - out_depth_raw: 상대 또는 metric depth, float32 HxW
  - out_depth_vis: BGR uint8, FlaskStreamNode 입력으로 웹 송출 가능
  - out_risk_json: 타임스탐프, ROI, min/p10/유효비율, 위험도, 정지권장, 추론지연 필드
  - 저장: VideoFrameSaveNode를 통해 시각화 프레임 저장
  - 스트리밍: FlaskStreamNode로 웹 송출
  - JSON: ArUco 패턴처럼 depth JSON 확장 가능

- 대시보드 표출 권장: Depth Status (Ready/Running/Disabled), Risk (SAFE/WARN/STOP), ROI Min Depth, 추론 지연, 실효 FPS

- 정확도/안전성 결론:
  - Depth V2의 강점: 상대 깊이 추정
  - 즉시 정지 계층: 절대거리 정밀도보다 일관된 위험 분류와 저지연 중요
  - 초기 목표: 거리 계측보다 근접 위험 감지 신뢰도 확보
  - 운영 권장: 경고 전용 → 로그 임계값 탐색 → 자동 정지 활성화, 연속프레임 조건과 히스테리시스 필수

- 구현 체크리스트:
  1. go1.py에 VIS_DEPTH_DA2 노드 클래스 추가
  2. core/factory.py에 노드 타입 등록
  3. ui/dpg_manager.py에 UI 렌더, 동기화, 팔레트 버튼 추가
  4. 비동기 추론 스레드 및 결과 캐시 설계
  5. depth JSON 스키마 확정 및 로그 저장
  6. 정지 정책 (N프레임, 히스테리시스, fail-safe) 정의
  7. End-to-end latency 계측

- 수정 파일:
  - `nodes/robots/go1.py`
  - `core/factory.py`
  - `ui/dpg_manager.py`

### [2026-04-28] Go1 Auto Avoidance 변경 보고서 및 구현 완료
- 문제 분석:
  - JSON 수신 시 근접 인물에 대해 즉시 정지 대신 위치 기반 횡회피 명령을 0.5초간 주입하도록 변경 필요.

- 조치 방안:
  - `nodes/robots/go1.py` Auto Avoidance 노드 로직 수정:
    - 입력: JSON payload의 `detections` 배열에서 `risk_level=='near'` 이며 `name=='person'` 항목만 처리
    - Bbox 중심 계산: `center_x = (x1 + x2) / 2.0` (이미지 너비 = 464px)
    - 위치 분류: `center_x == 232.0` (정확히 중앙)일 때만 `center`, `center_x < 232.0`→`left`, `center_x > 232.0`→`right`
    - 회피 매핑: `left` 또는 `center` → 오른쪽으로 0.5s 이동, `right` → 왼쪽으로 0.5s 이동
    - 명령 주입: `GO1_SERVER_JSON_RECV`의 `_inject_direction_motion(dir, speed, duration, signature)` 호출, 실패 시 안전 정지 폴백
  - `core/engine.py`: `GO1_AUTO_AVOIDANCE`를 주기 실행 목록에서 제거 (중복 실행 방지)

- 샘플 JSON 분석 및 검증:
  - 파일: `jsonbackup/1777363709_8966951_go1_front.json`
  - 관심 대상: id=5 (person, risk_level=near)
  - Bbox: [387, 152, 406, 222] → center_x = (387+406)/2 = 396.5
  - 상대 위치: 396.5 / 464 ≈ 0.8545 → `right` 판정
  - 결론: 로봇은 왼쪽으로 0.5초간 회피 명령 수신

- 예상 로그:
  - [GO1 AUTO AVOID] near person detected | id=5 | rel_depth=0.981680154800415
  - [GO1 AUTO AVOID] 왼쪽으로 이동 (id=5 | rel_depth=0.981680154800415)
  - [GO1 JSON RX] command=left → move vx=0.000, vy=0.200, wz=0.000, duration=0.50s

- 주의사항 및 권장:
  - 현재 `center` 판정이 매우 엄격함(정확히 중앙일 때만) → 실환경 대응을 위해 임계값(±2px 등) 완화 권장
  - 동일 이벤트 중복 실행 완벽 방지를 위해 노드 내부 디바운스 병행 권장

- 재현 방법:
  1. 엔진과 노드 실행, `GO1_SERVER_JSON_RECV`가 해당 JSON을 읽도록 배치
  2. 로그에서 예상 로그 라인들 확인

- 수정 파일:
  - `nodes/robots/go1.py`
  - `core/engine.py`

### [2026-05-04 00:00:00] Server Sender 가이드 및 통합 정리
- 목적:
  - Go1 Server Sender의 전체 동작 원리, 아키텍처, 노드 간 관계를 한 문서에서 종합적으로 가이드.
  - 이미지 업로드 → 서버 처리 → JSON 수신 → 로봇 제어의 폐루프 흐름을 시각화.

- 핵심 가이드 내용:
  - **Server Sender 노드 개요**: GO1_SERVER_SENDER, 원격 서버 URL 설정, START/STOP 제어, 비동기 multipart 업로드, Flow 포트 지원
  
  - **작동 원리 - 4개 핵심 구성 요소**:
    1) MultiSenderNode (사용자 UI): Action(Start/Stop), Server URL 입력 → `sender_command_queue` 큐잉
    2) sender_manager_thread(): 큐에서 START/STOP 명령 추출 → `multi_sender_active` 플래그 → 카메라별 워커 스레드 생성/정리
    3) camera_async_worker(): 저장 폴더 모니터링 → 최신 JPG 파일 감지 → 중복 업로드 방지 → 비동기 HTTP multipart 전송 (10Hz)
    4) send_image_async(): 이미지 파일 읽기 → form-data 구성 → asyncio 기반 POST 요청 (타임아웃 2초)

  - **아키텍처 다이어그램**: UI Layer → Background Thread Layer (sender_manager_thread) → 카메라별 AsyncIO Worker → HTTP Streaming Server
  
  - **카메라 노드와의 연결**: VIDEO_SRC (스트리밍 시작/중지) → VIS_SAVE (폴더 저장) → GO1_SERVER_SENDER (업로드 워커 관리)
  
  - **실행 흐름 (START)**:
    1. 사용자가 "Start Sender" 선택
    2. sender_command_queue에 ('START', url) 추가
    3. sender_manager_thread()가 START 명령 처리
    4. multi_sender_active = True, 카메라별 워커 스레드 생성
    5. start_async_loop()로 asyncio 이벤트루프 생성
    6. camera_async_worker() 루프 시작 (폴더 감시 + 파일 업로드)
  
  - **실행 흐름 (STOP)**:
    1. 사용자가 "Stop Sender" 선택
    2. sender_command_queue에 ('STOP', url) 추가
    3. sender_manager_thread()가 STOP 명령 처리
    4. multi_sender_active = False → 모든 워커 루프 종료
  
  - **사용 예시 (카메라 프레임 저장 및 업로드)**:
    - 파이프라인: START → VIDEO_SRC → FISHEYE(선택) → ARUCO(선택) → VIS_SAVE → GO1_SERVER_SENDER → 후속노드
    - 설정: 저장 폴더(폴더명), 타이머(초), Start Stream 버튼 → 서버 URL 입력 → Start/Stop Sender 선택
    - 검증: 로그에서 upload 성공/실패 확인, 서버에서 업로드된 파일 확인

- 개선 사항 통합:
  - 저장-송신 파이프라인을 Go1 기준으로 한 번에 추적 가능
  - 경로 불일치/상태 불일치 원인을 빠르게 확인 가능
  - VIS_SAVE 경로 우선 동기화로 폴더 분리 관리
  - Start 재트리거 보강으로 재실행 시 송신 누락 방지

- 수정 파일:
  - `code_fix_log/SERVER_SENDER_GUIDE.md`
  - `code_fix_log/code_fix_go1.md`

### [2026-05-05 22:05:00] Go1 모듈 선택 동적 로드 시스템 구현 (go1_team1.py, go1_team2.py 등 다중 변형 지원)
- 문제 분석:
  - 기존에는 `go1.py` 파일 이름이 하드코딩되어, `go1_team1.py`, `go1_team2.py` 같은 다양한 변형 모듈을 사용하려면 모든 import 문을 수동으로 수정해야 했음.
  - 여러 팀이 협업하거나 여러 Go1 구현 버전을 테스트할 때 비효율적이었으며, 실수로 인한 import 오류 위험이 높았음.
  - `main.py`, `core/factory.py`, `ui/dpg_manager.py` 3개 파일에 분산된 go1 import로 인해 변경 시 누락되기 쉬웠음.
- 조치 방안:
  - 중앙 설정 파일 `core/config.py`를 신규로 생성하여 `GO1_MODULE_NAME` 전역 설정 변수를 관리함.
  - `main.py`에 `select_go1_module()` 함수를 추가하여 프로그램 시작 시 `nodes/robots/` 폴더의 `go1*.py` 파일 목록을 스캔하고, 사용자에게 선택 프롬프트를 표시함.
  - 사용자 선택 후 `config.GO1_MODULE_NAME` 값을 업데이트하고 `core/config.py` 파일에 저장하여 다른 모듈들이 참조할 수 있도록 함.
  - `import_go1_modules()` 함수를 추가하여 `importlib`로 선택된 모듈을 동적으로 import하고, 필요한 함수(`go1_keepalive_thread`, `init_go1_connection`)를 `getattr()`로 추출함.
  - `core/factory.py`의 Go1 클래스 import를 동적으로 변경:
    - `GO1_MODULE_NAME`을 읽고 `importlib.import_module()`로 모듈을 로드
    - 12개의 Go1 노드 클래스(`Go1RobotDriver`, `Go1ActionNode`, 등)를 `getattr()`로 추출
    - Import 실패 시 `HAS_GO1 = False`로 graceful handling
  - `ui/dpg_manager.py`의 Go1 UI 변수 import를 동적으로 변경:
    - `GO1_MODULE_NAME`을 참조하여 `go1_dashboard`, `go1_target_vel`, `go1_state` 등을 동적으로 로드
    - Try/Except로 실패 시 UI가 비활성화되도록 처리
- 수정 및 추가 파일:
  - `core/config.py` (신규): 중앙 Go1 모듈 설정 파일
  - `main.py` (수정): `select_go1_module()`, `import_go1_modules()` 함수 추가 및 실행 순서 조정
  - `core/factory.py` (수정): Go1 클래스를 동적 import로 변경
  - `ui/dpg_manager.py` (수정): Go1 변수를 동적 import로 변경

  ### [2026-05-07 00:00:00] Go1 Auto Avoidance 정책표 기반 확장 및 로그 강화
- 문제 분석:
  - 기존 `Go1AutoAvoidanceNode`는 `person`만 대상으로 회피/정지 로직을 수행하고 있어, `avoidance_prompt.md`에 정의된 그룹 정책을 반영하지 못했음.
  - 탐지 후 어떤 `name`, `group`의 객체가 잡혔는지와 그에 따라 어떤 행동을 했는지 로그에서 바로 확인하기 어려웠음.
  - `go1_auto_avoidance_data`에 남아 있던 `person_*` 상태는 정책 기반 로직 전환 후에도 잔재로 남아 있었음.
- 조치 방안:
  - `nodes/robots/go1.py`의 Auto Avoidance 로직을 그룹 정책표 기반으로 전환함.
    - `LABELED_CLASSES` / `ZEROSHOT_EXTENDED_CLASSES` 대응 그룹을 내부 정책 테이블로 정규화.
    - `AGENT`, `MOVABLE_OBSTACLE`, `UNKNOWN_OBSTACLE`는 4초 정지.
    - `HARD_OBSTACLE`는 기존 person 회피 방식과 동일한 bbox 중심 기반 좌/우 반대 회피.
    - `ELECTRICAL_RISK`, `TANGLED_RISK`, `SPECIAL_OBJECT`는 2초 정지.
    - `SMALL_OBSTACLE`는 인식만 하고 동작 없이 통과.
  - `go1_auto_avoidance_data`에서 `person_found`, `person_id`, `person_rel_depth`를 제거하고, `target_*` 상태만 유지하도록 정리함.
  - 로그를 강화하여 새 입력마다 아래 정보를 확인할 수 있게 함.
    - 탐지 요약: `name[group]@depth`
    - 정책 행동: `action=stop` / `action=avoid` / `action=observe`
    - 결과 행동: `hold=4.0s`, `escape=왼쪽/오른쪽`, `result=move`, `result=pass-through`
- 수정 파일:
  - `nodes/robots/go1.py`

  ### [2026-05-12 13:30:00] Go1 카메라 송출 안정화 및 /dev/shm 초기화
- 문제 분석:
  - 서버 이미지 송출 시 이전 실행에서 남은 마지막 이미지가 간헐적으로 계속 업로드되는 현상이 있었음.
  - `/dev/shm`(RAM disk) 경로를 재사용하는 경우, 이전 세션의 파일이 남아 있으면 새 세션이 시작되기 전에 오래된 프레임을 집을 수 있었음.
  - 카메라 시작 과정이 SSH 원격 종료, 원격 스크립트 실행, 로컬 receiver 실행, 업로드 시작으로 이어지는데, 이 흐름에 대한 로그가 부족해 문제 지점을 바로 추적하기 어려웠음.
  - 업로드 파일명이 고정되어 있어 서버 저장 측에서 같은 이름으로 덮어쓰는 가능성도 있었음.
- 조치 방안:
  - `nodes/robots/go1.py`와 `nodes/robots/go1_quiz.py`에 동일한 카메라 송출 안정화 로직을 추가함.
    - `/dev/shm` 경로를 감지하는 `_is_under_dev_shm()` 추가.
    - 시작 시 해당 폴더를 `_reset_output_folder()`로 삭제 후 재생성하도록 변경.
    - SSH `kill_camera.sh` / `go1_send_both.sh` 실행 결과를 `_append_process_logs()`로 stdout/stderr까지 기록하도록 강화.
    - `camera_async_worker()`에 `start_after_epoch`를 추가해 `/dev/shm` 사용 시 업로드 시작을 warmup 시간만큼 지연.
    - 파일 선택 로직을 `best_idx` + `mtime` 기준으로 보강해 이전 파일 재선택 가능성을 줄임.
    - `send_image_async()`의 multipart 업로드 파일명을 `camera_id + timestamp + source filename` 형태로 변경해 서버 덮어쓰기 가능성을 낮춤.
  - 송출 시작 로그를 다음 순서로 남기도록 정리함.
    - `remote stop -> remote launch -> local receiver -> upload warmup`
    - `/dev/shm detected -> resetting folder`
    - `upload warmup enabled for 2.0s`
  - warmup 조건은 `/dev/shm` 경로일 때만 적용해, 일반 저장 경로의 동작은 불필요하게 바꾸지 않도록 했음.
- 수정 파일:
  - `nodes/robots/go1.py`
  - `nodes/robots/go1_quiz.py`

### [2026-05-12 22:10:00] Go1 이미지 송출 타이밍 동기화 (카메라-서버 연결 레이싱 컨디션 해결)
- **문제 분석**:
  - 카메라 라즈베리파이 연결 → 서버 연결 → 이미지 송출 → 카메라 이미지 갱신 순서로 진행되어야 하나, 실제로는 서버 연결이 카메라 연결보다 빨라서 레이싱 컨디션 발생
  - `init_go1_connection()` 함수에서 `camera_worker_thread`와 `sender_manager_thread`가 거의 동시에 시작됨
  - camera worker에서 SSH 원격 명령(~50-100ms) → gst receiver 시작(~100ms) → 첫 프레임 도착(~150-200ms) 소요
  - 이 사이에 sender가 폴더 모니터링을 시작하면 기존 파일(이전 이미지)을 최신 파일로 착각해 서버로 송출
  - `camera_async_worker()`의 파일 선택 로직이 폴더의 가장 높은 인덱스 파일을 선택하기 때문에 이전 파일이 먼저 송출되는 문제 발생

- **해결 방안 (방안 1: Camera 완료 신호 추가)**:
  - `camera_state` 딕셔너리에 `'first_frame_ready': False` 플래그 추가
  - `camera_worker_thread`에서 gst receiver 시작 후, 폴더에 실제 파일이 생성될 때까지 모니터링
    - 폴더 모니터링 최대 5초 대기
    - 첫 파일 생성되면 `camera_state['first_frame_ready'] = True` 설정
    - timeout 시 경고 로그 출력 후 계속 진행
  - `sender_manager_thread`에서 START 명령 처리 시, 시작 전 카메라의 첫 프레임 준비 신호 대기
    - 최대 10초 대기
    - timeout 시 경고 로그 출력 후 계속 진행 (안전장치)
  - STOP 명령 처리 시 플래그를 False로 리셋

- **구현 상세**:
  1. camera_state 초기화 (line ~358):
     ```python
     camera_state = {
         'status': 'Stopped',
         'target_ip': '',
         'duration': 0.0,
         'start_time': 0.0,
         'timer_started_logged': False,
         'last_interval_count': 0,
         'first_frame_ready': False,  # ← 추가
     }
     ```
  
  2. camera_worker_thread의 START_CMD 처리 시 receiver 시작 후 (line ~839):
     ```python
     # Monitor folder for first frame arrival
     camera_state['first_frame_ready'] = False
     write_log("[Cam START] Waiting for first frame...")
     monitor_start = time.time()
     while time.time() - monitor_start < 5.0:  # Max 5 seconds
         try:
             files = glob.glob(os.path.join(target_folder, "*.jpg"))
             if files:
                 write_log("[Cam START] First frame detected, ready for server sender")
                 camera_state['first_frame_ready'] = True
                 break
         except Exception:
             pass
         time.sleep(0.1)
     if not camera_state['first_frame_ready']:
         write_log("[Cam START] Warning: No frames detected within 5.0s, proceeding anyway")
         camera_state['first_frame_ready'] = True
     ```
  
  3. STOP 명령 처리 시 (line ~856):
     ```python
     camera_state['first_frame_ready'] = False
     ```
  
  4. sender_manager_thread의 START 명령 처리 시 (line ~1047):
     ```python
     # Wait for first frame from camera before starting sender
     camera_state['first_frame_ready'] = False
     write_log("[Server Sender] Waiting for camera first frame...")
     wait_start = time.time()
     while time.time() - wait_start < 10.0:  # Max 10 seconds
         if camera_state.get('first_frame_ready', False):
             break
         time.sleep(0.1)
     if not camera_state.get('first_frame_ready', False):
         write_log("[Server Sender] Warning: Camera not ready, proceeding anyway")
     ```

- **동작 흐름 개선**:
  | 단계 | 이전 | 현재 |
  |------|-----|------|
  | Camera receiver 시작 | → 즉시 준비 상태 | → 첫 파일 감지할 때까지 대기 |
  | Server 연결 시작 | → 폴더의 이전 파일 송출 ❌ | → 카메라 준비 신호 대기 ✅ |
  | 실제 이미지 송출 | → 이전 이미지 먼저 | → 새로운 이미지만 송출 |

- **효과**:
  - 서버 연결이 아무리 빨라도 카메라의 첫 프레임 도착까지 대기
  - 이전 이미지(stale frame)가 서버에 도달하지 않음
  - timeout 로직으로 카메라 연결 실패 시에도 안전하게 진행 가능

- **수정 파일** (동일한 변경 적용):
  - `nodes/robots/go1.py`
  - `nodes/robots/go1_quiz.py`
  - `nodes/robots/go1_answer.py`

### [추가] Go1 - YAML로 분리된 하드코딩 변수 목록
- `nodes/go1_config/auto_avoidance_config.yaml`:
  - `class_to_group` (객체 클래스 → 그룹 매핑), `policy` (그룹별 행동: stop/avoid 등), `group_priority`, `image`(width/height), `escape`(left_x/right_x), `bbox`(min_width/min_height), `motion`(move_speed/move_duration_sec)
- `nodes/go1_config/camera_config.yaml`:
  - `camera_nanos`, `camera_config`(folder/id), `camera_save_state_defaults`, `gstreamer`(udp_port/ssh_key_path), `timing`(first_frame_wait_sec, upload_warmup_sec, sender_camera_wait_sec, proc_kill_timeout_sec)
- `nodes/go1_config/model_config.yaml`:
  - `da2_models`(모델별 encoder/features/out_channels), `runtime`(target_fps/interval), `aruco`(enabled/marker_size)
- `nodes/go1_config/network_config.yaml`:
  - 네트워크/포트/URL: `highlevel`, `local_port`, `go1_ip`, `go1_port`, `go1_unity_ip`, `unity_*_port`들, `go1_ap_ip`, `aruco_udp_port`, `server_upload_url`, `json_cmd_url`
- `nodes/go1_config/robot_control.yaml`:
  - 제어 파라미터: `dt`, `v_max`, `s_max`, `w_max`, `vx_cmd`, `vy_cmd`, `wz_cmd`, `body_height`(min/max/key_step), `timing`(hold_timeout_sec 등), `yaw_control`(unity_yaw_offset_deg 등), `estop`(hold_sec), `foot_raise_height`
- `nodes/go1_config/special_actions.yaml`:
  - `special_actions`(동작별 mode/trigger_sec/wait_timeout/recovery), `phase_timing`(prep_stand_sec 등)