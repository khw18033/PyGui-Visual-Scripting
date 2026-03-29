

## 다른 노트북(PC)에서 실행할 때 준비/주의사항

이 가이드는 우분투(Ubuntu) 환경의 새 노트북에서 Go1 로봇의 **영상 수신(GStreamer)**과 **몸통 제어(Unitree SDK)**를 완벽하게 구동하기 위한 통합 설정 매뉴얼입니다.

## 1단계: 필수 시스템 패키지 설치
우분투 환경에서 영상을 디코딩하고 처리하기 위해 GStreamer 관련 패키지를 OS에 설치합니다.

터미널을 열고 아래 명령어를 실행하세요.
```bash
sudo apt-get update
sudo apt-get install gstreamer1.0-tools gstreamer1.0-plugins-good gstreamer1.0-plugins-bad
```

## 2단계: 네트워크 및 SSH 하이패스 개통 (가장 중요)
파이썬 스크립트가 로봇에 백그라운드로 자동 접속하려면, 비밀번호 없이 통과할 수 있는 SSH 키 인증이 필수입니다.

### 1. 라즈베리파이(징검다리) 현재 IP 확인
로봇과 같은 Wi-Fi에 연결한 후, 현재 라즈베리파이가 할당받은 진짜 IP를 찾습니다.
```bash
ping raspberrypi.local
```
*(출력되는 IP, 예: `192.168.50.42`를 기억해 둡니다.)*

### 2. 노트북의 SSH 보안 열쇠 생성
엔터를 3번 연속으로 눌러 암호 없는 열쇠를 만듭니다.
```bash
ssh-keygen -t rsa
```

### 3. 라즈베리파이(징검다리)에 열쇠 복사
위에서 찾은 IP를 입력합니다. (비밀번호: `123`)
```bash
ssh-copy-id pi@192.168.50.42
```

### 4. 나노 보드(카메라 목적지)에 열쇠 복사 (ProxyJump 사용)
`ssh-copy-id` 명령어의 버그를 우회하기 위해 `-o ProxyJump` 옵션을 사용하여 나노 보드(`192.168.123.13`)까지 열쇠를 밀어 넣습니다. (비밀번호: `123`)
```bash
ssh-copy-id -o "ProxyJump pi@192.168.50.42" unitree@192.168.123.13
```

## 3단계: 소스 코드 및 Unitree SDK 이식 (Git)
git clone --branch Go1_DS https://github.com/khw18033/PyGui-Visual-Scripting Go1_DS

## 4단계: 파이썬 3.8 전용 가상환경 구축 (핵심 에러 방지)
**[주의]** Unitree SDK(`robot_interface`)는 **파이썬 3.8 버전 전용**으로 굳게 빌드되어 있습니다. 최신 우분투의 기본 파이썬(3.10 등)을 사용하면 모듈을 찾을 수 없다는 에러와 함께 시뮬레이션 모드로 강제 전환됩니다.

### 1. 파이썬 3.8 강제 설치
```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.8 python3.8-venv python3.8-dev
```

### 2. 프로젝트 폴더 내 가상환경(venv) 생성 및 접속
```bash
# 반드시 프로젝트 폴더(Go1_DS.py가 있는 곳)로 이동 후 실행
python3.8 -m venv venv_go1
source venv_go1/bin/activate
```

### 3. 필수 파이썬 라이브러리 설치
가상환경(`venv_go1`)에 접속된 상태에서 아래 명령어를 실행합니다.
```bash
pip install --upgrade pip
pip install nano opencv-contrib-python flask aiohttp dearpygui pyserial numpy
```

## 5단계: 터미널 자동화 세팅 (편의성)
터미널을 열 때마다 매번 폴더를 찾아가서 가상환경을 켜는 귀찮음을 없애줍니다.

```bash

# 2. 터미널을 열면 자동으로 3.8 가상환경 켜기
nano ~/.bashrc
파일 안에 들어가서 맨 아래에 아래 1줄 작성 후 저장, 나가기
source home/(PC이름)/venv_go1/bin/activate

# 3. 설정 즉시 적용
source ~/.bashrc
```

---

## 다른 Go1 로봇에서 실행할 때 준비/주의사항

로봇이 바뀌면 IP 주소와 내부 커스텀 스크립트 존재 여부를 가장 먼저 확인해야 합니다.

* **송출 스크립트(`/home/unitree/go1_send_both.sh`) 이식:**
  이 파이썬 코드는 로봇 내부에 송출용 쉘 스크립트가 이미 만들어져 있다는 전제하에 작동합니다. 새로운 Go1 로봇의 Nano 13, 14, 15 보드 각각에 접속하여 해당 스크립트 파일과 `kill_camera.sh`를 복사해 주고, **실행 권한(`chmod +x`)**을 반드시 부여해야 합니다.
* **유동 IP (라즈베리파이) 확인 및 코드 수정:**
  나노 보드의 IP(`192.168.123.x`)는 로봇 내부망이라 고정이지만, 라즈베리파이가 외부 공유기로부터 할당받는 IP(현재 `192.168.50.41`)는 로봇이 바뀌거나 공유기가 바뀌면 무조건 달라집니다.
  * 새로운 로봇의 라즈베리파이 IP를 확인한 후, 파이썬 코드 내의 `-J pi@192.168.50.41` 부분을 새 IP로 수정해야 합니다.
* **로봇 관리자 비밀번호 확인:**
  현재 코드는 `echo 123 | sudo -S` 형태로 Unitree 기본 비밀번호인 `123`을 하드코딩하여 권한을 탈취합니다. 만약 다른 Go1 로봇의 비밀번호가 변경되어 있다면 코드 내의 `123` 부분을 해당 비밀번호로 변경해야 합니다.

---