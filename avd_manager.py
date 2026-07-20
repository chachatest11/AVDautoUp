"""AVD(Android Virtual Device) 생성·제어 모듈.

핵심 설계:
- 모든 avdmanager/emulator/adb 호출에 ANDROID_AVD_HOME 환경변수를 주입한다.
  이렇게 하면 AVD 의 .ini 포인터와 .avd 데이터가 모두 사용자가 지정한 경로
  (외장 SSD 또는 로컬)에 저장된다. 실행 시점마다 경로를 바꿔 넣을 수 있다.
- fingerprint(시리얼/IMEI 등)는 부팅 시 emulator 의 -prop 플래그로 주입하고,
  런타임에 바꿀 수 있는 값(android_id 등)은 adb 로 설정한다.

주의: ro.* 계열 시스템 프로퍼티는 이미지/커널에 따라 -prop 으로도 고정되지
않을 수 있다. 각 메서드 docstring 에 실제 반영 조건을 명시했다.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Fingerprint:
    """AVD 별 고유 식별자 묶음."""

    serialno: str
    imei: str
    android_id: str
    build_fingerprint: str
    wifi_mac: str

    @classmethod
    def for_device(cls, device_id: int) -> "Fingerprint":
        """채널 번호(1~N)로 결정적인 고유값 생성."""
        return cls(
            serialno=f"FA52N1A{device_id:05d}",
            imei=f"3556{device_id:011d}",
            android_id=f"{device_id:016x}",
            build_fingerprint=(
                f"samsung/o1sksx/o1s:13/TP1A.220624.014/"
                f"G991N{device_id:04d}:user/release-keys"
            ),
            wifi_mac=f"52:54:00:{(device_id >> 16) & 0xFF:02x}:"
            f"{(device_id >> 8) & 0xFF:02x}:{device_id & 0xFF:02x}",
        )

    def boot_props(self) -> list[str]:
        """emulator -prop 인자 목록 (부팅 시 주입)."""
        pairs = {
            "ro.serialno": self.serialno,
            "ro.boot.serialno": self.serialno,
            "ril.gsm.imei": self.imei,
            "ro.build.fingerprint": self.build_fingerprint,
        }
        args: list[str] = []
        for k, v in pairs.items():
            args += ["-prop", f"{k}={v}"]
        return args


class AVDError(RuntimeError):
    pass


class AVDManager:
    """avdmanager/emulator/adb 래퍼."""

    def __init__(
        self,
        avd_home: Path,
        sdk_root: Optional[Path] = None,
    ) -> None:
        self.avd_home = Path(avd_home).expanduser()
        self.avd_home.mkdir(parents=True, exist_ok=True)
        self.sdk_root = Path(sdk_root).expanduser() if sdk_root else self._find_sdk_root()

        self.avdmanager = self._tool("cmdline-tools/latest/bin/avdmanager", "tools/bin/avdmanager")
        self.sdkmanager = self._tool("cmdline-tools/latest/bin/sdkmanager", "tools/bin/sdkmanager")
        self.emulator = self._tool("emulator/emulator")
        self.adb = self._tool("platform-tools/adb", default="adb")

        # 모든 하위 프로세스에 주입할 환경.
        self.env = os.environ.copy()
        self.env["ANDROID_AVD_HOME"] = str(self.avd_home)
        self.env["ANDROID_SDK_ROOT"] = str(self.sdk_root)
        self.env["ANDROID_HOME"] = str(self.sdk_root)

    # ------------------------------------------------------------------ #
    # 경로 탐색
    # ------------------------------------------------------------------ #
    def _find_sdk_root(self) -> Path:
        env = os.getenv("ANDROID_SDK_ROOT") or os.getenv("ANDROID_HOME")
        if env:
            return Path(env).expanduser()
        for c in (
            Path.home() / "Android" / "Sdk",
            Path.home() / "Library" / "Android" / "sdk",
            Path("/opt/android-sdk"),
        ):
            if c.exists():
                return c
        raise AVDError(
            "Android SDK 를 찾을 수 없습니다. ANDROID_SDK_ROOT 를 설정하거나 "
            "--sdk-root 옵션을 사용하세요."
        )

    def _tool(self, *relative: str, default: Optional[str] = None) -> str:
        for rel in relative:
            p = self.sdk_root / rel
            if p.exists():
                return str(p)
        return default or relative[0]

    # ------------------------------------------------------------------ #
    # subprocess helpers
    # ------------------------------------------------------------------ #
    def _run(
        self,
        cmd: list[str],
        *,
        timeout: int = 120,
        text_input: Optional[str] = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd,
            input=text_input,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=self.env,
            check=check,
        )

    def _adb(self, *args: str, serial: Optional[str] = None, timeout: int = 30) -> subprocess.CompletedProcess:
        cmd = [self.adb]
        if serial:
            cmd += ["-s", serial]
        cmd += list(args)
        return self._run(cmd, timeout=timeout)

    # ------------------------------------------------------------------ #
    # 시스템 이미지 / AVD 생성
    # ------------------------------------------------------------------ #
    def system_image_package(self, api_level: int) -> str:
        return f"system-images;android-{api_level};google_apis_playstore;x86_64"

    def install_system_image(self, api_level: int = 34) -> bool:
        pkg = self.system_image_package(api_level)
        print(f"📥 시스템 이미지 설치: {pkg}")
        proc = self._run([self.sdkmanager, pkg], timeout=1800, text_input="y\n")
        if proc.returncode == 0:
            print("✓ 시스템 이미지 준비 완료")
            return True
        print(f"❌ 설치 실패:\n{proc.stderr.strip()}")
        return False

    def create_avd(
        self,
        avd_name: str,
        *,
        api_level: int = 34,
        device_model: str = "pixel_6_pro",
        ram: int = 2048,
        sdcard: int = 4096,
        fingerprint: Optional[Fingerprint] = None,
        force: bool = False,
    ) -> bool:
        """AVD 를 생성한다. ANDROID_AVD_HOME 덕분에 self.avd_home 아래에 저장된다."""
        pkg = self.system_image_package(api_level)
        cmd = [
            self.avdmanager,
            "create",
            "avd",
            "-n",
            avd_name,
            "-k",
            pkg,
            "-d",
            device_model,
        ]
        if sdcard:
            cmd += ["-c", f"{sdcard}M"]
        if force:
            cmd += ["--force"]

        proc = self._run(cmd, timeout=300, text_input="no\n")
        if proc.returncode != 0:
            print(f"❌ {avd_name} 생성 실패:\n{proc.stderr.strip()}")
            return False

        self._patch_config_ini(avd_name, ram=ram, fingerprint=fingerprint)
        return True

    def _patch_config_ini(
        self, avd_name: str, *, ram: int, fingerprint: Optional[Fingerprint]
    ) -> None:
        """config.ini 에 RAM/MAC 등 avdmanager 로 지정 못 하는 값을 기록."""
        cfg = self.avd_home / f"{avd_name}.avd" / "config.ini"
        if not cfg.exists():
            return
        lines = cfg.read_text().splitlines()
        overrides = {
            "hw.ramSize": str(ram),
            "hw.keyboard": "yes",
        }
        if fingerprint:
            overrides["hw.qemu.wifi.mac"] = fingerprint.wifi_mac
            overrides["hw.qemu.nic0.mac"] = fingerprint.wifi_mac

        kept = [ln for ln in lines if ln.split("=", 1)[0].strip() not in overrides]
        kept += [f"{k}={v}" for k, v in overrides.items()]
        cfg.write_text("\n".join(kept) + "\n")

    # ------------------------------------------------------------------ #
    # 부팅 / 종료
    # ------------------------------------------------------------------ #
    def boot_avd(
        self,
        avd_name: str,
        *,
        port: int = 5554,
        headless: bool = True,
        http_proxy: Optional[str] = None,
        fingerprint: Optional[Fingerprint] = None,
        wait: bool = True,
        boot_timeout: int = 180,
    ) -> subprocess.Popen:
        """AVD 부팅. fingerprint 가 주어지면 -prop 으로 식별자를 주입한다."""
        serial = f"emulator-{port}"
        cmd = [self.emulator, "-avd", avd_name, "-port", str(port), "-no-boot-anim"]
        if headless:
            cmd += ["-no-window", "-no-audio"]
        if http_proxy:
            cmd += ["-http-proxy", http_proxy]
        if fingerprint:
            cmd += fingerprint.boot_props()

        print(f"🚀 {avd_name} 부팅 (serial={serial})")
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.env)

        if wait:
            self._wait_boot_complete(serial, boot_timeout)
            print(f"✓ {avd_name} 부팅 완료")
        return proc

    def _wait_boot_complete(self, serial: str, timeout: int) -> None:
        self._adb("wait-for-device", serial=serial, timeout=timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self._adb("shell", "getprop", "sys.boot_completed", serial=serial, timeout=10)
            if r.stdout.strip() == "1":
                return
            time.sleep(2)
        raise AVDError(f"{serial} 부팅 타임아웃({timeout}s)")

    def shutdown(self, serial: str = "emulator-5554") -> None:
        self._adb("emu", "kill", serial=serial, timeout=15)
        time.sleep(2)

    # ------------------------------------------------------------------ #
    # fingerprint (런타임 적용분)
    # ------------------------------------------------------------------ #
    def apply_runtime_fingerprint(self, fingerprint: Fingerprint, serial: str = "emulator-5554") -> None:
        """부팅 후 런타임에 바꿀 수 있는 식별자 적용.

        android_id 는 google_apis_playstore 이미지에서 rooted 가 아니면 변경이
        제한될 수 있다. 실패해도 예외를 던지지 않고 경고만 남긴다.
        """
        self._adb("root", serial=serial, timeout=15)
        time.sleep(2)
        r = self._adb(
            "shell", "settings", "put", "secure", "android_id", fingerprint.android_id,
            serial=serial,
        )
        if r.returncode == 0:
            print(f"  android_id = {fingerprint.android_id}")
        else:
            print(f"  ⚠️ android_id 설정 실패(비루팅 이미지일 수 있음): {r.stderr.strip()}")

    def verify_fingerprint(self, serial: str = "emulator-5554") -> dict[str, str]:
        props = {
            "ro.serialno": "",
            "ril.gsm.imei": "",
            "ro.build.fingerprint": "",
        }
        for key in props:
            r = self._adb("shell", "getprop", key, serial=serial, timeout=10)
            props[key] = r.stdout.strip()
        aid = self._adb("shell", "settings", "get", "secure", "android_id", serial=serial, timeout=10)
        props["android_id"] = aid.stdout.strip()
        return props

    # ------------------------------------------------------------------ #
    # 스냅샷
    # ------------------------------------------------------------------ #
    def save_snapshot(self, name: str, serial: str = "emulator-5554") -> bool:
        r = self._adb("emu", "avd", "snapshot", "save", name, serial=serial, timeout=120)
        ok = r.returncode == 0
        print(("✓ " if ok else "⚠️ ") + f"스냅샷 저장: {name}")
        return ok

    def load_snapshot(self, name: str, serial: str = "emulator-5554") -> bool:
        r = self._adb("emu", "avd", "snapshot", "load", name, serial=serial, timeout=120)
        return r.returncode == 0

    # ------------------------------------------------------------------ #
    # 조회 / 삭제
    # ------------------------------------------------------------------ #
    def list_avds(self) -> list[str]:
        r = self._run([self.avdmanager, "list", "avd", "-c"], timeout=60)
        if r.returncode != 0:
            # -c(compact) 미지원 버전 대비 파싱 폴백
            r2 = self._run([self.avdmanager, "list", "avd"], timeout=60)
            return re.findall(r"Name:\s*(\S+)", r2.stdout)
        return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]

    def delete_avd(self, avd_name: str) -> bool:
        r = self._run([self.avdmanager, "delete", "avd", "-n", avd_name], timeout=60)
        return r.returncode == 0


if __name__ == "__main__":
    mgr = AVDManager(Path.home() / ".android" / "avd")
    print("SDK  :", mgr.sdk_root)
    print("AVDs :", mgr.avd_home)
    print("list :", mgr.list_avds())
