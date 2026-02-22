#!/usr/bin/env python3
"""
Scrape all news stories from https://dignityny.org/news
and save them to content/entries.json for the local website.
Downloads all images locally to the images/ directory.

Usage:
    python scrape_news.py
"""

import hashlib
import json
import os
import re
import sys
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, unquote

# Fix console encoding on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

BASE_URL = "https://dignityny.org"
NEWS_URL = f"{BASE_URL}/news"
OUTPUT_FILE = "content/entries.json"
IMAGES_DIR = "images"
DELAY = 1  # seconds between requests to be polite


def get_soup(url):
    """Fetch a URL and return a BeautifulSoup object."""
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def get_total_pages():
    """Determine the total number of listing pages."""
    soup = get_soup(NEWS_URL)
    last_link = soup.find("a", string=lambda s: s and "Last" in s, href=lambda h: h and "page=" in h)
    if last_link:
        match = re.search(r"page=(\d+)", last_link["href"])
        if match:
            return int(match.group(1))
    # Fallback: count page links
    page_links = soup.find_all("a", href=lambda h: h and "page=" in h)
    max_page = 0
    for link in page_links:
        match = re.search(r"page=(\d+)", link["href"])
        if match:
            max_page = max(max_page, int(match.group(1)))
    return max_page


def collect_article_urls():
    """Collect all article URLs from all listing pages."""
    print("  Discovering total pages...", flush=True)
    total_pages = get_total_pages()
    print(f"  Found {total_pages + 1} pages of news\n", flush=True)

    article_urls = []
    seen = set()

    for page_num in range(total_pages + 1):
        url = f"{NEWS_URL}?page={page_num}" if page_num > 0 else NEWS_URL
        print(f"  Scanning page {page_num + 1}/{total_pages + 1}...", end=" ", flush=True)
        soup = get_soup(url)

        count = 0
        articles = soup.find_all("article", class_="story")
        for article in articles:
            link = article.find("a", href=lambda h: h and "/node/" in h)
            if link:
                href = link["href"]
                full_url = urljoin(BASE_URL, href)
                # Normalize: strip /index.php/ prefix
                normalized = re.sub(r"/index\.php/", "/", full_url)
                if normalized not in seen:
                    seen.add(normalized)
                    article_urls.append(full_url)
                    count += 1

        print(f"found {count} articles (total: {len(article_urls)})", flush=True)

        if page_num < total_pages:
            time.sleep(DELAY)

    return article_urls


def make_local_filename(img_url):
    """Generate a unique local filename for an image URL."""
    parsed = urlparse(img_url)
    # Get the original filename from the URL path
    path = unquote(parsed.path)
    basename = os.path.basename(path)
    # Strip query params from name
    basename = re.sub(r"\?.*$", "", basename)
    # If no usable filename, create one from a hash
    if not basename or not re.search(r"\.\w{2,5}$", basename):
        ext = ".jpg"
        basename = hashlib.md5(img_url.encode()).hexdigest()[:12] + ext
    # Clean up special characters but keep the name readable
    basename = re.sub(r"[^\w.\-]", "_", basename)
    return basename


def download_image(img_url, local_path):
    """Download an image to a local path. Returns True on success."""
    try:
        r = requests.get(img_url, timeout=30, stream=True)
        r.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"\n    WARN: Failed to download {img_url}: {e}", flush=True)
        return False


def scrape_article(url):
    """Scrape a single article page for its full content."""
    soup = get_soup(url)

    article = soup.find("article", class_="story")
    if not article:
        return None

    # Title
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""

    # Body HTML - find the Drupal body field div
    body_div = article.find("div", class_="field--name-body")
    body_html = ""
    images = []

    if body_div:
        # Process images: download locally and rewrite src to local paths
        for img in body_div.find_all("img"):
            src = img.get("src", "")
            if not src:
                continue
            abs_url = urljoin(BASE_URL, src)

            # Skip blob: URLs (can't be downloaded)
            if abs_url.startswith("blob:"):
                continue

            local_name = make_local_filename(abs_url)
            local_path = os.path.join(IMAGES_DIR, local_name)
            local_ref = f"images/{local_name}"

            # Download if not already present
            if not os.path.exists(local_path):
                if download_image(abs_url, local_path):
                    images.append(local_ref)
                    img["src"] = local_ref
                # If download fails, keep the original absolute URL
                else:
                    img["src"] = abs_url
            else:
                images.append(local_ref)
                img["src"] = local_ref

            # Add responsive styling
            img["style"] = "max-width:100%; height:auto;"

        # Get the inner HTML content (now with local image paths)
        body_html = body_div.decode_contents().strip()
        # Clean up: remove <meta> tags and leading empty paragraphs
        body_html = re.sub(r"<meta[^>]*/>", "", body_html)
        body_html = re.sub(r"^\s*<p>\s*</p>\s*", "", body_html)

    # Build canonical link (normalize URL)
    canonical_tag = soup.find("link", rel="canonical")
    if canonical_tag:
        canonical = re.sub(r"/index\.php/", "/", urljoin(BASE_URL, canonical_tag["href"]))
    else:
        canonical = re.sub(r"/index\.php/", "/", url)

    return {
        "title": title,
        "date": "",  # Drupal site doesn't expose dates in HTML
        "body": body_html,
        "link": canonical,
        "images": images,
    }


def try_extract_date_from_title(title):
    """Try to extract a date from the article title."""
    months = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
    }
    pattern = r"(?:(" + "|".join(months.keys()) + r")\s+(\d{1,2})\s*,?\s*(\d{4}))"
    match = re.search(pattern, title.lower())
    if match:
        month = months[match.group(1)]
        day = match.group(2).zfill(2)
        year = match.group(3)
        return f"{year}-{month}-{day}"
    return ""


def main():
    print("=" * 60)
    print("  Dignity/New York News Scraper")
    print("=" * 60)

    # Ensure images directory exists
    os.makedirs(IMAGES_DIR, exist_ok=True)

    # Step 1: Collect all article URLs from listing pages
    print("\n[1/3] Collecting article URLs from listing pages...\n", flush=True)
    article_urls = collect_article_urls()
    print(f"\n  Total unique articles found: {len(article_urls)}\n", flush=True)

    # Step 2: Visit each article to get full content and download images
    total = len(article_urls)
    img_count = 0
    print(f"[2/3] Scraping articles & downloading images (0/{total})...\n", flush=True)
    entries = []
    errors = []
    for i, url in enumerate(article_urls):
        pct = int((i + 1) / total * 100)
        print(f"  [{i+1}/{total}] ({pct}%) Fetching {url.split('/')[-1]}...", end=" ", flush=True)
        try:
            entry = scrape_article(url)
            if entry:
                if not entry["date"]:
                    entry["date"] = try_extract_date_from_title(entry["title"])
                num_imgs = len(entry["images"])
                img_count += num_imgs
                entries.append(entry)
                title_short = entry["title"][:50]
                img_info = f" [{num_imgs} img]" if num_imgs else ""
                print(f"OK{img_info} - {title_short}", flush=True)
            else:
                print("WARN: no article found", flush=True)
        except Exception as e:
            print(f"ERROR: {e}", flush=True)
            errors.append(url)

        if i < total - 1:
            time.sleep(DELAY)

    # Step 3: Remove empty images arrays for cleaner output
    for entry in entries:
        if not entry["images"]:
            del entry["images"]

    # Step 4: Save to JSON
    print(f"\n[3/3] Saving {len(entries)} entries to {OUTPUT_FILE}...", flush=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    print(f"  DONE!")
    print(f"  Stories scraped: {len(entries)}")
    print(f"  Images downloaded: {img_count}")
    print(f"  Errors: {len(errors)}")
    if errors:
        print(f"  Failed URLs:")
        for u in errors:
            print(f"    - {u}")
    print(f"  Output: {OUTPUT_FILE}")
    print(f"  Images: {IMAGES_DIR}/")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
