"""YouTube 앱 UI 자동화 프로토타입 (Phase 1).

uiautomator2 로 에뮬레이터의 YouTube 앱을 조작한다. UI 리소스 ID/텍스트는
앱 버전에 따라 달라지므로 SELECTORS 한 곳에 모아두었다. 실제 기기에서
`uiautomator2` 의 weditor 또는 `d.dump_hierarchy()` 로 확인 후 보정한다.

connect()/open_youtube() 는 실제 동작하며, upload_short() 의 개별 탭 좌표는
플레이스홀더이므로 첫 실 기기 테스트에서 셀렉터를 채워야 한다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

try:  # CLI 는 uiautomator2 미설치 상태에서도 동작해야 하므로 지연/보호 임포트
    import uiautomator2 as u2
except Exception:  # pragma: no cover - 선택적 의존성
    u2 = None

YOUTUBE_PACKAGE = "com.google.android.youtube"

# 앱 버전별로 보정 대상. resourceId 우선, 없으면 text/description 폴백.
SELECTORS = {
    "account_avatar": {"resourceId": f"{YOUTUBE_PACKAGE}:id/image"},
    "create_tab": {"description": "만들기"},
    "create_short": {"text": "Shorts 동영상"},
    "gallery_upload": {"description": "동영상 추가"},
    "next_button": {"text": "다음"},
    "title_field": {"resourceId": f"{YOUTUBE_PACKAGE}:id/title_edit"},
    "description_field": {"resourceId": f"{YOUTUBE_PACKAGE}:id/description_edit"},
    "visibility_selector": {"text": "공개 상태"},
    "upload_button": {"text": "업로드"},
}


class AutomationError(RuntimeError):
    pass


@dataclass
class UploadResult:
    channel_id: int
    success: bool
    message: str = ""


class YouTubeAutomation:
    """단일 에뮬레이터에 대한 YouTube 자동화 세션."""

    def __init__(self, serial: str = "emulator-5554") -> None:
        if u2 is None:
            raise AutomationError(
                "uiautomator2 가 설치되어 있지 않습니다. `pip install uiautomator2` 후 사용하세요."
            )
        self.serial = serial
        self.d = u2.connect(serial)

    # ------------------------------------------------------------------ #
    def open_youtube(self, wait: float = 5.0) -> None:
        self.d.app_start(YOUTUBE_PACKAGE, stop=True)
        time.sleep(wait)

    def is_logged_in(self, timeout: float = 8.0) -> bool:
        """계정 아바타 존재 여부로 로그인 상태를 추정."""
        avatar = self.d(**SELECTORS["account_avatar"])
        return bool(avatar.wait(timeout=timeout))

    def push_video(self, local_path: str, remote_dir: str = "/sdcard/Movies") -> str:
        """로컬 영상을 에뮬레이터로 push 하고 미디어 스캔을 트리거."""
        import os

        remote_path = f"{remote_dir}/{os.path.basename(local_path)}"
        self.d.push(local_path, remote_path)
        self.d.shell(
            f'am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE '
            f'-d file://{remote_path}'
        )
        return remote_path

    def upload_short(
        self,
        *,
        channel_id: int,
        video_local_path: str,
        title: str,
        description: str = "",
        visibility: str = "PRIVATE",
        scheduled_time: Optional[str] = None,
    ) -> UploadResult:
        """쇼츠 업로드 플로우.

        영상 push → 만들기 → Shorts → 갤러리 선택 → 편집/다음 →
        제목·설명 입력 → 공개범위 설정 → 업로드.

        NOTE: 아래 각 단계의 셀렉터는 실 기기에서 보정 필요.
        """
        try:
            self.push_video(video_local_path)
            self.open_youtube()

            if not self.is_logged_in():
                return UploadResult(channel_id, False, "로그인 상태가 아님")

            self._tap("create_tab")
            self._tap("create_short")
            self._tap("gallery_upload")
            # 갤러리에서 방금 push 한 영상 선택 (가장 최근 항목 가정)
            self._tap("next_button")

            self._set_text("title_field", title)
            if description:
                self._set_text("description_field", description)

            self._set_visibility(visibility, scheduled_time)

            self._tap("upload_button")
            self._wait_upload_complete()
            return UploadResult(channel_id, True, "업로드 요청 완료")

        except Exception as exc:  # noqa: BLE001 - 상위로 결과 객체로 전달
            return UploadResult(channel_id, False, f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ #
    # 내부 헬퍼
    # ------------------------------------------------------------------ #
    def _tap(self, key: str, timeout: float = 15.0) -> None:
        el = self.d(**SELECTORS[key])
        if not el.wait(timeout=timeout):
            raise AutomationError(f"UI 요소를 찾지 못함: {key} ({SELECTORS[key]})")
        el.click()

    def _set_text(self, key: str, value: str, timeout: float = 15.0) -> None:
        el = self.d(**SELECTORS[key])
        if not el.wait(timeout=timeout):
            raise AutomationError(f"입력 필드를 찾지 못함: {key}")
        el.click()
        el.clear_text()
        el.set_text(value)

    def _set_visibility(self, visibility: str, scheduled_time: Optional[str]) -> None:
        self._tap("visibility_selector")
        label = {"PRIVATE": "비공개", "UNLISTED": "일부 공개", "PUBLIC": "공개"}.get(
            visibility.upper(), "비공개"
        )
        if scheduled_time:
            # 예약 발행 UI 는 앱 버전에 따라 별도 플로우 → Phase 2 에서 확장
            label = "예약"
        opt = self.d(text=label)
        if opt.wait(timeout=8):
            opt.click()

    def _wait_upload_complete(self, timeout: float = 600.0) -> None:
        """업로드 진행 표시가 사라질 때까지 대기. 셀렉터는 실 기기에서 보정."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            progress = self.d(textContains="업로드 중")
            if not progress.exists:
                return
            time.sleep(5)
        raise AutomationError("업로드 완료 감지 타임아웃")
