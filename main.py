"""AVDautoUp CLI (Phase 1).

명령:
  init            AVD 20개 생성 (사용자 지정 경로에 저장)
  setup-channels  각 AVD 부팅 → fingerprint 주입 → 로그인 → 스냅샷 저장
  status          생성된 AVD 목록/경로 확인
  encrypt         계정 비밀번호 암호화 (channels.yaml 에 넣을 값 생성)
  upload          업로드 배치 실행 (자동화 프로토타입)
"""

from __future__ import annotations

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
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """업로드 배치를 순차 실행한다."""
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
            typer.echo(f"  {i}. ch{up.channel_id} · {up.title} · {up.visibility} · {up.video_file}")
        raise typer.Exit()

    _, am = _manager(avd_home, sdk_root)
    from automation import AutomationError, YouTubeAutomation

    for up in batch_cfg.uploads:
        name = f"avd_ch{up.channel_id:02d}"
        serial = f"emulator-{base_port}"
        typer.echo(f"\n▶ ch{up.channel_id} · {up.title}")
        proc = am.boot_avd(name, port=base_port, fingerprint=Fingerprint.for_device(up.channel_id))
        try:
            am.load_snapshot(f"channel_{up.channel_id}", serial=serial)
            yt = YouTubeAutomation(serial=serial)
            result = yt.upload_short(
                channel_id=up.channel_id,
                video_local_path=up.video_file,
                title=up.title,
                description=up.description,
                visibility=up.visibility,
                scheduled_time=up.scheduled_time,
            )
            typer.echo(("  ✓ " if result.success else "  ✗ ") + result.message)
        except AutomationError as e:
            typer.echo(f"  ✗ 자동화 오류: {e}")
        finally:
            am.shutdown(serial=serial)
            if proc.poll() is None:
                proc.wait(timeout=30)


def _channel_id_from_name(name: str, fallback: int) -> int:
    digits = "".join(ch for ch in name if ch.isdigit())
    return int(digits) if digits else fallback


if __name__ == "__main__":
    app()
