from scripts.run_paper_evaluator import retry_delay


def test_retry_delay_is_exponential_jittered_and_capped():
    assert retry_delay(10, 1, jitter=1.0) == 10
    assert retry_delay(10, 2, jitter=1.0) == 20
    assert retry_delay(10, 2, jitter=0.8) == 16
    assert retry_delay(300, 99, jitter=1.2) == 300
