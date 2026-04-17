from __future__ import annotations

from hypo_agent.skills.auth.types import PlaywrightPlatformConfig


PLAYWRIGHT_PLATFORM_CONFIGS: dict[str, PlaywrightPlatformConfig] = {
    "weread": PlaywrightPlatformConfig(
        platform="weread",
        login_url="https://weread.qq.com/",
        entry_actions=[{"kind": "click_text", "text": "登录", "exact": True}],
        qr_targets=[
            {"kind": "locator", "selector": ".wr_login_modal_qr_img"},
            {"kind": "locator", "selector": "iframe[src*='open.weixin.qq.com/connect/qrconnect']"},
        ],
        success_cookies=[],
        cookie_domains=[".weread.qq.com", ".qq.com"],
        risk_texts=["安全验证", "访问受限", "环境异常"],
        qr_wait_seconds=30,
        login_wait_seconds=60,
    ),
    "zhihu": PlaywrightPlatformConfig(
        platform="zhihu",
        login_url="https://www.zhihu.com/signin",
        entry_actions=[],
        qr_targets=[
            {"kind": "locator", "selector": "img[src*='qrcode']"},
            {"kind": "locator", "selector": "canvas"},
        ],
        success_cookies=["z_c0", "_xsrf", "SESSIONID"],
        cookie_domains=[".zhihu.com", "www.zhihu.com"],
        risk_texts=["安全验证", "环境异常", "开始验证"],
        qr_wait_seconds=30,
        login_wait_seconds=60,
    ),
    "douban": PlaywrightPlatformConfig(
        platform="douban",
        login_url="https://www.douban.com/",
        entry_actions=[],
        qr_targets=[
            {"kind": "locator", "selector": ".account-form .qrcode"},
            {"kind": "locator", "selector": "img[src*='qrcode']"},
        ],
        success_cookies=["dbcl2"],
        cookie_domains=[".douban.com"],
        risk_texts=["验证", "异常", "限制"],
        qr_wait_seconds=30,
        login_wait_seconds=60,
    ),
}
