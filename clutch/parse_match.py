import json
import os
import re
import sys
from typing import Optional

# Add the parent directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clutch.models import MatchCrawlTask


def extract_array_from_push(script_content: str) -> Optional[str]:
    """Extracts the full JS array passed to __next_f.push([...]) using bracket matching."""
    start_idx = script_content.find("self.__next_f.push([")
    if start_idx == -1:
        return None

    start_idx += len("self.__next_f.push(")
    bracket_count = 0
    end_idx = start_idx

    while end_idx < len(script_content):
        char = script_content[end_idx]
        if char == "[":
            bracket_count += 1
        elif char == "]":
            bracket_count -= 1
            if bracket_count == 0:
                end_idx += 1  # include closing ]
                break
        end_idx += 1

    array_string = script_content[start_idx:end_idx]
    return array_string


def fix_json_like_string(s: str) -> str:
    """Fix JSON-like issues such as $D, $L, and $n prefixes."""
    # Replace special value tags like "$D...", "$L...", "$n..."
    s = re.sub(r'"\$[A-Za-z]\d*([^"]*)"', r'"\1"', s)
    s = s.replace('\\"', '"')
    s = s.replace("\\n", "")
    return s


def extract_match_data(html_content: str) -> Optional[dict]:
    """Extract match data from HTML content."""
    print("\n=== Starting HTML content analysis ===")

    # Find all script tags
    script_pattern = r"<script[^>]*>(.*?)</script>"
    scripts = re.findall(script_pattern, html_content, re.DOTALL)
    print(f"Found {len(scripts)} script tags in total")

    for i, script_content in enumerate(scripts, 1):
        print(f"\n--- Analyzing script tag {i}/{len(scripts)} ---")
        if "matchPageData" in script_content:
            print("✓ Found 'matchPageData' in this script tag")

            raw_array_str = extract_array_from_push(script_content)
            if not raw_array_str:
                print("✗ Could not extract array from __next_f.push")
                break

            try:
                raw_array_str = raw_array_str[:-5]

                start_idx = raw_array_str.find('{\\"matchPageData\\"')
                if start_idx != -1:
                    raw_array_str = raw_array_str[start_idx:]

                raw_array_str = raw_array_str.replace('\\"', '"')

                print(raw_array_str)
                data = json.loads(raw_array_str)

                return data

            except Exception as e:
                print(f"✗ Exception during parse: {e}")
                break

    print("\n=== No valid match data found ===")
    return None


def parse_match(match_id: int) -> None:
    """Parse and print match data for a given match ID."""
    print(f"\n=== Starting parse for match ID {match_id} ===")

    # Get the match from database
    match = MatchCrawlTask.select().where(MatchCrawlTask.match_id == match_id).first()

    if not match:
        print(f"✗ Match with ID {match_id} not found in database")
        return

    if not match.page_html:
        print(f"✗ No HTML content found for match {match_id}")
        return

    print("✓ Found match in database with HTML content")
    print(f"HTML content length: {len(match.page_html)} characters")

    # Extract and print the match data
    match_data = extract_match_data(match.page_html)

    if match_data:
        print(f"\n✓ Successfully extracted match data for ID {match_id}:")
        return match_data
    else:
        print(f"\n✗ Could not extract match data for ID {match_id}")
        return None


if __name__ == "__main__":
    matches = MatchCrawlTask.select().where(MatchCrawlTask.processed == False)  # noqa: E712
    for match in matches:
        data = parse_match(match.match_id)
        if data:
            match.parsed_match_json = data
            match.processed = True
            match.save()
            print(f"✓ Successfully parsed match {match.match_id}")
        else:
            print(f"✗ Could not parse match {match.match_id}")
