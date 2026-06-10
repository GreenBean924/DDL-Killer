from datetime import datetime, timezone

HALF_LIFE = 48  # hours: urgency doubles every 48h closer to deadline


def calculate_risk(
    difficulty: float,
    importance: float,
    ddl_time: datetime
) -> float:
    hours_left = (
        ddl_time - datetime.now(timezone.utc).replace(tzinfo=None)
    ).total_seconds() / 3600

    if hours_left <= 0:
        return 100.0

    # Exponential decay — pressure ramps up non-linearly as deadline approaches
    # hours_left=168 (1 week): urgency ≈ 8.8
    # hours_left=48  (2 days): urgency = 50
    # hours_left=24  (1 day):  urgency ≈ 70.7
    # hours_left=0:            urgency = 100
    urgency = 100 * (0.5 ** (hours_left / HALF_LIFE))

    score = (
        difficulty * 3 +
        importance * 3 +
        urgency * 0.4
    )

    return min(round(score, 2), 100.0)