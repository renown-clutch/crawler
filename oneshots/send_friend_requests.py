import concurrent.futures
import json
import os
import threading
from typing import Any, Dict, List

import requests

# File paths
FILTERED_USERS_FILE = "users_without_pending_requests.json"
SENT_REQUESTS_FILE = "sent_friend_requests.json"

# API endpoint
REQUEST_URL = "https://api.renown.gg/friend/friend-request"

# Number of concurrent workers
NUM_WORKERS = 20

# Locks for thread-safe operations
results_lock = threading.Lock()


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

    # Set headers
    session.headers.update(
        {"Content-Type": "application/json", "Accept": "application/json"}
    )

    return session


def load_filtered_users() -> List[Dict[str, Any]]:
    """Load the filtered users."""
    if not os.path.exists(FILTERED_USERS_FILE):
        print(f"Error: {FILTERED_USERS_FILE} not found!")
        return []

    with open(FILTERED_USERS_FILE, "r") as f:
        return json.load(f)


def load_sent_requests() -> Dict[str, str]:
    """Load already sent friend requests."""
    if not os.path.exists(SENT_REQUESTS_FILE):
        return {}

    with open(SENT_REQUESTS_FILE, "r") as f:
        return json.load(f)


def save_sent_requests(results: Dict[str, str]) -> None:
    """Save sent friend requests to a JSON file."""
    with open(SENT_REQUESTS_FILE, "w") as f:
        json.dump(results, f, indent=2)


def send_friend_request(
    user_id: int, session: requests.Session, results: Dict[str, str]
) -> None:
    """Send a friend request to a user and store the result."""
    user_id_str = str(user_id)

    # Skip if already processed
    if user_id_str in results:
        return

    payload = {"inviteUserId": user_id}

    try:
        response = session.post(REQUEST_URL, json=payload)

        # Get status from response
        if response.status_code == 200:
            data = response.json()
            status = "success" if data.get("success") else "failed"
        else:
            status = f"error:{response.status_code}"

        # Thread-safe update of results
        with results_lock:
            results[user_id_str] = status

        print(f"User {user_id}: {status}")

    except Exception as e:
        # Thread-safe update of results with error information
        with results_lock:
            results[user_id_str] = f"exception:{str(e)}"

        print(f"Error sending request to user {user_id}: {e}")


def main():
    # Load filtered users
    users = load_filtered_users()
    if not users:
        print("No filtered users found. Run filter_users.py first.")
        return

    print(f"Loaded {len(users)} filtered users")

    # Load already sent requests
    sent_requests = load_sent_requests()
    print(f"Loaded {len(sent_requests)} already sent requests")

    # Get list of user IDs to process
    all_user_ids = [user["id"] for user in users]
    remaining_user_ids = [
        user_id for user_id in all_user_ids if str(user_id) not in sent_requests
    ]

    print(f"{len(remaining_user_ids)} users remaining to process")

    if not remaining_user_ids:
        print("All users have been processed already.")
        return

    # Set up the session
    session = setup_session()

    # Counter for periodic saving
    completed_count = 0
    save_frequency = 20  # Save after this many completed requests

    try:
        # Use ThreadPoolExecutor for concurrent requests
        with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
            # Submit all tasks
            future_to_user_id = {
                executor.submit(
                    send_friend_request, user_id, session, sent_requests
                ): user_id
                for user_id in remaining_user_ids
            }

            # Process as they complete
            for future in concurrent.futures.as_completed(future_to_user_id):
                user_id = future_to_user_id[future]
                try:
                    future.result()  # Get the result or exception
                    completed_count += 1

                    # Periodically save progress
                    if completed_count % save_frequency == 0:
                        with results_lock:
                            save_sent_requests(sent_requests)
                        print(
                            f"Progress saved. Completed {completed_count}/{len(remaining_user_ids)}."
                        )

                except Exception as exc:
                    print(f"User ID {user_id} generated an exception: {exc}")

    except KeyboardInterrupt:
        print("\nInterrupted by user. Saving progress...")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        # Save final progress
        save_sent_requests(sent_requests)

        # Get success/failure stats
        success_count = sum(
            1 for status in sent_requests.values() if status == "success"
        )

        print(f"Friend requests sent: {completed_count}")
        print(f"Total progress: {len(sent_requests)}/{len(all_user_ids)}")
        print(
            f"Success rate: {success_count}/{len(sent_requests)} ({success_count / len(sent_requests) * 100:.2f}% if available)"
        )


if __name__ == "__main__":
    main()
