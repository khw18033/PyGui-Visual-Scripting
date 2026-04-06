### [2026-03-18 11:25:00] RoboMaster EP 로봇 시스템 모듈화 및 대시보드 이식
- 문제 분석:
  - 기존 코드에서 RoboMaster EP의 상태(배터리, 좌표) 수신 및 명령(UDP 명세) 처리 로직이 메인 파일에 종속되어 있었음.
  - 다양한 로봇이 동시에 운용되는 통합 시뮬레이터 환경 특성상, EP 제어 로직 역시 SRP를 준수하는 모듈로 분리할 필요가 큼.
- 조치 방안:
  - `nodes/robots/ep01.py`를 신설하여 EP의 TCP/UDP 통신을 순수 백엔드 스레드로 분리하고, 상태 폴링 루프(`ep_status_thread`)를 구축함.
  - `EPRobotDriver` 및 `EPActionNode` 클래스를 제작하여 시각적 스크립팅 노드로써 엔진과 호환되게 함.
  - `ui/dpg_manager.py`에 EP 전용 Dashboard 탭을 추가하고 수동 이동(Vx, Vy, Vz), LED 제어, 그립 액션 버튼을 통합 구성함.
  - 영상 제어는 EP 전용 노드를 억지로 만들지 않고, 기존에 재구성한 `VIDEO_SRC` 노드에 EP의 영상 프로토콜(`tcp://ip:11111`)을 그대로 기입하여 호환되도록 결합도를 낮춤.

### [2026-04-06 00:00:00] EP01 기능 동기화 (STA/AP 연결, EP 키보드, 대시보드 원본 일치)
- 문제 분석:
  - 현재 모듈형 코드의 EP 파트는 `visual_scripting_Int_v11.py` 대비 연결 방식이 단순(UDP 기반)하고, 대시보드 구성/표시 항목/버튼이 상이했음.
  - 특히 사용자 요구 핵심인 STA/AP 선택 접속 버튼과 SDK 기반 연결 흐름(연결 상태, SN, 텔레메트리 구독)이 누락되어 있었음.
- 조치 방안:
  - `nodes/robots/ep01.py`에 RoboMaster SDK 기반 연결 엔진을 이식:
    - `connect_ep_thread_func(conn_mode)` 추가 (STA/AP 선택 연결)
    - `btn_connect_ep_sta`, `btn_connect_ep_ap` 콜백 추가
    - 텔레메트리 콜백(`ep_sub_pos`, `ep_sub_vel`, `ep_sub_bat`, `ep_sub_imu`) 및 `ep_state` 확장
    - `ep_comm_thread`를 원본 로직에 맞춰 유지/정지 시퀀스 포함 형태로 정비
  - EP 제어 입력을 `ep_node_intent(vx, vy, wz)` 중심으로 통일하고, 기존 `ep_target_vel(vz)`와 호환되게 동기화 처리.
  - EP 노드 기능 확장:
    - `EPKeyboardNode` 추가 (`EP_KEYBOARD`)
    - `EPRobotDriver`를 원본 의도 기반 입력 처리로 변경
    - `EPActionNode`를 SDK 우선 + UDP fallback 전송 구조로 보강
  - 연동 파일 반영:
    - `core/factory.py`: `EP_KEYBOARD` 노드 생성 분기 추가
    - `core/engine.py`: `EP_KEYBOARD` 주기 실행 대상 추가
    - `ui/dpg_manager.py`: EP Dashboard를 원본 구성(상태 + Conn STA/AP + Odometry + Commands)으로 재구성, EP 상태 갱신 항목(SN/좌표/속도/가속도/명령값) 반영, EP KEY 버튼 추가