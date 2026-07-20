"""영상 전처리 모듈 (Phase 2).

- ffprobe 로 사양 검증 (쇼츠: 세로 영상, 60초 이하)
- ffmpeg 로 BGM 삽입 (원본 오디오와 믹스 또는 교체)
- 썸네일 생성

ffmpeg/ffprobe 가 PATH 또는 SDK 외부에 있어야 한다. 명령 생성 로직
(build_*_cmd)은 실행과 분리해 테스트 가능하게 했다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

MAX_SHORTS_SECONDS = 60.0


class VideoError(RuntimeError):
    pass


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    duration: float
    has_audio: bool

    @property
    def is_vertical(self) -> bool:
        return self.height >= self.width

    @property
    def within_shorts_length(self) -> bool:
        return self.duration <= MAX_SHORTS_SECONDS


class VideoProcessor:
    def __init__(
        self,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        temp_dir: str = "temp",
    ) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def _require_tools(self) -> None:
        if shutil.which(self.ffmpeg) is None or shutil.which(self.ffprobe) is None:
            raise VideoError(
                "ffmpeg/ffprobe 를 찾을 수 없습니다. 설치 후 PATH 에 추가하세요."
            )

    # ------------------------------------------------------------------ #
    # 검증
    # ------------------------------------------------------------------ #
    def probe(self, path: str) -> VideoInfo:
        self._require_tools()
        cmd = [
            self.ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise VideoError(f"ffprobe 실패: {proc.stderr.strip()}")
        data = json.loads(proc.stdout)
        return self._parse_probe(path, data)

    @staticmethod
    def _parse_probe(path: str, data: dict) -> VideoInfo:
        """ffprobe JSON → VideoInfo. (실행과 분리되어 단위 테스트 가능)"""
        width = height = 0
        has_audio = False
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and not width:
                width = int(s.get("width", 0))
                height = int(s.get("height", 0))
            elif s.get("codec_type") == "audio":
                has_audio = True
        duration = float(data.get("format", {}).get("duration", 0.0))
        return VideoInfo(path, width, height, duration, has_audio)

    def validate_shorts(self, info: VideoInfo) -> list[str]:
        """문제 목록 반환. 비어 있으면 통과."""
        issues: list[str] = []
        if not info.is_vertical:
            issues.append(
                f"세로 영상이 아님 ({info.width}x{info.height}, 쇼츠는 9:16 권장)"
            )
        if not info.within_shorts_length:
            issues.append(f"길이 초과 ({info.duration:.1f}s > {MAX_SHORTS_SECONDS}s)")
        return issues

    # ------------------------------------------------------------------ #
    # BGM 삽입
    # ------------------------------------------------------------------ #
    def build_bgm_cmd(
        self,
        video: str,
        bgm: str,
        output: str,
        *,
        volume: float = 0.3,
        mix_with_original: bool = True,
        has_original_audio: bool = True,
    ) -> list[str]:
        """BGM 삽입 ffmpeg 명령 생성. bgm 을 영상 길이에 맞춰 반복(-stream_loop)."""
        cmd = [self.ffmpeg, "-y", "-i", video, "-stream_loop", "-1", "-i", bgm]
        if mix_with_original and has_original_audio:
            cmd += [
                "-filter_complex",
                f"[1:a]volume={volume}[b];[0:a][b]amix=inputs=2:duration=first[a]",
                "-map",
                "0:v",
                "-map",
                "[a]",
            ]
        else:
            # 원본 오디오 없음 또는 교체 모드: BGM 을 그대로 사용
            cmd += ["-filter_complex", f"[1:a]volume={volume}[a]", "-map", "0:v", "-map", "[a]"]
        cmd += ["-c:v", "copy", "-c:a", "aac", "-shortest", output]
        return cmd

    def add_bgm(
        self,
        video: str,
        bgm: str,
        *,
        volume: float = 0.3,
        mix_with_original: bool = True,
        output: Optional[str] = None,
    ) -> str:
        self._require_tools()
        info = self.probe(video)
        out = output or str(self.temp_dir / f"{Path(video).stem}_bgm.mp4")
        cmd = self.build_bgm_cmd(
            video,
            bgm,
            out,
            volume=volume,
            mix_with_original=mix_with_original,
            has_original_audio=info.has_audio,
        )
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise VideoError(f"BGM 삽입 실패: {proc.stderr.strip()[-500:]}")
        return out

    # ------------------------------------------------------------------ #
    # 썸네일
    # ------------------------------------------------------------------ #
    def build_thumbnail_cmd(self, video: str, output: str, at_seconds: float = 1.0) -> list[str]:
        return [
            self.ffmpeg,
            "-y",
            "-ss",
            str(at_seconds),
            "-i",
            video,
            "-frames:v",
            "1",
            output,
        ]

    def make_thumbnail(self, video: str, at_seconds: float = 1.0, output: Optional[str] = None) -> str:
        self._require_tools()
        out = output or str(self.temp_dir / f"{Path(video).stem}_thumb.jpg")
        cmd = self.build_thumbnail_cmd(video, out, at_seconds)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise VideoError(f"썸네일 생성 실패: {proc.stderr.strip()}")
        return out

    # ------------------------------------------------------------------ #
    # 통합 처리
    # ------------------------------------------------------------------ #
    def process(
        self,
        video: str,
        *,
        bgm: Optional[str] = None,
        bgm_volume: float = 0.3,
        strict: bool = False,
    ) -> str:
        """검증 후 필요 시 BGM 을 삽입한 최종 로컬 경로 반환.

        strict=True 면 쇼츠 사양 위반 시 예외. 아니면 경고만 출력.
        """
        if not Path(video).exists():
            raise VideoError(f"영상 파일이 없습니다: {video}")

        info = self.probe(video)
        issues = self.validate_shorts(info)
        if issues:
            msg = "; ".join(issues)
            if strict:
                raise VideoError(f"쇼츠 사양 위반: {msg}")
            print(f"  ⚠️ 사양 경고: {msg}")

        if bgm:
            return self.add_bgm(video, bgm, volume=bgm_volume)
        return video
