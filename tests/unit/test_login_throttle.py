from app.services.login_throttle import LoginThrottle


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_locks_after_the_limit_and_unlocks_when_the_oldest_failure_leaves_the_window() -> None:
    clock = Clock()
    throttle = LoginThrottle(max_failures=3, window_seconds=60, clock=clock)
    for step in range(3):
        assert throttle.retry_after("ann") is None
        throttle.record_failure("ann")
        clock.now += 10
    assert throttle.retry_after("ann") == 30
    clock.now += 30
    assert throttle.retry_after("ann") is None


def test_usernames_are_counted_apart_ignoring_case_and_spaces() -> None:
    throttle = LoginThrottle(max_failures=2, clock=Clock())
    throttle.record_failure("Ann")
    throttle.record_failure(" ann ")
    assert throttle.retry_after("ANN") is not None
    assert throttle.retry_after("bob") is None


def test_success_forgets_the_failures() -> None:
    throttle = LoginThrottle(max_failures=1, clock=Clock())
    throttle.record_failure("ann")
    throttle.record_success("ann")
    assert throttle.retry_after("ann") is None
