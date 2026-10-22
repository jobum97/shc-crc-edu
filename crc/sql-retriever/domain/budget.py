"""승인된 단계 시간 상한과 남은 예산을 순수 계산함."""
FIXED = (1.0, 1.0, 1.0, 2.0, 8.0, 1.0)
NL2SQL = (1.0, 16.0, 1.0, 2.0, 8.0, 1.0)


def can_start(stage: int, elapsed: float, route: str | None) -> bool:
    """종료와 미실행 외부 호출을 예약한 두 검사를 함께 평가함."""
    if stage == 6:
        return True
    external = 15.0 if stage <= 2 and route != 'fixed' else 0.0
    path = FIXED if route == 'fixed' else NL2SQL
    return 30.0 - elapsed - 1.0 - external >= 1.5 and 30.0 - elapsed >= sum(path[stage - 1:])


def stage_limit(stage: int, route: str | None) -> float:
    """선택 경로의 현재 단계 상한을 초 단위로 반환함."""
    return (FIXED if route == 'fixed' else NL2SQL)[stage - 1]
