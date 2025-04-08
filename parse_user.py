import re
from datetime import datetime
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from clutch.models import Player, UserCrawlTask


def extract_user_data(html_content: str) -> Optional[Dict]:
    """Extract user data from HTML content."""
    soup = BeautifulSoup(html_content, "html.parser")

    # Check for 404 page
    not_found_container = soup.find("div", class_="page not-found")
    if not_found_container:
        not_found = not_found_container.find(
            "div", class_="text", string="This page could not be found"
        )
        if not_found:
            print("User does not exist (404)")
            return None

    try:
        # Extract name
        profile_data_container = soup.find("div", class_="profile-banner")
        name = profile_data_container.find("div", class_="name").text.strip()
        print(f"Name: {name}")

        # Extract registered since date
        registered_text = profile_data_container.find(
            "div", class_="registered-since"
        ).text.strip()
        print(f"Registered since: {registered_text}")
        registered_date_str = registered_text.replace("Registered since", "").strip()
        registered_since = datetime.strptime(registered_date_str, "%b %d, %Y").replace(
            tzinfo=ZoneInfo("UTC")
        )
        print(f"Registered since: {registered_since}")
        # Extract elo
        elo_element = soup.find("div", class_="current-elo")
        if elo_element:
            elo = int(elo_element.text.replace("Elo", "").replace(",", "").strip())
        else:
            elo = -1

        # Extract avatar URL
        avatar_img = profile_data_container.find("div", class_="avatar").find("img")
        avatar_url = avatar_img["src"] if avatar_img else None

        # Extract country from flag image URL
        flag_img = profile_data_container.find("div", class_="flag").find("img")
        country = None
        if flag_img and flag_img["src"]:
            country_match = re.search(r"/flags/1x1/(\w+)\.svg$", flag_img["src"])
            if country_match:
                country = country_match.group(1)

        return {
            "name": name,
            "registered_since": registered_since,
            "elo": elo,
            "avatar_url": avatar_url,
            "country": country,
        }
    except Exception as e:
        print(f"Error parsing user data: {e}")
        return None


def main():
    """Process all unprocessed UserCrawlTask entries."""
    unprocessed_tasks = UserCrawlTask.select().where(
        UserCrawlTask.processed == False  # noqa: E712
    )

    for task in unprocessed_tasks:
        print(f"Processing user {task.steam_id}")

        user_data = extract_user_data(task.page_html)

        if user_data:
            Player.get_or_create(
                steam_id=task.steam_id,
                defaults={
                    "name": user_data["name"],
                    "elo": user_data["elo"],
                    "country": user_data["country"],
                    "registered_since": user_data["registered_since"],
                    "avatar_url": user_data["avatar_url"],
                },
            )
        else:
            print(f"Could not parse user {task.steam_id} (404 or parsing error)")
            task.error_occured = True
            task.save()


if __name__ == "__main__":
    main()
