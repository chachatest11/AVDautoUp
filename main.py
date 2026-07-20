"""AVDautoUp CLI.

명령:
  doctor          기기 테스트 전 환경 점검 (SDK/도구/가속/의존성)
  init            AVD 20개 생성 (사용자 지정 경로에 저장)
  setup-channels  각 AVD 부팅 → fingerprint 주입 → 로그인 → 스냅샷 저장
  dump-ui         실행 중 화면의 UI 트리 덤프 (SELECTORS 보정용)
  status          생성된 AVD 목록/경로 확인
  encrypt         계정 비밀번호 암호화 (channels.yaml 에 넣을 값 생성)
  upload          업로드 배치 실행 (BGM+프록시+랜덤+재시도)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

import typer

from avd_manager import AVDError, AVDManager, Fingerprint
from config_manager import ConfigManager

app = typer.Typer(add_completion=False, help="YouTube Shorts 다채널 자동 업로드 시스템")

# device_model → avdmanager 의 -d 식별자. 다양화 옵션에서 순환 사용.
DEVICE_POOL = [
    "pixel_6_pro",
    "pixel_6",
    "pixel_5",
    "pixel_4",
    "pixel_3a",
]


def _manager(avd_home: Optional[str], sdk_root: Optional[str]) -> tuple[ConfigManager, AVDManager]:
    cm = ConfigManager(avd_home_cli=avd_home)
    cm.ensure_avd_home()
    am = AVDManager(
        avd_home=cm.get_avd_home(),
        sdk_root=Path(sdk_root) if sdk_root else (
            Path(cm.settings.android_sdk_root) if cm.settings.android_sdk_root else None
        ),
    )
    return cm, am


@app.command()
def init(
    num_channels: int = typer.Option(20, "--num-channels", help="생성할 AVD 개수"),
    avd_home: Optional[str] = typer.Option(None, "--avd-home", help="AVD 저장 경로(외장 SSD 등)"),
    sdk_root: Optional[str] = typer.Option(None, "--sdk-root", help="Android SDK 경로"),
    api_level: int = typer.Option(34, "--api-level"),
    device_model: str = typer.Option("pixel_6_pro", "--device-model"),
    diverse_models: bool = typer.Option(False, "--diverse-models", help="기기 모델 다양화"),
    skip_image: bool = typer.Option(False, "--skip-image", help="시스템 이미지 설치 건너뜀"),
    force: bool = typer.Option(False, "--force", help="기존 AVD 덮어쓰기"),
):
    """AVD 를 지정한 경로에 생성한다."""
    cm, am = _manager(avd_home, sdk_root)
    cm.settings.avd_api_level = api_level
    cm.settings.avd_device_model = device_model
    cm.settings.diverse_models = diverse_models
    cm.settings.avd_home = str(cm.get_avd_home())
    cm.save_settings()

    typer.echo("── 설정 ─────────────────────────────")
    for k, v in cm.summary().items():
        typer.echo(f"  {k:>14}: {v}")
    typer.echo(f"  {'num_channels':>14}: {num_channels}")
    typer.echo("─────────────────────────────────────")

    if not skip_image:
        if not am.install_system_image(api_level):
            raise typer.Exit(code=1)

    ok = 0
    for i in range(1, num_channels + 1):
        name = f"avd_ch{i:02d}"
        model = DEVICE_POOL[(i - 1) % len(DEVICE_POOL)] if diverse_models else device_model
        fp = Fingerprint.for_device(i)
        if am.create_avd(
            name,
            api_level=api_level,
            device_model=model,
            ram=cm.settings.avd_ram,
            sdcard=cm.settings.avd_storage,
            fingerprint=fp,
            force=force,
        ):
            ok += 1
            typer.echo(f"  [{i:2d}/{num_channels}] ✓ {name} ({model})")
        else:
            typer.echo(f"  [{i:2d}/{num_channels}] ✗ {name} ({model})")

    typer.echo(f"\n완료: {ok}/{num_channels} 생성")
    if ok:
        typer.echo(f"다음: python main.py setup-channels --avd-home {cm.get_avd_home()}")


@app.command("setup-channels")
def setup_channels(
    avd_home: Optional[str] = typer.Option(None, "--avd-home"),
    sdk_root: Optional[str] = typer.Option(None, "--sdk-root"),
    base_port: int = typer.Option(5554, "--base-port", help="첫 에뮬레이터 콘솔 포트"),
    auto_login: bool = typer.Option(False, "--auto-login", help="uiautomator2 로 로그인 상태만 확인(수동 로그인 대기 생략)"),
):
    """각 AVD 를 부팅해 fingerprint 를 주입하고 로그인 후 스냅샷을 저장한다."""
    cm, am = _manager(avd_home, sdk_root)
    avds = sorted(am.list_avds())
    if not avds:
        typer.echo("생성된 AVD 가 없습니다. 먼저 `init` 을 실행하세요.")
        raise typer.Exit(code=1)

    for idx, name in enumerate(avds):
        channel_id = _channel_id_from_name(name, idx + 1)
        serial = f"emulator-{base_port}"  # 순차 처리이므로 동일 포트 재사용
        fp = Fingerprint.for_device(channel_id)

        typer.echo(f"\n=== [{idx + 1}/{len(avds)}] {name} (channel {channel_id}) ===")
        try:
            proc = am.boot_avd(name, port=base_port, fingerprint=fp)
        except AVDError as e:
            typer.echo(f"  부팅 실패: {e}")
            continue

        try:
            am.apply_runtime_fingerprint(fp, serial=serial)
            verified = am.verify_fingerprint(serial=serial)
            for k, v in verified.items():
                typer.echo(f"  {k} = {v}")

            if not auto_login:
                typer.echo("  📱 에뮬레이터에서 YouTube 로그인 후 Enter…")
                typer.prompt("  ", default="", show_default=False)

            am.save_snapshot(f"channel_{channel_id}", serial=serial)
        finally:
            am.shutdown(serial=serial)
            proc.wait(timeout=30) if proc.poll() is None else None

    typer.echo("\n✓ 채널 설정 완료")


@app.command()
def status(
    avd_home: Optional[str] = typer.Option(None, "--avd-home"),
    sdk_root: Optional[str] = typer.Option(None, "--sdk-root"),
):
    """AVD 경로 및 목록 확인."""
    cm, am = _manager(avd_home, sdk_root)
    avds = sorted(am.list_avds())
    typer.echo(f"AVD Home : {cm.get_avd_home()}")
    typer.echo(f"SDK Root : {am.sdk_root}")
    typer.echo(f"총 AVD   : {len(avds)}")
    for i, a in enumerate(avds, 1):
        typer.echo(f"  {i:2d}. {a}")


@app.command()
def encrypt(
    password: str = typer.Argument(..., help="암호화할 평문 비밀번호"),
    avd_home: Optional[str] = typer.Option(None, "--avd-home"),
):
    """channels.yaml 에 넣을 암호화된 비밀번호를 출력한다."""
    cm = ConfigManager(avd_home_cli=avd_home)
    typer.echo(cm.encrypt_password(password))


@app.command()
def upload(
    batch: str = typer.Option("config/upload_batch.yaml", "--batch"),
    avd_home: Optional[str] = typer.Option(None, "--avd-home"),
    sdk_root: Optional[str] = typer.Option(None, "--sdk-root"),
    base_port: int = typer.Option(5554, "--base-port"),
    randomize: bool = typer.Option(True, "--randomize/--no-randomize", help="업로드 시간 랜덤화"),
    min_delay: int = typer.Option(60, "--min-delay", help="랜덤 대기 최소(초)"),
    max_delay: int = typer.Option(300, "--max-delay", help="랜덤 대기 최대(초)"),
    retries: int = typer.Option(3, "--retries", help="실패 시 최대 재시도 횟수"),
    bgm_volume: float = typer.Option(0.3, "--bgm-volume", help="BGM 볼륨(0~1)"),
    use_proxy: bool = typer.Option(True, "--proxy/--no-proxy", help="channels.yaml 프록시 사용"),
    strict_video: bool = typer.Option(False, "--strict-video", help="쇼츠 사양 위반 시 중단"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """업로드 배치를 순차 실행한다 (BGM 삽입 + 프록시 + 랜덤 대기 + 재시도)."""
    cm = ConfigManager(avd_home_cli=avd_home)
    try:
        batch_cfg = cm.load_upload_batch(batch)
    except FileNotFoundError as e:
        typer.echo(str(e))
        raise typer.Exit(code=1)

    typer.echo(f"배치: {batch} · 항목 {len(batch_cfg.uploads)}개 · dry_run={dry_run}")

    if dry_run:
        # dry-run 은 SDK/에뮬레이터 없이 배치 내용만 검증한다.
        for i, up in enumerate(batch_cfg.uploads, 1):
            bgm = f" +BGM({Path(up.bgm_file).name})" if up.bgm_file else ""
            sched = f" @{up.scheduled_time}" if up.scheduled_time else ""
            typer.echo(f"  {i}. ch{up.channel_id} · {up.title} · {up.visibility}{sched}{bgm}")
        raise typer.Exit()

    from network_manager import NetworkManager
    from scheduler import SchedulerOptions, UploadScheduler
    from video_processor import VideoProcessor

    _, am = _manager(avd_home, sdk_root)

    net = NetworkManager()
    if use_proxy:
        try:
            net.load_from_channels(cm.load_channels())
        except FileNotFoundError:
            typer.echo("  ⚠️ channels.yaml 없음 — 프록시 없이 진행")

    scheduler = UploadScheduler(
        avd_manager=am,
        network_manager=net,
        video_processor=VideoProcessor(temp_dir=cm.settings.temp_dir),
        options=SchedulerOptions(
            randomize=randomize,
            min_delay=min_delay,
            max_delay=max_delay,
            max_retries=retries,
            base_port=base_port,
            bgm_volume=bgm_volume,
            strict_video=strict_video,
        ),
    )

    results = scheduler.run(batch_cfg.uploads)

    typer.echo("\n── 결과 ─────────────────────────────")
    ok = sum(1 for r in results if r.success)
    for r in results:
        mark = "✓" if r.success else "✗"
        typer.echo(f"  {mark} ch{r.channel_id} · {r.title} · 시도 {r.attempts}회 · {r.message}")
    typer.echo(f"성공 {ok}/{len(results)}")


@app.command()
def doctor(
    avd_home: Optional[str] = typer.Option(None, "--avd-home"),
    sdk_root: Optional[str] = typer.Option(None, "--sdk-root"),
):
    """기기 테스트 전 환경 점검 (SDK/도구/가속/의존성)."""
    typer.echo("── 환경 점검 ────────────────────────")
    ok = True

    def check(label: str, passed: bool, hint: str = "") -> None:
        nonlocal ok
        mark = "✓" if passed else "✗"
        line = f"  {mark} {label}"
        if not passed and hint:
            line += f"  → {hint}"
        typer.echo(line)
        ok = ok and passed

    # Python
    import sys

    check(f"Python {sys.version_info.major}.{sys.version_info.minor}", sys.version_info >= (3, 9), "3.9+ 필요")

    # SDK 경로
    resolved_sdk = (
        sdk_root
        or os.getenv("ANDROID_SDK_ROOT")
        or os.getenv("ANDROID_HOME")
    )
    if not resolved_sdk:
        for c in (Path.home() / "Android/Sdk", Path.home() / "Library/Android/sdk", Path("/opt/android-sdk")):
            if c.exists():
                resolved_sdk = str(c)
                break
    check(f"Android SDK: {resolved_sdk or '미발견'}", bool(resolved_sdk and Path(resolved_sdk).exists()),
          "ANDROID_SDK_ROOT 설정 또는 --sdk-root")

    # SDK 하위 도구
    sdk = Path(resolved_sdk) if resolved_sdk else None
    def sdk_tool(*rel: str) -> bool:
        if sdk:
            for r in rel:
                if (sdk / r).exists():
                    return True
        return False
    check("avdmanager", sdk_tool("cmdline-tools/latest/bin/avdmanager", "tools/bin/avdmanager"),
          "sdkmanager 'cmdline-tools;latest' 설치")
    check("emulator", sdk_tool("emulator/emulator"), "sdkmanager 'emulator' 설치")
    check("adb", sdk_tool("platform-tools/adb") or shutil.which("adb") is not None,
          "sdkmanager 'platform-tools' 설치")

    # ffmpeg (BGM)
    check("ffmpeg", shutil.which("ffmpeg") is not None, "BGM 삽입에 필요 — brew/apt 로 설치")
    check("ffprobe", shutil.which("ffprobe") is not None, "영상 검증에 필요")

    # uiautomator2 (자동화)
    try:
        import uiautomator2  # noqa: F401
        check("uiautomator2", True)
    except Exception:
        check("uiautomator2", False, "pip install uiautomator2")

    # 하드웨어 가속
    if sys.platform.startswith("linux"):
        check("KVM 가속(/dev/kvm)", Path("/dev/kvm").exists(),
              "가속 없으면 부팅이 매우 느림 — cpu 가상화 활성화")

    # AVD 저장 경로 쓰기 가능
    cm = ConfigManager(avd_home_cli=avd_home)
    home = cm.get_avd_home()
    writable = True
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".write_test"
        probe.write_text("ok")
        probe.unlink()
    except Exception:
        writable = False
    check(f"AVD 저장경로 쓰기: {home}", writable, "경로 권한/마운트 확인(외장 SSD면 연결 상태)")

    typer.echo("─────────────────────────────────────")
    typer.echo("모두 통과 ✅ — 기기 테스트 진행 가능" if ok else "일부 실패 ❌ — 위 hint 참고 후 재실행")
    raise typer.Exit(code=0 if ok else 1)


@app.command("dump-ui")
def dump_ui(
    serial: str = typer.Option("emulator-5554", "--serial", help="실행 중인 에뮬레이터 serial"),
    out: str = typer.Option("ui_hierarchy.xml", "--out", help="UI 트리 저장 파일"),
):
    """실행 중인 YouTube 화면의 UI 트리를 덤프한다 (SELECTORS 보정용).

    사용법: 에뮬레이터에서 보정하려는 화면을 띄운 뒤 실행.
    저장된 XML 에서 resource-id / text / content-desc 를 찾아
    automation.py 의 SELECTORS 를 채운다.
    """
    try:
        import uiautomator2 as u2
    except Exception:
        typer.echo("uiautomator2 가 필요합니다: pip install uiautomator2")
        raise typer.Exit(code=1)

    d = u2.connect(serial)
    xml = d.dump_hierarchy()
    Path(out).write_text(xml, encoding="utf-8")
    typer.echo(f"✓ UI 트리 저장: {out} ({len(xml)} bytes)")
    typer.echo(f"현재 앱: {d.app_current()}")


def _channel_id_from_name(name: str, fallback: int) -> int:
    digits = "".join(ch for ch in name if ch.isdigit())
    return int(digits) if digits else fallback


if __name__ == "__main__":
    app()
