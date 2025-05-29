import json

with open("renown_users.json", "r") as f:
    users = json.load(f)

print(f"Number of users in renown_users.json: {len(users)}")
print(f"File size: {len(json.dumps(users, indent=2)) / (1024 * 1024):.2f} MB")
