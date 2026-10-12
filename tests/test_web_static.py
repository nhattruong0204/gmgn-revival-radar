import httpx

from revival_radar.web.app import create_app
from revival_radar.web.config import WebConfig


async def test_static_routes_keep_api_and_asset_errors_distinct(tmp_path):
    static = tmp_path / "site"
    static.mkdir()
    (static / "index.html").write_text("<html>radar application</html>")
    app = create_app(WebConfig(web_static_directory=static, database_path=tmp_path / "absent.db"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://radar.example"
    ) as client:
        for path in ("/", "/reports", "/tokens/sol/" + "A" * 32, "/admin/settings"):
            response = await client.get(path)
            assert response.status_code == 200
            assert "radar application" in response.text
            assert response.headers["x-frame-options"] == "DENY"
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        for path in ("/api", "/api/v1/missing", "/assets/missing.js"):
            response = await client.get(path)
            assert response.status_code == 404
            assert "radar application" not in response.text
        assert (await client.get("/api/v1/radar")).status_code == 503
    assert not (tmp_path / "absent.db").exists()
