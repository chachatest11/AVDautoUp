"""네트워크 분산 모듈 (Phase 3).

- 채널별 고정 프록시를 channels.yaml 또는 proxies.json 에서 로드
- 프록시 헬스체크 (선택)
- emulator -http-proxy 인자 생성

프록시는 각 AVD 에 부팅 시 고정 할당한다(업로드마다 바꾸지 않음).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:  # requests 는 헬스체크에만 필요 → 선택적
    import requests
except Exception:  # pragma: no cover
    requests = None

from config_manager import ChannelsConfig, ProxySettings


@dataclass
class Proxy:
    host: str
    port: int
    country: Optional[str] = None
    scheme: str = "http"

    @classmethod
    def from_settings(cls, p: ProxySettings) -> "Proxy":
        return cls(host=p.host, port=p.port, country=p.country)

    @property
    def emulator_arg(self) -> str:
        """emulator -http-proxy 값 (host:port)."""
        return f"{self.host}:{self.port}"

    @property
    def url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


class NetworkManager:
    def __init__(self) -> None:
        self._by_channel: dict[int, Proxy] = {}

    # ------------------------------------------------------------------ #
    # 로드
    # ------------------------------------------------------------------ #
    def load_from_channels(self, channels: ChannelsConfig) -> dict[int, Proxy]:
        """channels.yaml 의 각 채널 proxy 를 채널 ID 에 매핑."""
        for ch in channels.channels:
            if ch.proxy:
                self._by_channel[ch.id] = Proxy.from_settings(ch.proxy)
        return self._by_channel

    def load_pool_and_assign(self, pool_path: str, channel_ids: list[int]) -> dict[int, Proxy]:
        """proxies.json 풀을 채널에 순서대로 고정 할당."""
        data = json.loads(Path(pool_path).read_text(encoding="utf-8"))
        pool = [
            Proxy(host=d["host"], port=int(d["port"]), country=d.get("country"), scheme=d.get("scheme", "http"))
            for d in data
        ]
        if not pool:
            return self._by_channel
        for i, cid in enumerate(channel_ids):
            self._by_channel[cid] = pool[i % len(pool)]
        return self._by_channel

    # ------------------------------------------------------------------ #
    # 조회 / 검증
    # ------------------------------------------------------------------ #
    def proxy_for(self, channel_id: int) -> Optional[Proxy]:
        return self._by_channel.get(channel_id)

    def emulator_arg_for(self, channel_id: int) -> Optional[str]:
        p = self.proxy_for(channel_id)
        return p.emulator_arg if p else None

    def health_check(self, proxy: Proxy, test_url: str = "https://www.google.com/generate_204", timeout: int = 10) -> bool:
        """프록시를 통해 외부 접속이 되는지 확인."""
        if requests is None:
            raise RuntimeError("requests 미설치 — `pip install requests` 후 사용하세요.")
        proxies = {"http": proxy.url, "https": proxy.url}
        try:
            r = requests.get(test_url, proxies=proxies, timeout=timeout)
            return r.status_code < 400
        except Exception:
            return False

    def health_check_all(self, test_url: str = "https://www.google.com/generate_204") -> dict[int, bool]:
        return {cid: self.health_check(p, test_url) for cid, p in self._by_channel.items()}
