#!/usr/bin/env python3
"""Check live download CORS without credentials or downloading an object."""

import sys
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ORIGIN = "https://cors-check.pages.onticlabs.io"
HEADERS = {"range", "content-type", "x-requested-with"}


def check(endpoint, bucket):
    url = f"{endpoint.rstrip('/')}/{quote(bucket, safe='')}/_ontic_cors_probe"
    for method in ("GET", "HEAD"):
        request = Request(
            url,
            method="OPTIONS",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": ", ".join(sorted(HEADERS)),
            },
        )
        with urlopen(request, timeout=15) as response:
            headers = response.headers
            allowed = {
                value.strip().lower()
                for value in headers.get("Access-Control-Allow-Headers", "").split(",")
            }
            methods = {
                value.strip().upper()
                for value in headers.get("Access-Control-Allow-Methods", "").split(",")
            }
            if (
                not 200 <= response.status < 300
                or headers.get("Access-Control-Allow-Origin") not in (ORIGIN, "*")
                or method not in methods
                or not ("*" in allowed or HEADERS <= allowed)
            ):
                raise ValueError("download CORS preflight is incomplete")


if __name__ == "__main__":
    try:
        check(*sys.argv[1:])
    except (URLError, OSError, ValueError):
        sys.exit(
            "B2 download CORS check failed; apply the page-files rule in deploy/README.md "
            "before deploying, or set ONTIC_PAGES_DIRECT_FILES=0 for server delivery."
        )
    print("ok    live bucket CORS permits download preflights")
