# Multi-Robot Integrated Visual Scripting Framework (PyGui Int-v1)

> **"From Single Arm Control to Multi-Robot Collaboration Framework"**
> 객체 지향 설계(OOP)의 다형성(Polymorphism)을 기반으로, 서로 다른 이질적인 로봇(MT4 로봇 팔, Go1 4족 보행 로봇)들을 단일 환경에서 시각적으로 제어하고 연동할 수 있는 하이브리드 비주얼 스크립팅 도구입니다.

---

## 🚀 Project Overview (개요)

본 프로젝트는 초기에 **[MT4 Robot Arm]** 전용 제어 및 모니터링 패널로 시작되었으나, 최신 **Integrated v1 (v23 아키텍처)** 업데이트를 통해 **다기종 로봇 통합 제어 프레임워크**로 완벽하게 고도화되었습니다.

이제 사용자는 복잡한 파이썬 코드를 작성할 필요 없이, 노드(Node) 연결만으로 로봇의 행동 로직을 설계하고 **"MT4가 물건을 들어 올린 후, Go1이 해당 위치로 이동하는"** 복합적인 로봇 협업 시나리오를 손쉽게 구현할 수 있습니다.

### ✨ Key Features
* **Polymorphic Node Architecture (다형성 설계):** `BaseRobotDriver`를 상속받는 하드웨어 클래스(MT4, Go1)를 `UniversalRobotNode`(만능 노드)에 주입(Injection)하여, 로봇 기종에 따라 UI 핀(Pin)이 동적으로 생성되는 개방-폐쇄 원칙(OCP) 달성.
* **Independent Threading (독립적 비동기 통신):** MT4의 Serial 통신과 Go1의 UDP/SDK 및 고용량 카메라 스트리밍, AI 서버 비동기 전송(`asyncio`)을 독립된 백그라운드 스레드로 격리하여 GUI 프레임 드랍(Lag) 완벽 방지.
* **Digital Twin Sync:** Unity 3D 환경과의 실시간 양방향 동기화 및 원격 텔레프레즌스(Telepresence) 제어 지원.
* **Hybrid Execution Engine:** 순차적인 데이터 흐름(Data Flow)과 조건에 따른 실행 분기(If/Loop)를 동시 지원.

---

## 💻 Hardware & Environment

이 프레임워크는 크로스 플랫폼을 지원하며, 로봇의 통신 방식에 따라 분산 운영이 가능합니다.

* **Raspberry Pi 5 (Linux/Ubuntu):** MT4 로봇 팔과의 직접적인 USB Serial 통신 및 제어 권장.
* **PC Laptop (Ubuntu 22.04 LTS):** Unitree SDK 구동 및 Go1 4족 보행 로봇과의 네트워크(UDP) 통신, 고해상도 카메라 스트리밍 수신 및 AI 연산 서버 연동 권장.
* **Unity 3D PC (Windows/Mac):** 디지털 트윈 시각화 및 피드백 수신용.

---

## 🧩 Node Dictionary (지원 노드 목록)

### 1. Common Nodes (논리 및 도구)
| Node Name | Type Code | Description |
| --- | --- | --- |
| **START** | `START` | 스크립트 실행의 시작점 (Flow Out) |
| **Logic: IF** | `LOGIC_IF` | 참/거짓 조건에 따른 실행 흐름(Flow) 분기 |
| **Logic: LOOP** | `LOGIC_LOOP` | 지정된 횟수만큼 특정 Flow를 반복 실행 |
| **Check: Key** | `COND_KEY` | 특정 키보드 입력 여부(True/False) 반환 |
| **Constant** | `CONSTANT` | 고정된 숫자 데이터 제공 |
| **System Log** | `LOGGER` | 시스템 이벤트 및 동작 히스토리를 텍스트 뷰어로 출력 |
| **Print Log** | `PRINT` | 연결된 데이터 값을 콘솔에 출력 (디버깅용) |

### 2. MT4 Robot Nodes (로봇 팔 제어)
| Node Name | Type Code | Description |
| --- | --- | --- |
| **MT4 Driver** | `MT4_DRIVER` | MT4 하드웨어 연산 코어 및 동적 상태 표시 노드 |
| **MT4 Action** | `MT4_ACTION` | 목표 좌표로 이동, 그리퍼 제어, Homing 등 직접 명령 |
| **MT4 Keyboard** | `MT4_KEYBOARD` | WASD 기반의 MT4 수동 조작 인텐트(Intent) 반환 |
| **MT4 Unity** | `MT4_UNITY` | Unity에서 수신된 JSON 좌표를 MT4 좌표계로 변환 |
| **UDP Receiver** | `UDP_RECV` | Unity 패킷 수신 및 로봇 현재 상태(피드백) 송신 |

### 3. Go1 Robot Nodes (4족 보행 로봇 제어)
| Node Name | Type Code | Description |
| --- | --- | --- |
| **Go1 Driver** | `GO1_DRIVER` | Go1 하드웨어 연산 코어 및 속도(Vx, Vy, Wz) 인텐트 처리 |
| **Go1 Action** | `GO1_ACTION` | 스탠드, 걷기, 회전, Yaw 영점 초기화 등 사전 정의된 동작 수행 |
| **Go1 Keyboard** | `GO1_KEYBOARD` | WASD 기반의 Go1 텔레옵(Teleop) 제어 신호 반환 |
| **Go1 Unity** | `GO1_UNITY` | Unity 디지털 트윈에서 보내는 원격 이동 명령 수신 및 적용 |
| **Cam Control** | `CAM_CTRL` | Go1 다중 카메라 스트리밍 백그라운드 프로세스 시작/종료 제어 |
| **Target IP** | `TARGET_IP` | 카메라 스트림을 수신할 타겟 PC의 IP 문자열 제공 |
| **AI Sender** | `MULTI_SENDER` | 수집된 카메라 이미지를 AI 분석 서버로 비동기 전송 제어 |
| **Get Go1 State** | `GET_GO1_STATE`| Go1의 현재 월드 좌표(X, Z) 및 Yaw 각도 반환 |

---

## ⚙️ Getting Started

### Prerequisites
* Python 3.8+
* `dearpygui`, `pyserial`, `aiohttp`
* **Unitree Legged SDK** (Go1 제어를 위해 필수, `robot_interface` 모듈 필요)

### Installation

```bash
# 1. Clone the repository
git clone [https://github.com/khw18033/PyGui-Visual-Scripting.git](https://github.com/khw18033/PyGui-Visual-Scripting.git)
cd PyGui-Visual-Scripting

# 2. Install dependencies
pip install dearpygui pyserial aiohttp
# (Raspberry Pi/Ubuntu 22.04 환경의 경우: --break-system-packages 사용 고려)

# 3. Unitree SDK Setup (Go1 제어 PC 한정)
# Go1 제어를 위해서는 unitree_legged_sdk가 빌드되어 있어야 하며,
# 파이썬 경로(sys.path)가 SDK의 /lib/python/arm64 (또는 amd64)를 가리키도록 설정해야 합니다.