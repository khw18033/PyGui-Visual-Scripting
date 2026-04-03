# GO1 Server Sender 핵심 발견 사항

작성일: 2026-04-03
대상 파일: nodes/robots/go1.py

## 1) 최신 프레임 선택 기준이 생성시간 기반
- 업로더가 최신 파일을 고를 때 `os.path.getctime` 기준으로 선택합니다.
- 동시 쓰기/파일시스템 타이밍 이슈가 있으면 실제 최신 프레임이 아닌 파일이 선택될 수 있습니다.
- 근거:
  - `valid_files.append((os.path.getctime(f), f))` (line 472)
  - `_, latest_file = max(valid_files)` (line 477)
  - `if latest_file != last_processed_file:` (line 478)

## 2) 업로드 파일명이 고정
- 업로드 시 파일명이 항상 `{camera_id}_calib.jpg`로 고정됩니다.
- 서버/클라이언트 캐시 정책에 따라 이전 이미지가 겹쳐 보일 가능성이 있습니다.
- 근거:
  - `filename=f"{camera_id}_calib.jpg"` (line 444)

## 3) 동일 경로에 대한 다중 쓰기 가능성
- GStreamer 수신 파이프라인과 Video Save 노드가 모두 JPG를 파일로 씁니다.
- 폴더가 동일하게 설정되면 업로더가 쓰기 중 파일/이전 파일을 집을 수 있습니다.
- 근거:
  - GStreamer 저장: `multifilesink location="{target_folder}/front_%06d.jpg"` (line 360)
  - Video Save 저장: `filename = os.path.join(folder, f"front_{self._frame_index:06d}.jpg")` (line 1393)
  - `cv2.imwrite(filename, frame)` (line 1394)

## 4) 예외 무시로 드롭 원인 은닉
- 업로드/비동기 워커 주요 구간에서 `except Exception: pass`가 많아
  타임아웃/읽기 실패/네트워크 실패가 발생해도 로그로 확인이 어렵습니다.
- 근거:
  - `session.post(... timeout=aiohttp.ClientTimeout(total=2.0))` (line 446)
  - `except Exception: pass` (line 448, 483, 493 등)

## 5) 표시용 프레임이 의도적으로 1프레임 늦음
- 화면 표시 경로에서 최신 파일이 아닌 `files[-2]`를 읽습니다.
- 체감상 이전 프레임이 반복/겹쳐 보이는 인상을 강화할 수 있습니다.
- 근거:
  - `files.sort(key=os.path.getctime)` (line 1105)
  - `target_file = files[-2]` (line 1106)
  - `_last_frame` 캐시 재사용 (line 1093, 1112)

## 우선순위 요약
1. 고정 파일명 업로드로 인한 캐시 충돌 가능성
2. 파일 폴링 + 생성시간 선택 + 동시 쓰기 구조의 조합으로 인한 프레임 선택 불안정
3. 예외 무시로 인해 실제 드롭 원인 가시성 부족
