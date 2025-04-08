from clutch.db.models import Match, MatchCrawlTask, Stat


def check_db_integrity():
    print("Starting database integrity checks...")

    # Get all matches and crawl tasks
    matches = list(Match.select())
    crawl_tasks = list(MatchCrawlTask.select())

    # Check 1: Match-task correspondence
    print("\nChecking match-task correspondence...")
    match_ids = {m.id for m in matches}
    task_match_ids = {t.match_id for t in crawl_tasks}

    # Find tasks without matches
    tasks_without_matches = task_match_ids - match_ids
    if tasks_without_matches:
        print(
            f"Found {len(tasks_without_matches)} tasks without corresponding matches:"
        )
        print(sorted(tasks_without_matches))
    else:
        print("✓ All tasks have corresponding matches")

    # Find matches without tasks
    matches_without_tasks = match_ids - task_match_ids
    if matches_without_tasks:
        print(
            f"Found {len(matches_without_tasks)} matches without corresponding tasks:"
        )
        print(sorted(matches_without_tasks))
    else:
        print("✓ All matches have corresponding tasks")

    # Check 2: Stats count verification
    print("\nChecking stats count per match...")
    stats_by_match = {}
    for stat in Stat.select():
        stats_by_match[stat.match_id] = stats_by_match.get(stat.match_id, 0) + 1

    matches_with_wrong_stats = []
    for match in matches:
        stats_count = stats_by_match.get(match.id, 0)
        if stats_count != 10:
            matches_with_wrong_stats.append((match.id, stats_count))

    if matches_with_wrong_stats:
        print("Found matches with incorrect number of stats:")
        for match_id, count in matches_with_wrong_stats:
            print(f"Match ID {match_id}: {count} stats (expected 10)")
    else:
        print("✓ All matches have exactly 10 stats")

    # Check 3: Match ID gaps
    print("\nChecking for match ID gaps...")
    if matches:
        min_match_id = min(m.id for m in matches)
        max_match_id = max(m.id for m in matches)
        expected_match_ids = set(range(min_match_id, max_match_id + 1))
        missing_match_ids = expected_match_ids - match_ids

        if missing_match_ids:
            print(
                f"Found {len(missing_match_ids)} missing match IDs between {min_match_id} and {max_match_id}:"
            )
            print(sorted(missing_match_ids))
        else:
            print(f"✓ No gaps found in match IDs from {min_match_id} to {max_match_id}")
    else:
        print("No matches found in database")

    # Check 4: Match Task ID gaps
    print("\nChecking for match task ID gaps...")
    if crawl_tasks:
        min_task_id = min(t.match_id for t in crawl_tasks)
        max_task_id = max(t.match_id for t in crawl_tasks)
        expected_task_ids = set(range(min_task_id, max_task_id + 1))
        missing_task_ids = expected_task_ids - task_match_ids

        if missing_task_ids:
            print(
                f"Found {len(missing_task_ids)} missing task IDs between {min_task_id} and {max_task_id}:"
            )
            print(sorted(missing_task_ids))
        else:
            print(f"✓ No gaps found in task IDs from {min_task_id} to {max_task_id}")
    else:
        print("No tasks found in database")

    # Check 5: Empty JSON/HTML fields
    print("\nChecking for empty JSON/HTML fields in tasks...")
    tasks_with_empty_fields = []
    for task in crawl_tasks:
        issues = []
        if not task.parsed_match_json or task.parsed_match_json == {}:
            issues.append("empty json")
        if not task.page_html or task.page_html.strip() == "":
            issues.append("empty html")
        if issues:
            tasks_with_empty_fields.append((task.match_id, issues))

    if tasks_with_empty_fields:
        print("Found tasks with empty fields:")
        for match_id, issues in tasks_with_empty_fields:
            print(f"Match ID {match_id}: {', '.join(issues)}")
    else:
        print("✓ All tasks have non-empty JSON and HTML fields")


if __name__ == "__main__":
    check_db_integrity()
