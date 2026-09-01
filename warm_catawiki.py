#!/usr/bin/env python3
"""Solo warm-up Catawiki: tempo per completare Akamai senza fare tutto il monitor."""

from dotenv import load_dotenv

from http_fetch import open_fetcher


def main() -> None:
    load_dotenv()
    url = "https://www.catawiki.com/it/"
    print("Warm-up Catawiki (cookie in .playwright-profile/)")
    with open_fetcher() as fetcher:
        fetcher.warm(url)
    print("Fine warm-up. Se vedi 'Warm-up OK', lancia: python monitor_catawiki.py")


if __name__ == "__main__":
    main()
