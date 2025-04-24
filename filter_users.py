import json
import os

# File paths
USERS_FILE = "renown_users.json"
FILTERED_USERS_FILE = "users_without_pending_requests.json"


def load_users():
    """Load the stored users."""
    if not os.path.exists(USERS_FILE):
        print(f"Error: {USERS_FILE} not found!")
        return []

    with open(USERS_FILE, "r") as f:
        return json.load(f)


def save_filtered_users(users):
    """Save the filtered users to a JSON file."""
    with open(FILTERED_USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)


def filter_users_without_pending_requests(users):
    """Filter users who don't have any PENDING received friend requests."""
    filtered_users = []

    for user in users:
        has_pending_request = False

        # Check if the user has any PENDING received friend requests
        if "receivedFriendRequests" in user:
            for request in user["receivedFriendRequests"]:
                if request.get("status") == "PENDING":
                    has_pending_request = True
                    break

        # If no pending requests, add to filtered list
        if not has_pending_request:
            filtered_users.append(user)

    return filtered_users


def main():
    # Load all users
    users = load_users()
    if not users:
        return

    print(f"Loaded {len(users)} users")

    # Sort users by ID (from lowest to highest)
    users.sort(key=lambda x: x["id"])

    # Filter users without pending received requests
    filtered_users = filter_users_without_pending_requests(users)

    print(f"Found {len(filtered_users)} users without pending received requests")

    # Save filtered users
    save_filtered_users(filtered_users)
    print(f"Saved filtered users to {FILTERED_USERS_FILE}")

    # Print some sample user IDs if available
    if filtered_users:
        sample_size = min(5, len(filtered_users))
        sample_ids = [user["id"] for user in filtered_users[:sample_size]]
        print(f"Sample user IDs: {sample_ids}")


if __name__ == "__main__":
    main()
