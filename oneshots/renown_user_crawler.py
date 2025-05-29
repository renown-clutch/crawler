import concurrent.futures
import itertools
import json
import os
import string
import threading
from typing import Any, Dict, List, Set

import requests

# Base URL for the API
BASE_URL = "https://api.renown.gg/friend/add-friends-search"

# File to store the users
USERS_FILE = "renown_users.json"

# File to store progress
PROGRESS_FILE = "renown_progress.json"

# Characters allowed in usernames: A-Z, 0-9, -_
ALLOWED_CHARS = string.ascii_uppercase + string.digits + "-_"

# Number of concurrent workers
NUM_WORKERS = 20

# Lock for thread-safe operations
user_lock = threading.Lock()
progress_lock = threading.Lock()


def setup_session() -> requests.Session:
    """Set up a requests session with the required cookies."""
    session = requests.Session()
    session.cookies.set(
        "auth_token",
        "<redacted-token>",
        domain=".renown.gg",
    )
    # Enable connection pooling with higher max connections
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=NUM_WORKERS, pool_maxsize=NUM_WORKERS
    )
    session.mount("https://", adapter)
    return session


def generate_combinations() -> List[str]:
    """Generate all possible 3-character combinations of allowed characters."""
    return ["".join(combo) for combo in itertools.product(ALLOWED_CHARS, repeat=3)]


def load_progress() -> Set[str]:
    """Load the already processed combinations."""
    if not os.path.exists(PROGRESS_FILE):
        return set()

    with open(PROGRESS_FILE, "r") as f:
        return set(json.load(f))


def load_users() -> List[Dict[str, Any]]:
    """Load already stored users."""
    if not os.path.exists(USERS_FILE):
        return []

    with open(USERS_FILE, "r") as f:
        return json.load(f)


def save_progress(processed_combinations: Set[str]) -> None:
    """Save the processed combinations."""
    with open(PROGRESS_FILE, "w") as f:
        json.dump(list(processed_combinations), f)


def save_users(users: List[Dict[str, Any]]) -> None:
    """Save the users to a JSON file."""
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)


def process_combination(
    combo: str,
    session: requests.Session,
    users: List[Dict[str, Any]],
    user_ids: Set[int],
    processed_combinations: Set[str],
) -> None:
    """Process a single combination."""
    url = f"{BASE_URL}?searchString={combo}"

    try:
        response = session.get(url)

        if response.status_code == 200:
            data = response.json()
            new_users = 0

            if data.get("success") and "result" in data:
                # Thread-safe update of the users list
                with user_lock:
                    for user in data["result"]:
                        if user["id"] not in user_ids:
                            users.append(user)
                            user_ids.add(user["id"])
                            new_users += 1

                print(
                    f"Found {len(data['result'])} users, added {new_users} new users for '{combo}'"
                )

            # Thread-safe update of processed combinations
            with progress_lock:
                processed_combinations.add(combo)
        else:
            print(f"Error: Status code {response.status_code} for combination {combo}")

    except Exception as e:
        print(f"Error processing '{combo}': {e}")


def main():
    # Set up the session
    session = setup_session()

    # Generate all combinations
    combinations = generate_combinations()
    print(f"Generated {len(combinations)} combinations")

    # Load progress and users
    processed_combinations = load_progress()
    print(f"Loaded {len(processed_combinations)} already processed combinations")

    users = load_users()
    user_ids = {user["id"] for user in users}
    print(f"Loaded {len(users)} already stored users")

    # Filter out already processed combinations
    remaining_combinations = [
        combo for combo in combinations if combo not in processed_combinations
    ]
    print(f"{len(remaining_combinations)} combinations remaining to process")

    # Counter for periodic saving
    completed_count = 0
    save_frequency = 50  # Save after this many completed combinations

    try:
        # Use ThreadPoolExecutor for concurrent requests
        with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
            # Submit all tasks
            future_to_combo = {
                executor.submit(
                    process_combination,
                    combo,
                    session,
                    users,
                    user_ids,
                    processed_combinations,
                ): combo
                for combo in remaining_combinations
            }

            # Process as they complete
            for future in concurrent.futures.as_completed(future_to_combo):
                combo = future_to_combo[future]
                try:
                    future.result()  # Get the result or exception
                    completed_count += 1

                    # Periodically save progress
                    if completed_count % save_frequency == 0:
                        with progress_lock:
                            save_progress(processed_combinations)
                        with user_lock:
                            save_users(users)
                        print(
                            f"Progress saved. Completed {completed_count}/{len(remaining_combinations)}. Total users: {len(users)}"
                        )

                except Exception as exc:
                    print(f"Combination {combo} generated an exception: {exc}")

    except KeyboardInterrupt:
        print("\nInterrupted by user. Saving progress...")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        # Save final progress
        save_progress(processed_combinations)
        save_users(users)
        print(f"Final save complete. Total users: {len(users)}")


if __name__ == "__main__":
    main()
