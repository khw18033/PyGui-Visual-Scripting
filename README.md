# PyGui Visual Scripting

MT4 로봇암, Unitree Go1, RoboMaster EP를 하나의 노드 기반 GUI에서 제어하는 Python 비주얼 스크립팅 프로젝트입니다.

코드는 DearPyGui 기반 노드 에디터 + 실시간 대시보드 + 백그라운드 통신 스레드 구조로 구성되어 있습니다.

## 주요 기능

- 노드 그래프 실행 엔진
: START 기준 Flow 실행 + Data 핀 연결 기반 계산
- 다기종 로봇 통합
: MT4(Serial), Go1(Unitree SDK/UDP), EP(RoboMaster SDK/UDP) 동시 지원
- 실시간 대시보드
: MT4/Go1/EP 상태, 좌표, 배터리, 지연시간, 네트워크 표시
- Unity 연동
: MT4/Go1 UDP/JSON 기반 명령 수신 및 상태 피드백 송신
- 영상 파이프라인
: Go1 카메라 수집, 왜곡 보정, ArUco 검출, Flask 스트리밍, 프레임 저장
- 이미지 저장/전송
: Go1/EP 저장 폴더 기반 프레임 보관 및 HTTP 업로드 송신 노드 지원
- 그래프 저장/로드
: 노드 타입, 위치, 설정, 링크를 JSON으로 직렬화

## 프로젝트 구조

```text
main.py
core/
	engine.py         # 노드/링크 레지스트리, 단일 틱 실행기
	factory.py        # 노드 타입 문자열 -> 실제 노드 생성
	input_manager.py  # 키 입력/포커스 상태 싱글톤
	serializer.py     # 그래프 저장/로드
nodes/
	common.py         # START, IF, LOOP, CONSTANT, PRINT, LOGGER
	robots/
		mt4.py          # MT4 드라이버/액션/키보드/유니티/보정 노드
		go1.py          # Go1 드라이버/액션/키보드/유니티/비전 노드
		ep01.py         # EP 드라이버/액션/키보드/카메라/저장/업로드 노드
ui/
	dpg_manager.py    # DearPyGui 화면, 노드 렌더링, 실행 루프
```

## 실행 흐름

1. `main.py`에서 로봇 통신 초기화 및 백그라운드 스레드 시작
2. `start_gui()`로 DPG 메인 루프 진입
3. `RUN SCRIPT` 버튼 활성화 시 주기적으로
	 - UI 값 -> 노드 state 동기화
	 - `execute_graph_once()` 실행
	 - 노드 output_data 갱신 및 다음 Flow 결정

## 지원 노드 목록 (현재 코드 기준)

### Common

- `START`
- `COND_KEY`
- `LOGIC_IF`
- `LOGIC_LOOP`
- `CONSTANT`
- `PRINT`
- `LOGGER`

### MT4

- `MT4_DRIVER`
- `MT4_ACTION`
- `MT4_KEYBOARD`
- `MT4_UNITY`
- `UDP_RECV`
- `MT4_SAG`
- `MT4_CALIB`
- `MT4_TOOLTIP`
- `MT4_BACKLASH`

### Go1 / Vision

- `GO1_DRIVER`
- `GO1_ACTION`
- `GO1_KEYBOARD`
- `GO1_UNITY`
- `VIDEO_SRC`
- `VIS_FISHEYE`
- `VIS_ARUCO`
- `VIS_FLASK`
- `VIS_SAVE`
- `GO1_SERVER_SENDER`

### EP

- `EP_DRIVER`
- `EP_KEYBOARD`
- `EP_ACTION`
- `EP_CAM_SRC`
- `EP_CAM_STREAM`

## 노드 입출력 요약

### Common

- START
: 출력 `Flow`
- COND_KEY
: 출력 `Is Pressed?(Data)`
- LOGIC_IF
: 입력 `Condition(Data)` / 출력 `True(Flow)`, `False(Flow)`
- LOGIC_LOOP
: 출력 `Loop Body(Flow)`, `Finished(Flow)`
- CONSTANT
: 출력 `Data`
- PRINT
: 입력 `Data` / 출력 `Flow`

### MT4

- MT4_DRIVER
: 입력 `x,y,z,roll,gripper` + 설정 `smooth,grip_spd,roll_spd` / 출력 `Flow`
- MT4_ACTION
: 입력 `v1,v2,v3(Data)` / 출력 `Flow`
- MT4_KEYBOARD
: 출력 `Target X,Y,Z,Roll,Grip(Data)` + `Flow`
- MT4_UNITY
: 입력 `JSON(Data)` / 출력 `Target X,Y,Z,Roll,Grip(Data)` + `Flow`
- UDP_RECV
: 출력 `JSON(Data)` + `Flow`
- MT4_SAG, MT4_CALIB, MT4_TOOLTIP, MT4_BACKLASH
: 보정용 Data 입출력 노드

### Go1 / Vision

- GO1_DRIVER
: 입력 `vx,vy,vyaw,body_height(Data)` / 출력 `Flow`
- GO1_ACTION
: 입력 `Mode + Speed/Val(Data)` / 출력 `Flow`
: `Stand`, `Reset Yaw0`, `Walk Fwd/Back`, `Walk Strafe`, `Turn`, `Sit Down`, `Stand Tall`, `Set Body Height`, `Jump Yaw`, `Straight Hand`, `Dance 1`, `Dance 2` 지원
- GO1_KEYBOARD
: 출력 `vx,vy,vyaw,body_height(Data)` + `Flow`
- GO1_UNITY
: 입력 `JSON(Data)` / 출력 `vx,vy,vyaw,body_height,active(Data)` + `Flow`
- VIDEO_SRC
: 출력 `Frame(Data)`
- VIS_FISHEYE
: 입력 `Frame` / 출력 `Frame`
- VIS_ARUCO
: 입력 `Frame` / 출력 `Draw Frame`, `Marker Info`
- VIS_FLASK
: 입력 `Frame` (Flask `/video_feed` 송출)
- VIS_SAVE
: 입력 `Flow`, `Frame` / 출력 `Flow`
- GO1_SERVER_SENDER
: 입력 `Flow` / 출력 `Flow`

### EP

- EP_DRIVER
: 입력 `vx,vy,wz,arm_dx,arm_dy,grip_open,grip_close(Data)` / 출력 `Flow`
- EP_KEYBOARD
: 출력 `vx,vy,wz,arm_dx,arm_dy,grip_open,grip_close(Data)` + `Flow`
- EP_ACTION
: 입력 `Flow` / 출력 `Flow`
: `LED Red`, `LED Blue`, `Blaster Fire`, `Arm Center`, `Grip Open`, `Grip Close` 지원
- EP_CAM_SRC
: 입력 `Flow` / 출력 `Frame(Data)`, `Flow`
- EP_CAM_STREAM
: 입력 `Flow`, `Frame` / 출력 `Flow`
- `EP_VIS_SAVE`
: 입력 `Flow`, `Frame` / 출력 `Flow`
- `EP_SERVER_SENDER`
: 입력 `Flow` / 출력 `Flow`

## 추천 그래프 예제

### 1) MT4 Unity 원격 제어

- 구성
: `START -> UDP_RECV -> MT4_UNITY -> MT4_DRIVER`
- 효과
: Unity JSON 명령을 MT4 좌표계로 변환해 실시간 반영

### 2) Go1 키보드 주행

- 구성
: `START -> GO1_KEYBOARD -> GO1_DRIVER`
- 효과
: WASD/방향키, Q/E, Z/X, Space, R, C를 Go1 제어 의도로 변환

### 3) Go1 비전 파이프라인

- 구성
: `VIDEO_SRC -> VIS_FISHEYE -> VIS_ARUCO -> VIS_FLASK`
- 확장
: 저장이 필요하면 `VIS_SAVE`, 원격 업로드는 `GO1_SERVER_SENDER` 추가

### 4) Go1 특수 동작 제어

- 구성
: `START -> GO1_ACTION -> GO1_DRIVER`
- 효과
: 정지, 자세 전환, 높이 조절, 백플립, 점프, 댄스 같은 특수 동작을 실행

### 5) EP 카메라 스트림

- 구성
: `START -> EP_CAM_SRC -> EP_CAM_STREAM`
- 효과
: EP 카메라를 Flask 엔드포인트(`/ep_video_feed`)로 송출

### 6) EP 카메라 저장/업로드

- 구성
: `START -> EP_CAM_SRC -> EP_VIS_SAVE -> EP_SERVER_SENDER`
- 효과
: EP 프레임을 로컬 폴더에 저장하고, 저장 폴더의 이미지를 원격 서버로 업로드

## 프로젝트 구조상 장점

- 역할 분리(Separation of Concerns)
: `core`(실행/직렬화), `nodes`(기능), `ui`(렌더/입력)로 분리되어 디버깅과 변경 영향 범위가 작음
- 다형성 기반 확장성
: `BaseRobotDriver` + `UniversalRobotNode` 구조로 새 로봇 추가 시 드라이버 스키마만 맞추면 UI 핀과 실행 경로를 재사용 가능
- 비동기/백그라운드 안정성
: 통신/카메라/송신 작업을 스레드로 분리해 GUI 프레임 드랍을 줄이고 제어 루프 응답성을 유지
- 선택 의존성에 대한 강건성
: OpenCV/Flask/aiohttp/SDK 미설치 시에도 핵심 에디터와 기본 노드 실행이 가능해 개발 단계별 점진적 적용이 쉬움
- 저장 포맷 호환성 고려
: 링크를 인덱스 + 이름 메타데이터로 함께 저장하고, 로드시 fallback/보정 로직을 적용해 버전 변화에 대응
- 노드 팩토리 중심 등록 구조
: 타입 문자열 기반 `NodeFactory`로 노드 생성 경로가 단일화되어 기능 추가/유지보수가 일관됨

## 환경 및 의존성

## Python

- Python 3.8 이상 권장

## 필수 패키지

- `dearpygui`
- `pyserial`

## 기능별 선택 패키지

- `opencv-python` 또는 `opencv-contrib-python` (비전 노드)
- `numpy` (캘리브레이션, 영상 처리)
- `flask` (영상 스트림 서버 노드)
- `aiohttp` (Go1 서버 전송 노드)

## 외부 SDK (선택)

- Unitree SDK (`robot_interface`)
	- 위치: `unitree_legged_sdk/lib/python/<arm64|amd64>`를 경로에 포함
	- 미설치 시 Go1은 시뮬레이션 상태로 동작
- RoboMaster SDK (`robomaster`)
	- 미설치 시 EP는 시뮬레이션/제한 모드

## 빠른 시작

```bash
git clone https://github.com/khw18033/PyGui-Visual-Scripting.git
cd PyGui-Visual-Scripting

pip install dearpygui pyserial numpy opencv-contrib-python flask aiohttp
python main.py
```

실행 후 GUI에서 노드를 추가하고 연결한 뒤, `RUN SCRIPT`로 그래프를 실행합니다.

## 저장/로드

- 저장 위치: `Node_File_MT4/*.json`
- 저장 항목
	- 노드 타입/ID/위치
	- 노드 settings(state)
	- 링크 정보(인덱스 + 포트 이름 기반 메타데이터)
- 로드시 타입 보정
	- 과거 저장 파일의 특정 `GO1_DRIVER` 오저장 케이스를 자동 보정

## 디렉터리 자동 생성

실행 중 아래 폴더가 자동 생성될 수 있습니다.

- `Node_File_MT4` : 그래프 저장 파일
- `path_record` : MT4 경로 녹화 CSV
- `result_log` : MT4 동작 로그 CSV
- `Captured_Images/go1_front` : Go1 프레임 저장 기본 경로
- `Captured_Images/go1_saved` : Go1 저장 프레임 기본 경로
- `Captured_Images/ep01_saved` : EP 저장 프레임 기본 경로

## 주의 사항

- MT4 기본 시리얼 포트는 코드상 `/dev/ttyUSB0` 기준입니다.
- Go1 초기화 시 콘솔에서 IP 입력을 받습니다.
- 일부 네트워크 정보 표시는 Linux `ip` 명령 출력 형식에 맞춰져 있습니다.
- 하드웨어/SDK가 없어도 GUI와 기본 노드 로직은 실행됩니다.

## 라이선스

저장소 라이선스 정책을 따릅니다.