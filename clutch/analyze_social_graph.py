import json
import logging
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Set, Tuple

from clutch.db.models import FriendsCrawlTask, Player

logger = logging.getLogger(__name__)


def get_social_graph_stats() -> Dict:
    """Get basic statistics about the social graph."""
    logger.info("Analyzing social graph statistics...")

    # Get all players and successful friend crawls
    total_players = Player.select().count()
    successful_crawls = FriendsCrawlTask.select().where(
        FriendsCrawlTask.crawl_successful == True
    )

    players_with_friends_count = successful_crawls.count()
    players_without_friends = total_players - players_with_friends_count

    # Analyze friend connections
    total_friendships = 0
    friend_counts = []
    all_connections = defaultdict(set)

    for crawl_task in successful_crawls:
        friend_count = len(crawl_task.friends_list)
        friend_counts.append(friend_count)
        total_friendships += friend_count

        # Track bidirectional connections
        for friend_id in crawl_task.friends_list:
            all_connections[crawl_task.steam_id].add(friend_id)

    # Calculate mutual friendships (bidirectional)
    mutual_friendships = 0
    for player_id, friends in all_connections.items():
        for friend_id in friends:
            if friend_id in all_connections and player_id in all_connections[friend_id]:
                mutual_friendships += 1

    # Since we count each mutual friendship twice, divide by 2
    mutual_friendships = mutual_friendships // 2

    stats = {
        "total_players": total_players,
        "players_with_friends": players_with_friends_count,
        "players_without_friends": players_without_friends,
        "total_friendships": total_friendships,
        "mutual_friendships": mutual_friendships,
        "avg_friends_per_player": sum(friend_counts) / len(friend_counts)
        if friend_counts
        else 0,
        "max_friends": max(friend_counts) if friend_counts else 0,
        "min_friends": min(friend_counts) if friend_counts else 0,
        "friend_count_distribution": dict(Counter(friend_counts)),
        "coverage_percentage": (players_with_friends_count / total_players * 100)
        if total_players > 0
        else 0,
    }

    return stats


def find_most_connected_players(limit: int = 10) -> List[Tuple[Player, int]]:
    """Find the players with the most friends."""
    logger.info(f"Finding top {limit} most connected players...")

    # Get successful crawls and their friend counts
    crawl_tasks = (
        FriendsCrawlTask.select()
        .where(FriendsCrawlTask.crawl_successful == True)
        .order_by(FriendsCrawlTask.friends_count.desc())
        .limit(limit)
    )

    # Get player info for each
    player_friend_counts = []
    for crawl_task in crawl_tasks:
        try:
            player = Player.get(Player.steam_id == crawl_task.steam_id)
            player_friend_counts.append((player, crawl_task.friends_count))
        except Player.DoesNotExist:
            logger.warning(
                f"Player with steam_id {crawl_task.steam_id} not found in database"
            )
            continue

    return player_friend_counts


def find_mutual_friend_clusters(min_cluster_size: int = 3) -> List[Set[int]]:
    """Find groups of players who are all mutual friends with each other."""
    logger.info(f"Finding mutual friend clusters (min size: {min_cluster_size})...")

    # Build adjacency set for mutual friendships only
    mutual_connections = defaultdict(set)

    successful_crawls = FriendsCrawlTask.select().where(
        FriendsCrawlTask.crawl_successful == True
    )

    # First pass: collect all connections
    all_connections = {}
    for crawl_task in successful_crawls:
        all_connections[crawl_task.steam_id] = set(crawl_task.friends_list)

    # Second pass: find mutual connections
    for player_id, friends in all_connections.items():
        for friend_id in friends:
            if friend_id in all_connections and player_id in all_connections[friend_id]:
                mutual_connections[player_id].add(friend_id)

    # Find cliques (groups where everyone is connected to everyone)
    clusters = []
    processed = set()

    for player_id in mutual_connections:
        if player_id in processed:
            continue

        # Start with this player and their mutual friends
        potential_cluster = {player_id} | mutual_connections[player_id]

        # Check if this forms a clique
        is_clique = True
        for member1 in potential_cluster:
            for member2 in potential_cluster:
                if member1 != member2 and member2 not in mutual_connections.get(
                    member1, set()
                ):
                    is_clique = False
                    break
            if not is_clique:
                break

        if is_clique and len(potential_cluster) >= min_cluster_size:
            clusters.append(potential_cluster)
            processed.update(potential_cluster)

    return clusters


def export_graph_for_visualization(output_file: str = "social_graph.json") -> None:
    """Export the social graph in a format suitable for visualization tools."""
    logger.info(f"Exporting social graph to {output_file}...")

    nodes = []
    edges = []

    # Get all players
    all_players = Player.select()
    player_lookup = {player.steam_id: player for player in all_players}

    # Get friend counts from crawl tasks
    friend_counts = {}
    successful_crawls = FriendsCrawlTask.select().where(
        FriendsCrawlTask.crawl_successful == True
    )

    for crawl_task in successful_crawls:
        friend_counts[crawl_task.steam_id] = len(crawl_task.friends_list)

    # Create nodes
    for player in all_players:
        node = {
            "id": str(player.steam_id),
            "name": player.name,
            "elo": player.elo,
            "country": player.country,
            "friend_count": friend_counts.get(player.steam_id, 0),
        }
        nodes.append(node)

    # Create edges (friendships)
    processed_edges = set()

    for crawl_task in successful_crawls:
        for friend_id in crawl_task.friends_list:
            if friend_id in player_lookup:
                # Create a unique edge identifier (smaller ID first to avoid duplicates)
                edge_id = tuple(sorted([crawl_task.steam_id, friend_id]))

                if edge_id not in processed_edges:
                    # Check if it's mutual
                    friend_crawl = (
                        FriendsCrawlTask.select()
                        .where(
                            (FriendsCrawlTask.steam_id == friend_id)
                            & (FriendsCrawlTask.crawl_successful == True)
                        )
                        .first()
                    )

                    is_mutual = (
                        friend_crawl
                        and crawl_task.steam_id in friend_crawl.friends_list
                    )

                    edge = {
                        "source": str(crawl_task.steam_id),
                        "target": str(friend_id),
                        "mutual": is_mutual,
                    }
                    edges.append(edge)
                    processed_edges.add(edge_id)

    graph_data = {
        "nodes": nodes,
        "edges": edges,
        "metadata": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "exported_at": datetime.now().isoformat(),
        },
    }

    with open(output_file, "w") as f:
        json.dump(graph_data, f, indent=2)

    logger.info(f"Exported {len(nodes)} nodes and {len(edges)} edges to {output_file}")


def get_crawl_status_summary() -> Dict:
    """Get a summary of friends crawling status."""

    total_players = Player.select().count()
    total_crawled = FriendsCrawlTask.select().count()

    # Status distribution
    status_counts = {}
    for crawl_task in FriendsCrawlTask.select():
        status = crawl_task.crawl_status
        status_counts[status] = status_counts.get(status, 0) + 1

    # Success metrics
    successful_crawls = sum(
        1 for task in FriendsCrawlTask.select() if task.crawl_successful
    )

    return {
        "total_players": total_players,
        "total_crawled": total_crawled,
        "uncrawled_players": total_players - total_crawled,
        "successful_crawls": successful_crawls,
        "failed_crawls": total_crawled - successful_crawls,
        "coverage_percentage": (total_crawled / total_players * 100)
        if total_players > 0
        else 0,
        "success_rate": (successful_crawls / total_crawled * 100)
        if total_crawled > 0
        else 0,
        "status_distribution": status_counts,
    }


def print_social_graph_report():
    """Print a comprehensive report about the social graph."""
    print("=" * 60)
    print("RENOWN.GG SOCIAL GRAPH ANALYSIS REPORT")
    print("=" * 60)

    # Crawl status summary
    crawl_stats = get_crawl_status_summary()

    print("\n📊 CRAWL STATUS:")
    print(f"  Total players in database: {crawl_stats['total_players']:,}")
    print(f"  Players crawled: {crawl_stats['total_crawled']:,}")
    print(f"  Players not yet crawled: {crawl_stats['uncrawled_players']:,}")
    print(f"  Coverage: {crawl_stats['coverage_percentage']:.1f}%")
    print(f"  Successful crawls: {crawl_stats['successful_crawls']:,}")
    print(f"  Failed crawls: {crawl_stats['failed_crawls']:,}")
    print(f"  Success rate: {crawl_stats['success_rate']:.1f}%")

    # Status distribution
    print("\n📈 CRAWL STATUS DISTRIBUTION:")
    for status, count in crawl_stats["status_distribution"].items():
        percentage = (
            (count / crawl_stats["total_crawled"] * 100)
            if crawl_stats["total_crawled"] > 0
            else 0
        )
        print(f"  {status}: {count:,} ({percentage:.1f}%)")

    # Social graph stats
    stats = get_social_graph_stats()

    print("\n🤝 FRIENDSHIP STATISTICS:")
    print(f"  Total friendships: {stats['total_friendships']:,}")
    print(f"  Mutual friendships: {stats['mutual_friendships']:,}")
    print(f"  Average friends per player: {stats['avg_friends_per_player']:.1f}")
    print(f"  Max friends (single player): {stats['max_friends']}")
    print(f"  Min friends (single player): {stats['min_friends']}")

    # Most connected players
    print("\n🌟 MOST CONNECTED PLAYERS:")
    most_connected = find_most_connected_players(10)
    for i, (player, friend_count) in enumerate(most_connected, 1):
        print(f"  {i:2d}. {player.name} ({player.steam_id}) - {friend_count} friends")

    # Friend count distribution
    print("\n📈 FRIEND COUNT DISTRIBUTION:")
    distribution = stats["friend_count_distribution"]
    for friend_count in sorted(distribution.keys())[:10]:  # Show first 10 buckets
        count = distribution[friend_count]
        percentage = (
            (count / stats["players_with_friends"] * 100)
            if stats["players_with_friends"] > 0
            else 0
        )
        print(f"  {friend_count:3d} friends: {count:4d} players ({percentage:4.1f}%)")

    if len(distribution) > 10:
        print(f"  ... and {len(distribution) - 10} more friend count buckets")

    # Mutual friend clusters
    print("\n👥 MUTUAL FRIEND CLUSTERS:")
    clusters = find_mutual_friend_clusters(min_cluster_size=3)
    if clusters:
        print(f"  Found {len(clusters)} clusters of mutual friends:")
        for i, cluster in enumerate(clusters[:5], 1):  # Show first 5 clusters
            print(f"    Cluster {i}: {len(cluster)} players")
        if len(clusters) > 5:
            print(f"    ... and {len(clusters) - 5} more clusters")
    else:
        print("  No mutual friend clusters found")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Analyze the Renown social graph")
    parser.add_argument(
        "--export", help="Export graph data to JSON file for visualization"
    )
    parser.add_argument(
        "--report", action="store_true", help="Print comprehensive analysis report"
    )
    parser.add_argument(
        "--stats-only", action="store_true", help="Print basic stats only"
    )
    parser.add_argument(
        "--crawl-status", action="store_true", help="Show crawl status summary only"
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.export:
        export_graph_for_visualization(args.export)
    elif args.crawl_status:
        crawl_stats = get_crawl_status_summary()
        print(json.dumps(crawl_stats, indent=2))
    elif args.stats_only:
        stats = get_social_graph_stats()
        print(json.dumps(stats, indent=2))
    elif args.report:
        print_social_graph_report()
    else:
        print_social_graph_report()  # Default action
