import argparse

import uvicorn

from revival_radar.web.app import create_app
from revival_radar.web.config import WebConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve saved Revival Radar website data")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    config = WebConfig()
    uvicorn.run(
        create_app(config), host=args.host or config.web_host, port=args.port or config.web_port
    )


if __name__ == "__main__":
    main()
