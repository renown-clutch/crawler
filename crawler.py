import json
import os
import time

import requests


def main():
    session = requests.Session()

    cookies = {
        "auth_token": "<redacted-token>"
    }

    session.cookies.update(cookies)

    url = "https://api.renown.gg/friend/friend-request"

    results_file = "friend_request_results.json"
    if os.path.exists(results_file):
        with open(results_file, "r") as f:
            results = json.load(f)

        processed_ids = [int(id) for id in results.keys()]
        start_id = max(processed_ids) + 1 if processed_ids else 1
    else:
        results = {}
        start_id = 1

    for user_id in range(start_id, 1000000):
        payload = {"inviteUserId": user_id}
        status_code = 0
        response_successful = False

        try:
            response = session.post(url, json=payload)
            status_code = response.status_code
            print(f"Request for inviteUserId {user_id}: Status Code {status_code}")

            response_data = response.json()

            if status_code == 201 and response_data.get("success"):
                results[str(user_id)] = "success"
                response_successful = True
            else:
                error_msg = response_data.get("error", f"Status code: {status_code}")
                results[str(user_id)] = error_msg

            print(f"Response: {response.text}")

        except Exception as e:
            print(f"Error for inviteUserId {user_id}: {str(e)}")
            results[str(user_id)] = str(e)

        with open(results_file, "w") as f:
            json.dump(results, f, indent=2)

        if status_code == 500:
            continue
        elif response_successful:
            pass

        else:
            print("Waiting 5 seconds for next request")
            time.sleep(5)


if __name__ == "__main__":
    main()
