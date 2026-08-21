from ydcore.marketdata import MarketDataListener


def test_summary_prints_human_readable_average_absolute_latency(capsys, caplog):
    caplog.set_level("INFO")
    listener = MarketDataListener(max_quotes=10)
    listener.quote_count = 5
    listener.differences_ms = [-10, 20, -30, 40]

    assert listener.summary("IC2609") is True

    output = capsys.readouterr().out
    assert "=== 行情延迟测试汇总 ===" in output
    assert "收到行情: 5 条" in output
    assert "有效时间戳: 4 条" in output
    assert "平均延迟（绝对值）: 25.000 ms" in output
    assert "最小延迟（绝对值）: 10 ms" in output
    assert "最大延迟（绝对值）: 40 ms" in output
    assert "平均时间差（本机减行情）: +5.000 ms" in output
    assert "average_absolute_difference_ms=25.000" in caplog.text


def test_summary_prints_failure_when_no_timestamp_is_comparable(capsys):
    listener = MarketDataListener(max_quotes=10)
    listener.quote_count = 3

    assert listener.summary("IC2609") is False

    output = capsys.readouterr().out
    assert "收到行情: 3 条" in output
    assert "有效时间戳: 0 条" in output
    assert "结果: 失败" in output
