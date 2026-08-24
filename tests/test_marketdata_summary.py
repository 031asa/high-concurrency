from datetime import datetime, timedelta, timezone

from ydcore.marketdata import MarketDataListener


CHINA_TIME = timezone(timedelta(hours=8))


def test_summary_excludes_first_quote_and_prints_average_and_median(capsys, caplog):
    caplog.set_level("INFO")
    listener = MarketDataListener(max_quotes=10)
    listener.quote_count = 5
    listener.differences_ms = [-10, 20, -30, 40]

    assert listener.summary("IC2609") is True

    output = capsys.readouterr().out
    assert "=== 行情延迟测试汇总 ===" in output
    assert "收到行情: 5 条" in output
    assert "有效时间戳: 4 条" in output
    assert "统计口径: 已剔除首条有效行情" in output
    assert "用于统计: 3 条" in output
    assert "平均延迟（绝对值，剔除首条后）: 30.000 ms" in output
    assert "中位延迟（绝对值，剔除首条后）: 30.000 ms" in output
    assert "最小延迟（绝对值）: 20 ms" in output
    assert "最大延迟（绝对值）: 40 ms" in output
    assert "平均时间差（本机减行情）: +10.000 ms" in output
    assert "excluded_initial_quotes=1 measured_quotes=3" in caplog.text
    assert "average_absolute_difference_ms=30.000" in caplog.text
    assert "median_absolute_difference_ms=30.000" in caplog.text


def test_summary_prints_failure_when_no_timestamp_is_comparable(capsys):
    listener = MarketDataListener(max_quotes=10)
    listener.quote_count = 3

    assert listener.summary("IC2609") is False

    output = capsys.readouterr().out
    assert "收到行情: 3 条" in output
    assert "有效时间戳: 0 条" in output
    assert "结果: 失败" in output


def test_summary_fails_cleanly_when_only_initial_quote_is_comparable(capsys, caplog):
    caplog.set_level("INFO")
    listener = MarketDataListener(max_quotes=10)
    listener.quote_count = 1
    listener.differences_ms = [1046]

    assert listener.summary("IC2609") is False

    output = capsys.readouterr().out
    assert "有效时间戳: 1 条" in output
    assert "统计口径: 已剔除首条有效行情" in output
    assert "用于统计: 0 条" in output
    assert "剔除首条后没有可统计的行情时间戳" in output
    assert "reason=NO_TIMESTAMP_AFTER_INITIAL_QUOTE" in caplog.text


def test_summary_prints_15_minute_bins_and_writes_csv(tmp_path, capsys):
    listener = MarketDataListener(max_quotes=10)
    listener.quote_count = 5
    listener.differences_ms = [-10, 20, -30, 40, 50]
    listener.received_times = [
        datetime(2026, 8, 21, 9, 44, 59, tzinfo=CHINA_TIME),
        datetime(2026, 8, 21, 9, 45, 1, tzinfo=CHINA_TIME),
        datetime(2026, 8, 21, 9, 46, 1, tzinfo=CHINA_TIME),
        datetime(2026, 8, 21, 9, 59, 59, tzinfo=CHINA_TIME),
        datetime(2026, 8, 21, 10, 0, 0, tzinfo=CHINA_TIME),
    ]
    output_csv = tmp_path / "latency-bins.csv"

    assert listener.summary("IC2609", 15, output_csv) is True

    output = capsys.readouterr().out
    assert "=== 15分钟行情时间差分箱（绝对值，ms） ===" in output
    assert "time_bin" in output
    assert "2026-08-21 09:45:00+08:00" in output
    assert "2026-08-21 10:00:00+08:00" in output
    assert "30.000000" in output
    assert "10.000000" in output
    assert "39.000" in output
    csv_text = output_csv.read_text(encoding="utf-8")
    assert "time_bin,count,mean,std,p95,max" in csv_text
    assert "2026-08-21T09:45:00+08:00,3,30.000000,10.000000,39.000,40.000" in csv_text
