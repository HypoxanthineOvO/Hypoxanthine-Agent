from __future__ import annotations

from typing import Any

from hypo_agent.skills.auth.providers.bilibili import BilibiliProvider
from hypo_agent.skills.auth.providers.playwright_qr import PlaywrightQrProvider
from hypo_agent.skills.auth.providers.weibo import WeiboProvider
from hypo_agent.skills.auth.providers.wewe_rss import WeWeRSSProvider
from hypo_agent.skills.auth.providers.zhihu import ZhihuProvider


def build_auth_registry(*, http_transport: Any | None = None) -> dict[str, Any]:
    return {
        "bilibili": BilibiliProvider(http_transport=http_transport),
        "wewe_rss": WeWeRSSProvider(),
        "weibo": WeiboProvider(http_transport=http_transport),
        "zhihu": ZhihuProvider(http_transport=http_transport),
        "weread": PlaywrightQrProvider(platform="weread"),
    }
