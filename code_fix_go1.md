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