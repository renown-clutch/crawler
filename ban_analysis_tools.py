#!/usr/bin/env python3
"""
Ban Analysis and Cleanup Tools

This script provides utilities to:
1. Check for duplicate/similar bans (same player, similar times)
2. Analyze the current state of ban data
3. Help transition to the new sync system
4. Clean up obvious duplicates before running the new sync
"""

from datetime import datetime, timedelta

from clutch.db.models import Ban, Player, db


def find_duplicate_bans():
    """Find users with multiple bans on the same day"""
    print("=== DUPLICATE BANS ON SAME DAY ===")

    query = """
    SELECT 
        p.steam_id,
        p.name,
        DATE(b.issued_on) as ban_date,
        COUNT(*) as ban_count,
        STRING_AGG(b.category, ', ') as categories,
        STRING_AGG(b.severity, ', ') as severities,
        STRING_AGG(CAST(b.id AS TEXT), ', ') as ban_ids
    FROM ban b
    JOIN player p ON b.player_id = p.id
    GROUP BY p.steam_id, p.name, DATE(b.issued_on)
    HAVING COUNT(*) > 1
    ORDER BY ban_count DESC, p.steam_id;
    """

    cursor = db.execute_sql(query)
    results = cursor.fetchall()

    if not results:
        print("✅ No users found with multiple bans on the same day")
        return []

    print(f"⚠️  Found {len(results)} users with multiple bans on the same day:\n")

    duplicate_cases = []
    for row in results:
        steam_id, name, ban_date, ban_count, categories, severities, ban_ids = row
        print(f"Steam ID: {steam_id}")
        print(f"Name: {name}")
        print(f"Ban Date: {ban_date}")
        print(f"Number of Bans: {ban_count}")
        print(f"Categories: {categories}")
        print(f"Severities: {severities}")
        print(f"Ban IDs: {ban_ids}")
        print("-" * 50)

        duplicate_cases.append(
            {
                "steam_id": steam_id,
                "name": name,
                "ban_date": ban_date,
                "ban_count": ban_count,
                "ban_ids": [int(x) for x in ban_ids.split(", ")],
            }
        )

    return duplicate_cases


def find_similar_bans():
    """Find bans that might be recreated (same player, similar category/severity, different times)"""
    print("\n=== POTENTIALLY RECREATED BANS ===")

    # Group bans by player
    players_with_multiple_bans = {}
    for ban in Ban.select().join(Player):
        if ban.player.steam_id not in players_with_multiple_bans:
            players_with_multiple_bans[ban.player.steam_id] = []
        players_with_multiple_bans[ban.player.steam_id].append(ban)

    potentially_recreated = []

    for steam_id, bans in players_with_multiple_bans.items():
        if len(bans) < 2:
            continue

        # Sort bans by issued_on
        bans.sort(key=lambda x: x.issued_on)

        # Look for similar bans
        for i in range(len(bans)):
            for j in range(i + 1, len(bans)):
                ban1, ban2 = bans[i], bans[j]

                # Check if they could be the same ban recreated
                time_diff = abs((ban2.issued_on - ban1.issued_on).total_seconds())

                if (
                    ban1.category == ban2.category
                    and ban1.severity == ban2.severity
                    and time_diff < 86400
                ):  # Within 24 hours
                    print("POTENTIAL RECREATION DETECTED:")
                    print(f"  Player: {ban1.player.name} (steam_id: {steam_id})")
                    print(f"  Category: {ban1.category}, Severity: {ban1.severity}")
                    print(
                        f"  Ban 1 - ID: {ban1.id}, Issued: {ban1.issued_on}, Expires: {ban1.expires_on}"
                    )
                    print(
                        f"  Ban 2 - ID: {ban2.id}, Issued: {ban2.issued_on}, Expires: {ban2.expires_on}"
                    )
                    print(f"  Time difference: {time_diff / 3600:.1f} hours")
                    print("-" * 60)

                    potentially_recreated.append(
                        {
                            "steam_id": steam_id,
                            "player_name": ban1.player.name,
                            "older_ban": ban1,
                            "newer_ban": ban2,
                            "time_diff_hours": time_diff / 3600,
                        }
                    )

    if not potentially_recreated:
        print("✅ No potentially recreated bans found")
    else:
        print(f"⚠️  Found {len(potentially_recreated)} potentially recreated bans")

    return potentially_recreated


def analyze_ban_data():
    """Provide overall statistics about ban data"""
    print("\n=== BAN DATA ANALYSIS ===")

    total_bans = Ban.select().count()
    total_players = Player.select().join(Ban).distinct().count()

    print(f"Total bans: {total_bans}")
    print(f"Total players with bans: {total_players}")
    print(f"Average bans per player: {total_bans / total_players:.2f}")

    # Category breakdown
    print("\nBan categories:")
    categories = db.execute_sql(
        "SELECT category, COUNT(*) FROM ban GROUP BY category ORDER BY COUNT(*) DESC"
    ).fetchall()
    for category, count in categories:
        print(f"  {category}: {count}")

    # Recent activity
    recent_bans = (
        Ban.select().where(Ban.issued_on > datetime.now() - timedelta(days=30)).count()
    )
    print(f"\nBans issued in last 30 days: {recent_bans}")


def prepare_for_new_sync():
    """Prepare database for transition to new sync system"""
    print("\n=== PREPARING FOR NEW SYNC SYSTEM ===")

    # Check if new fields exist (they should after model update)
    try:
        # Test if new fields exist by querying them
        test_ban = Ban.select().limit(1).first()
        if test_ban:
            _ = test_ban.website_active
            _ = test_ban.last_seen_on_website
            _ = test_ban.created_at
            print("✅ New sync fields are already present")
        else:
            print("⚠️  No bans in database to test fields")
    except Exception as e:
        print(f"❌ New sync fields not found. You need to run database migration: {e}")
        return False

    # Initialize new fields for existing bans
    bans_to_update = (
        Ban.select()
        .where(
            (Ban.website_active.is_null())
            | (Ban.last_seen_on_website.is_null())
            | (Ban.created_at.is_null())
        )
        .count()
    )

    if bans_to_update > 0:
        print(
            f"Initializing {bans_to_update} bans with default values for new sync system..."
        )

        now_time = datetime.now()
        Ban.update(
            website_active=True, last_seen_on_website=now_time, created_at=now_time
        ).where(
            (Ban.website_active.is_null())
            | (Ban.last_seen_on_website.is_null())
            | (Ban.created_at.is_null())
        ).execute()

        print("✅ Successfully initialized existing bans for new sync system")
    else:
        print("✅ All bans already have sync fields initialized")

    return True


def main():
    """Run all analysis tools"""
    print("BAN DATA ANALYSIS AND CLEANUP TOOLS")
    print("=" * 50)

    # Basic analysis
    analyze_ban_data()

    # Find duplicates
    duplicates = find_duplicate_bans()

    # Find potentially recreated bans
    recreated = find_similar_bans()

    # Prepare for new sync
    if prepare_for_new_sync():
        print("\n✅ Database is ready for the new sync system!")
        print("\nNext steps:")
        print("1. Review the duplicate and recreated bans above")
        print("2. Run the new sync job to get accurate data")
        print("3. Monitor logs for admin modifications")
    else:
        print("\n❌ Database needs migration before using new sync system")

    print("\nSUMMARY:")
    print(f"- Found {len(duplicates)} cases of multiple bans on same day")
    print(f"- Found {len(recreated)} potentially recreated bans")
    print(
        f"- Database preparation: {'✅ Ready' if prepare_for_new_sync() else '❌ Needs migration'}"
    )


if __name__ == "__main__":
    main()
