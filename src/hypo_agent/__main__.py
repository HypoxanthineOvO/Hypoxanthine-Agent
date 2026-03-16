from __future__ import annotations

import argparse

from hypo_agent.gateway.main import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Hypo-Agent")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
