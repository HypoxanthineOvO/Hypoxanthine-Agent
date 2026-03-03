from pathlib import Path


def test_nginx_ws_proxy_has_upgrade_headers() -> None:
    content = Path("deploy/nginx/hypo-agent.conf").read_text(encoding="utf-8")
    assert "location /ws" in content
    assert "proxy_http_version 1.1;" in content
    assert "proxy_set_header Upgrade $http_upgrade;" in content
    assert 'proxy_set_header Connection "upgrade";' in content
