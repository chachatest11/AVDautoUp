# YouTube Shorts 다채널 자동 업로드 시스템 기획서

> **문서 버전:** v1.0
> **작성일:** 2026-07-20
> **프로젝트 코드명:** AVDautoUp

---

## 1. 프로젝트 개요

### 1.1 목적
유튜브 다채널(최대 20개)을 운영하면서, 각 채널을 격리된 가상 모바일 기기(Android Virtual Device)에서 관리하고 쇼츠(Shorts) 영상을 자동으로 업로드하는 시스템을 구축한다.

### 1.2 핵심 요구사항
- **20개 가상 디바이스**를 프로그램으로 자동 생성 및 관리
- **1~2개씩 순차 부팅** 방식으로 리소스 부담 최소화
- 각 디바이스별 **고유 식별자(fingerprint)** 설정으로 계정 격리
- **IP 분산**(프록시/VPN) + **업로드 시간 랜덤화**로 비정상 패턴 회피
- 사용자가 **영상 파일, 제목, 설명, 발행 설정, BGM**을 지정하면 자동 업로드

### 1.3 왜 AVD인가 (블루스택 대비)
| 항목 | Google AVD | BlueStacks |
|------|-----------|------------|
| CLI 자동화 | ✅ `avdmanager`/`emulator`로 완전 제어 | ❌ GUI 중심, 자동화 제한적 |
| 헤드리스 실행 | ✅ `-no-window` 리눅스 서버 가능 | ❌ Windows/Mac 전용 |
| 스냅샷 | ✅ 로그인 상태 저장/복원 | ⚠️ 제한적 |
| 계정 격리 | ✅ AVD별 독립 데이터 | ⚠️ 인스턴스 무거움 |
| 상업적 자동화 | ✅ 표준 도구 | ❌ 라이선스 애매 |

**결론:** 20개 규모 자동화 및 서버 확장성을 고려할 때 **Google AVD** 채택. (대규모 확장 시 Docker 기반 redroid도 후속 검토 대상)

---

## 2. 시스템 아키텍처

### 2.1 전체 구조
```
AVDautoUp (YouTube Shorts Auto-Uploader)
│
├── avd_manager.py          # 가상 디바이스 생성/제어
│   ├── AVD 생성/삭제
│   ├── Fingerprint 설정 (Serial, IMEI, Android ID)
│   ├── MAC Address 변경
│   ├── 프록시/VPN 네트워크 할당
│   ├── 스냅샷 저장/복원
│   └── 부팅/종료 제어
│
├── video_processor.py      # 영상 전처리
│   ├── 포맷/사양 검증 (9:16, ≤60초)
│   ├── BGM 삽입 (ffmpeg)
│   ├── 썸네일 생성
│   └── AVD로 파일 푸시 (adb push)
│
├── automation.py           # YouTube 앱 UI 자동화
│   ├── 앱 실행 / 로그인
│   ├── Shorts 업로드 플로우
│   ├── 제목·설명 입력
│   ├── 발행 설정 (비공개/예약/공개)
│   └── 업로드 완료 감지
│
├── network_manager.py      # 네트워크 분산
│   ├── 프록시 리스트 관리
│   ├── VPN 프로파일 관리
│   └── AVD별 고정 IP 할당
│
├── scheduler.py            # 실행 스케줄링
│   ├── 채널별 순차 실행
│   ├── 업로드 시간 랜덤화
│   └── 재시도 / 에러 처리
│
├── config_manager.py       # 설정 관리
│   ├── 전역 설정 로드 (settings.yaml)
│   ├── 채널 설정 (channels.yaml)
│   ├── 업로드 배치 (upload_batch.yaml)
│   ├── AVD 경로 관리 (CLI / 환경변수 / 설정파일 우선순위)
│   └── 계정 정보 암호화 (cryptography)
│
├── ui.py                   # 인터페이스 (선택)
│   ├── CLI (Typer)
│   └── 웹 대시보드 (FastAPI, 후속)
│
└── main.py                 # 통합 실행 진입점
```

### 2.2 기술 스택
| 영역 | 라이브러리 / 도구 | 용도 |
|------|------------------|------|
| AVD 제어 | Android SDK (`avdmanager`, `emulator`, `adb`) | 기기 생성·부팅·제어 |
| 프로세스 제어 | Python `subprocess` | CLI 도구 호출 |
| UI 자동화 | `uiautomator2` (또는 Appium) | YouTube 앱 조작 |
| 영상 처리 | `ffmpeg`, `moviepy` | BGM 삽입, 포맷 변환 |
| 네트워크 | `requests[socks]` | 프록시 검증 |
| 스케줄링 | `asyncio`, `APScheduler` | 순차/시간 기반 실행 |
| 설정 | `PyYAML`, `Pydantic` | 구조화된 설정 검증 |
| 보안 | `cryptography` | 계정 정보 암호화 |
| CLI | `Typer` | 커맨드라인 인터페이스 |
| 대시보드(선택) | `FastAPI` | 웹 모니터링 |

---

## 3. 기능 상세

### 3.1 가상 디바이스 생성 (Python 프로그램화)
Android Studio GUI 없이 CLI를 `subprocess`로 호출하여 20개 AVD를 자동 생성.

**주요 단계:**
1. AVD 저장 경로 설정 (사용자 입력 또는 설정 파일)
   - CLI: `python main.py init --avd-home /mnt/external_ssd/android_avd`
   - 환경변수: `export ANDROID_AVD_HOME=/mnt/external_ssd/android_avd`
   - 설정파일: `config/settings.yaml`의 `avd_home` 항목
2. 시스템 이미지 설치 — `sdkmanager "system-images;android-34;google_apis_playstore;x86_64"`
   - YouTube 앱 로그인이 필요하므로 반드시 **Google Play 이미지**(`google_apis_playstore`) 사용
3. AVD 생성 — `avdmanager create avd -n avd_ch01 -k "..." -d "Pixel 6 Pro" --path $ANDROID_AVD_HOME/avd_ch01`
4. 고유 fingerprint 설정 (§3.2)
5. 네트워크 할당 (§3.4)
6. 스냅샷 저장 (§3.5)

**기기 모델:** 통일(예: Pixel 6 Pro)로 시작. fingerprint(Serial/IMEI/Android ID)가 다르면 모델이 같아도 별개 기기로 인식됨. 운영 중 필요 시 모델 다양화 옵션(`diverse_models=True`)으로 전환 가능하도록 설계.

### 3.2 고유 Fingerprint 설정
YouTube가 기기를 구분하는 기준과 중요도:

| 항목 | 중요도 | 설정 방식 |
|------|--------|----------|
| Serial Number | ⭐⭐⭐ | `setprop ro.boot.serialno` |
| IMEI | ⭐⭐⭐ | `setprop ril.gsm.imei` |
| Android ID | ⭐⭐⭐ | OS 레벨 고유 ID |
| MAC Address | ⭐⭐ | AVD 설정 파일 / 부팅 옵션 |
| Build Fingerprint | ⭐⭐ | `setprop ro.build.fingerprint` |
| Device Model | ⭐ | 보조 정보 (통일 가능) |

**채택 방식: ADB `setprop` 런타임 변경 + 스냅샷 저장 (방법 D)**
- AVD 부팅 → `adb root` → `setprop`으로 Serial/IMEI/Fingerprint 주입 → 스냅샷 저장
- 각 AVD가 고유 값을 가지므로 YouTube는 서로 다른 실제 기기로 인식

**MAC Address 변경:** AVD 설정 파일(`hardware-qemu.ini`)에 `hw.qemu.wifi.mac`, `hw.qemu.nic0.mac` 항목을 AVD별 고유 값으로 기록.

**검증:** 설정 후 `getprop`으로 각 AVD가 서로 다른 값을 반환하는지 확인.

### 3.3 순차 업로드 파이프라인
20개 동시 부팅이 아닌 **1~2개씩 순차** 처리로 리소스 충돌 방지.

```
Loop (채널 1 → 20):
  1. 랜덤 대기 (업로드 시간 랜덤화)
  2. AVD 부팅 (스냅샷 복원 → 로그인 상태 즉시 복구)
  3. ADB 연결 대기
  4. 영상 전처리 (BGM 삽입 등) 후 기기에 푸시
  5. YouTube 앱 Shorts 업로드 UI 자동 조작
  6. 제목/설명/발행설정 입력
  7. 업로드 완료 대기 및 감지
  8. AVD 종료
  → 다음 채널
```

**스냅샷 활용:** "로그인 완료 + fingerprint + 네트워크 설정" 상태를 스냅샷으로 저장해두면 매 부팅 시 즉시 복원(부팅 수 초)되어 순차 처리여도 총 소요 시간 최소화.

### 3.4 IP 분산 (네트워크)
각 AVD에 **고정 프록시/VPN을 미리 할당** (매 업로드마다 변경 불필요).

| 방식 | 설정 방법 | 특징 |
|------|----------|------|
| HTTP 프록시 | `emulator -http-proxy host:port` | 간단, HTTP만 지원 |
| WiFi 프록시 | `adb shell settings put global http_proxy ...` | 시스템 레벨 |
| VPN 앱 | APK 설치 + 자동 로그인 | SOCKS5 우회, YouTube 허용적 ⭐ |

**권장:** 프록시 서버가 확보되어 있으면 부팅 옵션(`-http-proxy`)으로 고정 할당. VPN 구독(무제한 프로파일)이면 AVD별 프로파일 분리. `config/proxies.json`에 국가별 프록시 리스트 관리.

### 3.5 스냅샷 관리
- 최초 1회: AVD 생성 → fingerprint → 네트워크 → 로그인 완료 후 `snapshot save`
- 이후: 매 업로드 시 `snapshot load`로 즉시 복원
- 채널별 스냅샷명: `channel_{id}` 또는 `setup_complete`

### 3.6 영상 업로드 사용자 입력 항목
사용자가 지정 가능한 메타데이터:

| 항목 | 값 | 비고 |
|------|-----|------|
| 영상 파일 | `video_file` 경로 | MP4 권장, 9:16, ≤60초 |
| 제목 | `title` | |
| 설명 | `description` | |
| 발행 여부 | `visibility` | `PRIVATE` / `UNLISTED` / `PUBLIC` |
| 예약 시간 | `scheduled_time` | 미지정 시 즉시 발행 |
| BGM | `bgm_file` | ffmpeg로 사전 삽입 |

---

## 4. 설정 파일 설계

### 4.1 전역 설정 (`config/settings.yaml`)
AVD 저장 경로 등 프로그램 전체에 적용되는 설정.

```yaml
# 필수 설정
avd_home: "/mnt/external_ssd/android_avd"  # AVD 저장 경로 (절대경로)
                                             # 또는 "~/.android/avd" (상대경로)
                                             # 또는 "/path/to/local/storage"

android_sdk_root: "/path/to/android/sdk"    # Android SDK 경로 (없으면 환경변수 사용)

# 선택 설정
log_dir: "./logs"                            # 로그 저장 경로
temp_dir: "./temp"                           # 임시 파일 경로 (영상 처리 등)
config_dir: "./config"                       # 설정 파일 경로

# AVD 생성 옵션
avd_api_level: 34                            # 안드로이드 API 레벨
avd_device_model: "Pixel 6 Pro"              # 기기 모델
avd_ram: 2048                                # 메모리 (MB)
avd_storage: 4096                            # 저장소 (MB)
diverse_models: false                        # 기기 모델 다양화 여부
```

### 4.2 채널 설정 (`config/channels.yaml`)
```yaml
channels:
  - id: 1
    email: "channel1@gmail.com"
    password: "<encrypted>"
    avd_device: "avd_ch01"
    proxy: { host: "proxy1.example.com", port: 8080, country: "US" }
  
  - id: 2
    email: "channel2@gmail.com"
    password: "<encrypted>"
    avd_device: "avd_ch02"
    proxy: { host: "proxy2.example.com", port: 8080, country: "UK" }
```

### 4.3 업로드 배치 (`config/upload_batch.yaml`)
```yaml
uploads:
  - channel_id: 1
    video_file: "/videos/shorts_001.mp4"
    title: "쇼츠 제목 #1"
    description: "설명글"
    bgm_file: "/bgm/music.mp3"
    visibility: "PRIVATE"
    scheduled_time: "2026-07-25 10:00:00"
  
  - channel_id: 2
    video_file: "/videos/shorts_002.mp4"
    title: "쇼츠 제목 #2"
    description: "설명글"
    bgm_file: "/bgm/music.mp3"
    visibility: "PUBLIC"
    # scheduled_time 생략 시 즉시 발행
```

### 4.4 경로 설정 우선순위
프로그램 실행 시 AVD 경로는 다음 순서로 결정됨:

1. **CLI 옵션** (최우선): `python main.py --avd-home /path/to/ssd`
2. **환경 변수**: `export ANDROID_AVD_HOME=/path/to/ssd`
3. **설정 파일**: `config/settings.yaml`의 `avd_home`
4. **기본값**: `~/.android/avd`

---

## 5. 개발 로드맵

| Phase | 기간(예상) | 내용 |
|-------|-----------|------|
| **Phase 1** | 1~2주 | 기본 파이프라인: AVD 생성 + fingerprint 설정 + YouTube 로그인 자동화 + 단순 업로드 |
| **Phase 2** | 1주 | 고급 기능: BGM 삽입(ffmpeg) + 메타데이터(제목/설명/예약) + 에러 처리·재시도 |
| **Phase 3** | 1주 | 최적화: IP 분산(프록시/VPN) + 업로드 시간 랜덤화 + 스냅샷 최적화 |
| **Phase 4** | 진행형 | 운영·안정화: 감지 회피, 자동 재로그인, 모니터링 대시보드 |

---

## 6. 리스크 및 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| YouTube 비정상 패턴 감지 | 채널 제재/정지 | fingerprint 분리, IP 분산, 업로드 시간·간격 랜덤화 |
| UI 변경으로 자동화 깨짐 | 업로드 실패 | UI 요소 셀렉터 추상화, 실패 감지·알림 |
| 계정 로그인 세션 만료 | 재작업 발생 | 스냅샷 + 자동 재로그인 로직 |
| 리소스 부족 | 부팅 실패 | 1~2개 순차 실행, 배치 크기 조절 |
| 프록시 불안정 | 네트워크 오류 | 부팅 전 프록시 헬스체크, 대체 프록시 |

### 정책·법적 유의사항
- 다수 계정 반복 자동 업로드는 YouTube 서비스 약관에 저촉될 수 있으며, 채널 제재 리스크가 상존한다.
- **본인 소유 채널에 본인 콘텐츠 업로드**가 목적이라면, UI 자동화보다 **YouTube Data API `videos.insert`**가 더 안정적일 수 있다(단, 일일 쿼터·OAuth 심사 제약 존재). 규모 확대 시 API 방식 병행 검토 권장.

---

## 7. 다음 단계

### Phase 1 착수 순서
1. `config_manager.py` 구현
   - 설정 파일 로드/검증 (YAML 파싱)
   - CLI 옵션 처리 (Typer)
   - AVD 경로 우선순위 결정 로직
   
2. `avd_manager.py` 구현
   - AVD 생성/삭제 (자동 경로 지정)
   - Fingerprint 설정 (Serial, IMEI)
   - 스냅샷 관리

3. `automation.py` 프로토타입
   - `uiautomator2` 기반 YouTube 로그인 자동화
   - Shorts 업로드 UI 조작

### CLI 사용 예시 (Phase 1 완성 후)
```bash
# AVD 경로 지정하여 초기화
python main.py init --avd-home /mnt/external_ssd/android_avd --num-channels 20

# 또는 설정 파일 사용
python main.py init --config config/settings.yaml

# 순차 업로드
python main.py upload --avd-home /mnt/external_ssd/android_avd --batch config/upload_batch.yaml
```
