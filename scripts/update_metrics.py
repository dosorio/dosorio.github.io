#!/usr/bin/env python3
"""Refresh the profile metrics in index.html.

Each number on the page is marked with a data-metric="..." attribute. This script
recomputes or fetches every value and rewrites only those elements:

- Publication counts are recomputed from the publication list in index.html.
- CRAN downloads are summed from the CRAN logs API (cranlogs.r-pkg.org).
- Citations and h-index come from Google Scholar, through SerpAPI when the
  SERPAPI_KEY environment variable is set, otherwise by fetching the public
  profile page directly.

When a source cannot be reached, its values and its "as of" date are left
untouched, so the page never claims to be fresher than its data.
"""

import datetime
import json
import os
import re
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index.html")

SCHOLAR_USER = "uA4VdlIAAAAJ"
CRAN_PACKAGES = ["SCORPION", "retriever", "rPanglaoDB", "scTenifoldKnk", "scTenifoldNet",
                 "exp2flux", "g2f", "minval", "Peptides"]
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def month_label(today):
    return today.strftime("%B %Y")


def floor_label(value, step, suffix=""):
    """2241 with step 10 -> '2,240+'; 360271 with step 1000 and suffix 'K' -> '360K+'."""
    floored = value // step * step
    if suffix:
        return f"{floored // step:,}{suffix}+"
    return f"{floored:,}+"


def publication_counts(html):
    start = html.index('>Publications</h2>')
    section = html[start:]
    items = section.split('<div class="item">')[1:]
    headers = [item.split('<div class="ui right floated">')[0] for item in items]

    def count(marker):
        return sum(marker in h for h in headers)

    counts = {
        "publications": len(items),
        "preprints": count('class="file outline icon"'),
        "research": count('class="file icon"'),
        "reviews": count('class="folder icon"'),
        "conference": count('class="book icon"'),
        "commentaries": count('class="comment outline icon"'),
        "first-author": count('class="bookmark icon"'),
        "corresponding": count('class="envelope icon"'),
    }
    typed = sum(counts[k] for k in ("preprints", "research", "reviews", "conference", "commentaries"))
    if typed != counts["publications"]:
        raise ValueError(f"{counts['publications']} publications but {typed} have a type icon")
    counts["publications-list"] = counts["publications"]
    counts["legend-first"] = counts["first-author"]
    counts["legend-corresponding"] = counts["corresponding"]
    return {k: str(v) for k, v in counts.items()}


def cran_downloads(today):
    url = ("https://cranlogs.r-pkg.org/downloads/total/2000-01-01:"
           f"{today.isoformat()}/{','.join(CRAN_PACKAGES)}")
    data = json.loads(fetch(url))
    found = {d["package"]: d["downloads"] for d in data}
    missing = [p for p in CRAN_PACKAGES if p not in found]
    if missing:
        raise ValueError(f"CRAN logs returned no data for {missing}")
    total = sum(found.values())
    if total <= 0:
        raise ValueError("CRAN logs returned zero downloads")
    return total


def scholar_via_serpapi(key):
    query = urllib.parse.urlencode({"engine": "google_scholar_author", "author_id": SCHOLAR_USER,
                                    "hl": "en", "api_key": key})
    data = json.loads(fetch(f"https://serpapi.com/search.json?{query}"))
    if "error" in data:
        raise ValueError(f"SerpAPI error: {data['error']}")
    table = {k: v for row in data["cited_by"]["table"] for k, v in row.items()}
    return int(table["citations"]["all"]), int(table["h_index"]["all"])


def scholar_direct():
    html = fetch(f"https://scholar.google.com/citations?user={SCHOLAR_USER}&hl=en")
    cells = [c.replace(",", "") for c in re.findall(r'class="gsc_rsb_std">(\d[\d,]*)<', html)]
    if len(cells) < 6:
        raise ValueError("Scholar returned no metrics table (likely a CAPTCHA)")
    # Order: citations all, citations recent, h-index all, h-index recent, i10 all, i10 recent.
    return int(cells[0]), int(cells[2])


def scholar_metrics():
    key = os.environ.get("SERPAPI_KEY", "").strip()
    errors = []
    if key:
        try:
            return scholar_via_serpapi(key), "SerpAPI"
        except Exception as exc:  # fall back to the direct fetch
            errors.append(f"SerpAPI: {exc}")
    try:
        return scholar_direct(), "scholar.google.com"
    except Exception as exc:
        errors.append(f"direct: {exc}")
    raise RuntimeError("; ".join(errors))


def set_metric(html, name, value):
    pattern = re.compile(r'(<(\w+)[^>]*\bdata-metric="' + re.escape(name) + r'"[^>]*>)(.*?)(</\2>)', re.S)
    matches = pattern.findall(html)
    if len(matches) != 1:
        raise ValueError(f'expected one data-metric="{name}" element, found {len(matches)}')
    return pattern.sub(lambda m: m.group(1) + value + m.group(4), html)


def main():
    today = datetime.date.today()
    with open(INDEX, encoding="utf-8") as f:
        html = f.read()
    original = html
    failures = []

    for name, value in publication_counts(html).items():
        html = set_metric(html, name, value)

    try:
        total = cran_downloads(today)
        html = set_metric(html, "cran-downloads", floor_label(total, 1000, "K"))
        html = set_metric(html, "cran-date", month_label(today))
        print(f"CRAN downloads: {total:,}")
    except Exception as exc:
        failures.append(f"CRAN: {exc}")

    try:
        (citations, h_index), source = scholar_metrics()
        if citations <= 0 or h_index <= 0:
            raise ValueError(f"implausible values: citations={citations}, h-index={h_index}")
        html = set_metric(html, "citations", floor_label(citations, 10))
        html = set_metric(html, "h-index", str(h_index))
        html = set_metric(html, "scholar-date", month_label(today))
        print(f"Scholar ({source}): citations={citations:,}, h-index={h_index}")
    except Exception as exc:
        failures.append(f"Scholar: {exc}")

    if html != original:
        with open(INDEX, "w", encoding="utf-8") as f:
            f.write(html)
        print("index.html updated")
    else:
        print("index.html unchanged")

    for failure in failures:
        print(f"::warning::{failure} (kept previous values)")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and failures:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### Some metrics were not refreshed\n\n")
            f.write("".join(f"- {failure} (kept previous values)\n" for failure in failures))
            if not os.environ.get("SERPAPI_KEY", "").strip():
                f.write("\nThe SERPAPI_KEY secret is not set, so Google Scholar is fetched directly and is often blocked.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
