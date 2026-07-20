"""설정 관리 모듈.

- 전역 설정(settings.yaml), 채널(channels.yaml), 업로드 배치(upload_batch.yaml) 로드/검증
- AVD 저장 경로 우선순위 결정: CLI > 환경변수(ANDROID_AVD_HOME) > settings.yaml > 기본값
- 계정 비밀번호 대칭키 암호화 (키는 config/.secret.key 에 1회 생성 후 재사용)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from cryptography.fernet import Fernet
from pydantic import BaseModel, Field, field_validator

DEFAULT_AVD_HOME = Path.home() / ".android" / "avd"


class ProxySettings(BaseModel):
    host: str
    port: int
    country: Optional[str] = None

    def as_emulator_arg(self) -> str:
        """emulator -http-proxy 에 넘길 host:port 문자열."""
        return f"{self.host}:{self.port}"


class GlobalSettings(BaseModel):
    """settings.yaml 매핑."""

    avd_home: str = str(DEFAULT_AVD_HOME)
    android_sdk_root: Optional[str] = None
    log_dir: str = "logs"
    temp_dir: str = "temp"
    config_dir: str = "config"
    avd_api_level: int = 34
    avd_device_model: str = "Pixel 6 Pro"
    avd_ram: int = 2048
    avd_storage: int = 4096
    diverse_models: bool = False

    @field_validator("avd_home", "log_dir", "temp_dir", "config_dir")
    @classmethod
    def _expand(cls, v: str) -> str:
        return str(Path(v).expanduser())


class Channel(BaseModel):
    id: int
    email: str
    password: str  # 암호화된 토큰 (encrypt_password 로 생성)
    avd_device: str
    proxy: Optional[ProxySettings] = None


class ChannelsConfig(BaseModel):
    channels: list[Channel] = Field(default_factory=list)


class Upload(BaseModel):
    channel_id: int
    video_file: str
    title: str
    description: str = ""
    bgm_file: Optional[str] = None
    visibility: str = "PRIVATE"
    scheduled_time: Optional[str] = None

    @field_validator("visibility")
    @classmethod
    def _check_visibility(cls, v: str) -> str:
        allowed = {"PRIVATE", "UNLISTED", "PUBLIC"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"visibility must be one of {allowed}, got {v!r}")
        return upper


class UploadBatchConfig(BaseModel):
    uploads: list[Upload] = Field(default_factory=list)


class ConfigManager:
    """설정 파일 로드/저장 및 경로 결정을 담당."""

    def __init__(
        self,
        config_dir: str = "config",
        avd_home_cli: Optional[str] = None,
    ) -> None:
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.settings = self._load_settings()
        self.avd_home = self._resolve_avd_home(avd_home_cli)

    # ------------------------------------------------------------------ #
    # settings.yaml
    # ------------------------------------------------------------------ #
    def _settings_path(self) -> Path:
        return self.config_dir / "settings.yaml"

    def _load_settings(self) -> GlobalSettings:
        path = self._settings_path()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return GlobalSettings(**data)
        settings = GlobalSettings()
        self._save_settings(settings)
        return settings

    def _save_settings(self, settings: GlobalSettings) -> None:
        with open(self._settings_path(), "w", encoding="utf-8") as f:
            yaml.safe_dump(
                settings.model_dump(), f, default_flow_style=False, allow_unicode=True
            )

    def save_settings(self) -> None:
        self._save_settings(self.settings)

    # ------------------------------------------------------------------ #
    # AVD 경로 우선순위
    # ------------------------------------------------------------------ #
    def _resolve_avd_home(self, cli_path: Optional[str]) -> Path:
        """CLI > 환경변수 > settings.yaml > 기본값 순으로 AVD 경로 결정."""
        if cli_path:
            return Path(cli_path).expanduser()

        env_path = os.getenv("ANDROID_AVD_HOME")
        if env_path:
            return Path(env_path).expanduser()

        if self.settings.avd_home:
            return Path(self.settings.avd_home).expanduser()

        return DEFAULT_AVD_HOME

    def get_avd_home(self) -> Path:
        return self.avd_home

    def ensure_avd_home(self) -> Path:
        self.avd_home.mkdir(parents=True, exist_ok=True)
        return self.avd_home

    # ------------------------------------------------------------------ #
    # channels.yaml / upload_batch.yaml
    # ------------------------------------------------------------------ #
    def load_channels(self) -> ChannelsConfig:
        path = self.config_dir / "channels.yaml"
        if not path.exists():
            raise FileNotFoundError(f"채널 설정 파일이 없습니다: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return ChannelsConfig(**data)

    def save_channels(self, config: ChannelsConfig) -> None:
        path = self.config_dir / "channels.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                config.model_dump(), f, default_flow_style=False, allow_unicode=True
            )

    def load_upload_batch(
        self, path: Optional[str] = None
    ) -> UploadBatchConfig:
        p = Path(path) if path else self.config_dir / "upload_batch.yaml"
        if not p.exists():
            raise FileNotFoundError(f"업로드 배치 파일이 없습니다: {p}")
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return UploadBatchConfig(**data)

    # ------------------------------------------------------------------ #
    # 암호화 (계정 비밀번호)
    # ------------------------------------------------------------------ #
    def _key_path(self) -> Path:
        return self.config_dir / ".secret.key"

    def _get_or_create_key(self) -> bytes:
        path = self._key_path()
        if path.exists():
            return path.read_bytes()
        key = Fernet.generate_key()
        path.write_bytes(key)
        os.chmod(path, 0o600)
        return key

    def encrypt_password(self, plaintext: str) -> str:
        cipher = Fernet(self._get_or_create_key())
        return cipher.encrypt(plaintext.encode()).decode()

    def decrypt_password(self, token: str) -> str:
        cipher = Fernet(self._get_or_create_key())
        return cipher.decrypt(token.encode()).decode()

    # ------------------------------------------------------------------ #
    def summary(self) -> dict[str, Any]:
        return {
            "config_dir": str(self.config_dir.resolve()),
            "avd_home": str(self.avd_home),
            "sdk_root": self.settings.android_sdk_root,
            "api_level": self.settings.avd_api_level,
            "device_model": self.settings.avd_device_model,
            "diverse_models": self.settings.diverse_models,
        }


if __name__ == "__main__":
    cm = ConfigManager()
    for k, v in cm.summary().items():
        print(f"{k:>15}: {v}")
