import matplotlib.pyplot as plt

from clutch.db.models import Player


def get_rank_tier(elo):
    if elo is None:
        return "unranked"
    if elo >= 25000:
        return "renowned"
    if elo >= 22000:
        return "master"
    if elo >= 19000:
        return "diamond"
    if elo >= 16000:
        return "platinum"
    if elo >= 13000:
        return "gold"
    if elo >= 10000:
        return "silver"
    if elo >= 100:
        return "bronze"
    return "unranked"


def analyze_ranks():
    # Get all players with their ELO
    players = Player.select(Player.elo)

    # Extract ELO values
    elo_values = [p.elo for p in players if p.elo is not None]

    # Create figure with two subplots
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

    # Plot 1: ELO Distribution
    ax1.hist(elo_values, bins=50, edgecolor="black")
    ax1.set_title("ELO Distribution")
    ax1.set_xlabel("ELO")
    ax1.set_ylabel("Number of Players")
    ax1.grid(True, alpha=0.3)

    # Plot 2: Rank Distribution
    rank_counts = {}
    for elo in elo_values:
        rank = get_rank_tier(elo)
        rank_counts[rank] = rank_counts.get(rank, 0) + 1

    # Sort ranks in order
    rank_order = [
        "unranked",
        "bronze",
        "silver",
        "gold",
        "platinum",
        "diamond",
        "master",
        "renowned",
    ]
    sorted_ranks = [rank for rank in rank_order if rank in rank_counts]
    sorted_counts = [rank_counts[rank] for rank in sorted_ranks]

    # Create bar chart
    bars = ax2.bar(sorted_ranks, sorted_counts)
    ax2.set_title("Rank Distribution")
    ax2.set_xlabel("Rank")
    ax2.set_ylabel("Number of Players")
    ax2.grid(True, alpha=0.3)

    # Rotate x-axis labels for better readability
    plt.xticks(rotation=45)

    # Add value labels on top of bars
    for bar in bars:
        height = bar.get_height()
        ax2.text(
            bar.get_x() + bar.get_width() / 2.0,
            height,
            f"{int(height)}",
            ha="center",
            va="bottom",
        )

    # Adjust layout to prevent label cutoff
    plt.tight_layout()

    # Save the plot
    plt.savefig("rank_distribution.png")
    print("Plot saved as 'rank_distribution.png'")


if __name__ == "__main__":
    analyze_ranks()
