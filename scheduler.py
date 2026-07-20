"""업로드 스케줄러 (Phase 3).

채널을 순차 처리한다. 각 채널마다:
  1. 랜덤 대기 (업로드 시간 랜덤화)
  2. 고정 프록시 + fingerprint 로 AVD 부팅
  3. 스냅샷 복원 (로그인 상태)
  4. 영상 전처리 (BGM 삽입)
  5. 자동화로 업로드
  6. AVD 종료
실패 시 지수 백오프로 재시도한다.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from avd_manager import AVDError, AVDManager, Fingerprint
from config_manager import Upload
from network_manager import NetworkManager
from video_processor import VideoError, VideoProcessor


@dataclass
class JobResult:
    channel_id: int
    title: str
    success: bool
    message: str = ""
    attempts: int = 0


@dataclass
class SchedulerOptions:
    randomize: bool = True
    min_delay: int = 60
    max_delay: int = 300
    max_retries: int = 3
    backoff_base: int = 5
    base_port: int = 5554
    bgm_volume: float = 0.3
    strict_video: bool = False


class UploadScheduler:
    def __init__(
        self,
        avd_manager: AVDManager,
        network_manager: NetworkManager,
        video_processor: VideoProcessor,
        options: Optional[SchedulerOptions] = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        automation_factory: Optional[Callable[[str], object]] = None,
    ) -> None:
        self.avd = avd_manager
        self.net = network_manager
        self.vp = video_processor
        self.opt = options or SchedulerOptions()
        self._sleep = sleep
        # 지연 임포트/주입 가능: 테스트에서 가짜 자동화 주입
        self._automation_factory = automation_factory

    # ------------------------------------------------------------------ #
    def _make_automation(self, serial: str):
        if self._automation_factory:
            return self._automation_factory(serial)
        from automation import YouTubeAutomation

        return YouTubeAutomation(serial=serial)

    def random_delay(self) -> float:
        if not self.opt.randomize:
            return 0.0
        return float(random.randint(self.opt.min_delay, self.opt.max_delay))

    def backoff_delay(self, attempt: int) -> float:
        """attempt(1-based) 지수 백오프: base * 2^(attempt-1)."""
        return float(self.opt.backoff_base * (2 ** (attempt - 1)))

    # ------------------------------------------------------------------ #
    def run(self, uploads: list[Upload]) -> list[JobResult]:
        results: list[JobResult] = []
        for up in uploads:
            delay = self.random_delay()
            if delay:
                print(f"⏳ ch{up.channel_id} 업로드 전 {delay:.0f}s 대기")
                self._sleep(delay)
            results.append(self._run_one(up))
        return results

    def _run_one(self, up: Upload) -> JobResult:
        last_msg = ""
        for attempt in range(1, self.opt.max_retries + 1):
            try:
                msg = self._attempt(up)
                return JobResult(up.channel_id, up.title, True, msg, attempt)
            except (AVDError, VideoError, RuntimeError) as e:
                last_msg = f"{type(e).__name__}: {e}"
                print(f"  ⚠️ 시도 {attempt}/{self.opt.max_retries} 실패: {last_msg}")
                if attempt < self.opt.max_retries:
                    wait = self.backoff_delay(attempt)
                    print(f"  ↻ {wait:.0f}s 후 재시도")
                    self._sleep(wait)
        return JobResult(up.channel_id, up.title, False, last_msg, self.opt.max_retries)

    def _attempt(self, up: Upload) -> str:
        name = f"avd_ch{up.channel_id:02d}"
        serial = f"emulator-{self.opt.base_port}"
        fp = Fingerprint.for_device(up.channel_id)
        proxy_arg = self.net.emulator_arg_for(up.channel_id)

        # 4) 영상 전처리 (부팅과 무관하므로 먼저 처리)
        processed = self.vp.process(
            up.video_file,
            bgm=up.bgm_file,
            bgm_volume=self.opt.bgm_volume,
            strict=self.opt.strict_video,
        )

        proc = self.avd.boot_avd(name, port=self.opt.base_port, http_proxy=proxy_arg, fingerprint=fp)
        try:
            self.avd.load_snapshot(f"channel_{up.channel_id}", serial=serial)
            yt = self._make_automation(serial)
            result = yt.upload_short(
                channel_id=up.channel_id,
                video_local_path=processed,
                title=up.title,
                description=up.description,
                visibility=up.visibility,
                scheduled_time=up.scheduled_time,
            )
            if not result.success:
                raise RuntimeError(result.message)
            return result.message
        finally:
            self.avd.shutdown(serial=serial)
            if proc.poll() is None:
                try:
                    proc.wait(timeout=30)
                except Exception:
                    pass
