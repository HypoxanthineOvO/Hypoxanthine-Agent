from pathlib import Path
import re


def test_nginx_ws_proxy_has_upgrade_headers() -> None:
    content = Path("deploy/nginx/hypo-agent.conf").read_text(encoding="utf-8")
    assert "location /ws" in content
    assert "proxy_pass http://127.0.0.1:8765/ws;" in content
    assert "proxy_http_version 1.1;" in content
    assert "proxy_set_header Upgrade $http_upgrade;" in content
    assert 'proxy_set_header Connection "upgrade";' in content
    assert "proxy_set_header Host $host;" in content
    assert "proxy_read_timeout 86400;" in content


def test_nginx_spa_and_api_proxy_exist() -> None:
    content = Path("deploy/nginx/hypo-agent.conf").read_text(encoding="utf-8")
    assert re.search(r"listen\s+\d+;", content)
    assert "listen 80;" not in content
    assert "server_name _;" in content
    assert "root /home/heyx/Hypo-Agent/web/dist;" in content
    assert "try_files $uri $uri/ /index.html;" in content
    assert "location /api/" in content
    assert "proxy_pass http://127.0.0.1:8765;" in content
    assert "proxy_set_header Host $host;" in content
