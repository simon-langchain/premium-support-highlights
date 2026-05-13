"""Inspect a QBR template presentation and print object IDs for shapes that need
to be hardcoded in slides_client._TEMPLATE_CONFIGS.

Usage (from the backend directory):
    uv run python discover_template_ids.py [PRESENTATION_ID]

If PRESENTATION_ID is omitted, reads QBR_TEMPLATE_ID from the environment
(falling back to the production template).

What it finds:
  - All 5-dot rows (health score / FR score indicators)
  - The largest text box on any slide that contains {{FEATURE_REQUEST_LIST}}
  - Text boxes and images on the slide labelled "LangChain Product Roadmap"
  - The image placeholder on the "Enterprise Support" slide (metrics chart)
"""

import json
import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


def _creds():
    from google.oauth2 import service_account
    raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    return service_account.Credentials.from_service_account_info(
        json.loads(raw),
        scopes=[
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/presentations.readonly",
        ],
    )


def _services():
    from googleapiclient.discovery import build
    creds = _creds()
    return build("slides", "v1", credentials=creds)


def _text_of(elem: dict) -> str:
    parts = []
    for tr in elem.get("shape", {}).get("text", {}).get("textElements", []):
        parts.append(tr.get("textRun", {}).get("content", ""))
    return "".join(parts).strip()


def main():
    pres_id = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get("QBR_TEMPLATE_ID", "1cPn6jsC4Sc3HcRJEOAaYcuo8nDVozRe5c8PStgnm-WM")
    )
    print(f"Inspecting presentation: {pres_id}\n")

    slides_svc = _services()
    pres = slides_svc.presentations().get(presentationId=pres_id).execute()
    slides = pres.get("slides", [])

    print(f"Total slides: {len(slides)}\n")

    for slide_idx, slide in enumerate(slides):
        slide_id = slide.get("objectId", "")
        elements = slide.get("pageElements", [])

        # Collect slide title for context
        title = ""
        for elem in elements:
            t = _text_of(elem)
            if t and not title:
                title = t[:60]

        # --- Find dot rows (5 adjacent ellipses/shapes in a row) ---
        shapes = [e for e in elements if "shape" in e]
        ellipses = [
            e for e in shapes
            if e.get("shape", {}).get("shapeType") in ("ELLIPSE", "ROUND_RECTANGLE")
        ]
        if len(ellipses) >= 5:
            # Sort by Y then X to group into rows
            def pos(e):
                t = e.get("transform", {})
                return (round(t.get("translateY", 0) / 50000), round(t.get("translateX", 0) / 50000))
            ellipses.sort(key=pos)
            # Group into rows by Y bucket
            rows: dict[int, list] = {}
            for e in ellipses:
                t = e.get("transform", {})
                row_key = round(t.get("translateY", 0) / 200000)
                rows.setdefault(row_key, []).append(e)
            for row_key, row_elems in rows.items():
                if len(row_elems) == 5:
                    ids = [e["objectId"] for e in row_elems]
                    print(f"Slide {slide_idx+1} ({title[:40]!r}): 5-dot row")
                    print(f"  IDs: {ids}")

        # --- Find {{FEATURE_REQUEST_LIST}} box ---
        for elem in elements:
            t = _text_of(elem)
            if "{{FEATURE_REQUEST_LIST}}" in t or "FEATURE_REQUEST" in t:
                print(f"Slide {slide_idx+1} ({title[:40]!r}): FR list box")
                print(f"  objectId: {elem['objectId']!r}")

        # --- Roadmap slide: text boxes and images ---
        if "roadmap" in title.lower() or "product roadmap" in title.lower():
            print(f"\nSlide {slide_idx+1} — ROADMAP SLIDE ({title!r})")
            print(f"  Slide objectId: {slide_id!r}")
            text_boxes = [e for e in elements if "shape" in e and _text_of(e)]
            images = [e for e in elements if "image" in e]
            print(f"  Text boxes ({len(text_boxes)}):")
            for e in text_boxes:
                print(f"    {e['objectId']!r}: {_text_of(e)[:60]!r}")
            print(f"  Images ({len(images)}):")
            for e in images:
                print(f"    {e['objectId']!r}")

        # --- Enterprise Support slide: images (metrics chart placeholder) ---
        if "enterprise support" in title.lower():
            print(f"\nSlide {slide_idx+1} — ENTERPRISE SUPPORT ({title!r})")
            images = [e for e in elements if "image" in e]
            print(f"  Image placeholders ({len(images)}):")
            for e in images:
                print(f"    {e['objectId']!r}")

    print("\nDone.")


if __name__ == "__main__":
    main()
