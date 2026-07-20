# AVDautoUp: YouTube Shorts 다채널 자동 업로드 시스템

20개의 유튜브 채널을 가상 모바일 기기(Android Virtual Device)에서 관리하고 쇼츠 영상을 자동으로 업로드하는 프로그램입니다.

## 🎯 특징

- **20개 가상 디바이스 자동 생성** — Android Studio GUI 없이 Python으로 완전 자동화
- **고유 식별자(Fingerprint) 설정** — Serial, IMEI, Android ID 각각 다르게 설정하여 YouTube 감지 회피
- **순차 업로드** — 1~2개씩 순차 부팅으로 리소스 효율성 극대화
- **IP 분산** — 프록시/VPN을 각 AVD에 고정 할당
- **유연한 경로 관리** — 외장 SSD 또는 PC 저장공간 선택 가능
- **설정 파일 기반** — YAML로 채널/업로드 설정 관리

## 📋 시스템 요구사항

- Python 3.9+
- Android SDK (또는 자동 설치 가능)
- Linux, macOS, 또는 Windows WSL2
- 저장소: 20개 AVD × ~2GB = 약 40GB + 시스템 이미지

## 🚀 빠른 시작

### 1. 설치

```bash
# 저장소 클론
git clone <repo-url> AVDautoUp
cd AVDautoUp

# 의존성 설치
pip install -r requirements.txt
```

### 2. 전역 설정 (선택)

`config/settings.yaml` 수정:

```yaml
avd_home: /mnt/external_ssd/android_avd  # 또는 ~/.android/avd
android_sdk_root: /path/to/android/sdk   # 자동 감지되면 생략 가능
```

### 3. AVD 초기화 (20개 생성)

```bash
# 기본값 사용
python main.py init --num-channels 20

# 또는 외장 SSD 지정
python main.py init --num-channels 20 --avd-home /mnt/external_ssd/android_avd

# 기기 모델 다양화
python main.py init --num-channels 20 --diverse-models
```

**소요 시간**: 약 10~15분 (네트워크 속도에 따라 다름)

### 4. 채널 설정 (각 AVD에 로그인)

```bash
python main.py setup-channels --avd-home /mnt/external_ssd/android_avd
```

**과정**:
1. 각 AVD 순차 부팅
2. Fingerprint (Serial, IMEI) 자동 설정
3. YouTube 앱에서 **수동 로그인**
4. 로그인 완료 후 스냅샷 저장

**소요 시간**: 약 30분~1시간 (채널당 3~5분)

### 5. 상태 확인

```bash
python main.py status --avd-home /mnt/external_ssd/android_avd
```

### 6. 업로드 배치 준비

`config/upload_batch.yaml` 수정:

```yaml
uploads:
  - channel_id: 1
    video_file: "/path/to/video1.mp4"
    title: "쇼츠 제목 1"
    description: "설명"
    bgm_file: "/path/to/bgm.mp3"
    visibility: "PRIVATE"
    scheduled_time: "2026-07-25 10:00:00"  # 선택사항
```

### 7. 일괄 업로드

```bash
# 확인 모드 (SDK 없이 배치 내용만 검증)
python main.py upload --batch config/upload_batch.yaml --dry-run

# 실제 업로드 (BGM 삽입 + 프록시 + 랜덤 대기 + 재시도 포함)
python main.py upload --batch config/upload_batch.yaml

# 옵션 예시: 랜덤 대기 끄고, 재시도 5회, BGM 볼륨 0.2
python main.py upload --no-randomize --retries 5 --bgm-volume 0.2

# 프록시 미사용, 쇼츠 사양 위반 시 중단
python main.py upload --no-proxy --strict-video
```

**동작 순서** (채널별 순차):
1. 랜덤 대기 (`--min-delay`~`--max-delay`, 기본 60~300초)
2. 고정 프록시(`channels.yaml`) + fingerprint 로 AVD 부팅
3. 스냅샷 복원 (로그인 상태)
4. 영상 전처리 — ffmpeg 로 BGM 삽입, 쇼츠 사양 검증
5. uiautomator2 로 업로드 (제목/설명/공개범위)
6. AVD 종료 — 실패 시 지수 백오프로 재시도

## 📁 디렉토리 구조

```
AVDautoUp/
├── PLANNING.md                 # 프로젝트 기획서
├── README.md                   # 이 파일
├── requirements.txt            # Python 의존성
├── config/
│   ├── settings.yaml           # 전역 설정
│   ├── channels.yaml           # 채널 설정 (계정 정보)
│   └── upload_batch.yaml       # 업로드 배치
├── config_manager.py           # 설정 관리
├── avd_manager.py              # AVD 생성/제어 + fingerprint
├── automation.py               # YouTube UI 자동화 (uiautomator2)
├── video_processor.py          # 영상 처리 (ffmpeg BGM 삽입/검증)
├── network_manager.py          # 네트워크 분산 (프록시)
├── scheduler.py                # 순차 스케줄링 + 재시도
└── main.py                     # CLI 진입점
```

## 🔧 CLI 명령어

### init - AVD 초기화

```bash
python main.py init [OPTIONS]

Options:
  --num-channels INTEGER           생성할 AVD 개수 (기본값: 20)
  --avd-home TEXT                  AVD 저장 경로 (기본값: ~/.android/avd)
  --sdk-root TEXT                  Android SDK 경로
  --api-level INTEGER              Android API 레벨 (기본값: 34)
  --device-model TEXT              기기 모델명 (기본값: Pixel 6 Pro)
  --diverse-models                 기기 모델 다양화
  --force                          기존 AVD 덮어쓰기
```

### setup-channels - 채널 설정

```bash
python main.py setup-channels [OPTIONS]

Options:
  --avd-home TEXT                  AVD 저장 경로
```

### status - 상태 확인

```bash
python main.py status [OPTIONS]

Options:
  --avd-home TEXT                  AVD 저장 경로
```

### upload - 일괄 업로드

```bash
python main.py upload [OPTIONS]

Options:
  --batch TEXT                     배치 파일 경로 (기본값: config/upload_batch.yaml)
  --avd-home TEXT                  AVD 저장 경로
  --dry-run                        실제 업로드 없이 확인만
```

## 🎬 동작 흐름

### Phase 1: AVD 생성 + Fingerprint 설정 (현재)

```
1. init
   ├─ 시스템 이미지 설치
   ├─ 20개 AVD 생성
   └─ 각 AVD 기본 설정

2. setup-channels
   ├─ 각 AVD 순차 부팅
   ├─ Fingerprint 설정 (Serial, IMEI)
   ├─ YouTube 수동 로그인
   └─ 스냅샷 저장

3. status
   └─ 생성된 AVD 목록 확인
```

### Phase 2: 업로드 자동화

```
- automation.py 구현
- YouTube Shorts 업로드 UI 자동화
- BGM 삽입
- 메타데이터 설정
```

### Phase 3: 최적화

```
- IP 분산 (프록시/VPN)
- 업로드 시간 랜덤화
- 네트워크 안정성
```

### Phase 4: 운영

```
- 감지 회피
- 자동 재로그인
- 모니터링 대시보드
```

## ⚠️ 주의사항

### YouTube 정책

- **자동 다중 계정 업로드는 YouTube 서비스 약관 위반 가능성**
- 채널 제재 또는 정지될 수 있음
- 본인 소유 채널에 본인 콘텐츠 업로드 권장

### 법적 고려사항

- 본인 콘텐츠가 아닌 경우 저작권 침해 위험
- 현지 규정 확인 후 사용

### 기술적 주의

- 프로세스 중단 시 AVD 저장소 손상 가능
- 외장 SSD는 USB 분리 금지
- 정기적 백업 권장

## 🐛 문제 해결

### Android SDK를 찾을 수 없다

```bash
# 수동으로 경로 지정
export ANDROID_SDK_ROOT=/path/to/android/sdk
python main.py init --sdk-root /path/to/android/sdk
```

### ADB 연결 실패

```bash
# ADB 프로세스 재시작
adb kill-server
adb start-server
```

### AVD 부팅 시간이 너무 김

```bash
# 스냅샷 저장 여부 확인
adb emu avd snapshot list

# 스냅샷에서 부팅 (자동)
```

## 📞 지원

문제가 발생하면 로그를 확인하세요:

```bash
# 로그 디렉토리
ls -la logs/
```

## 📄 라이선스

[라이선스 정보 추가]

## 🔗 관련 자료

- [Android Virtual Device 문서](https://developer.android.com/studio/run/managing-avds)
- [YouTube Shorts 업로드 가이드](https://support.google.com/youtube/answer/7679057)
- [PLANNING.md](PLANNING.md) - 상세 기획서
