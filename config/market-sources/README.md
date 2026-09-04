# Independent market-source configuration

Pass one JSON file for every source process. Relative paths inside a file are
resolved from the project root. Account secrets stay in separately protected
files and must not be embedded in a source configuration.

Deterministic smoke run:

```bash
bash scripts/run_multi_source_aeron_mvp.sh \
  --source-config \
    config/market-sources/synthetic-sim-a.json \
    config/market-sources/synthetic-sim-b.json \
  --count 1000
```

Quick live callback acceptance uses the same launcher and stops after one
callback from each source:

```bash
bash scripts/run_multi_source_aeron_mvp.sh \
  --source-config \
    /secure/market-sources/ctp-live.json \
    /secure/market-sources/ydapi-main.json \
  --count 1
```

Real-source example files have the `.example.json` suffix. Copy them to
deployment-owned paths, replace placeholders, and pass the resulting files after
one `--source-config`. One enabled file is valid; add as many files as required.
The repeated `--source-config <file>` form remains compatible. Do not commit
account credentials.

Common fields:

- `schema_version`: must be `1`.
- `name`: unique source label written to `MarketSource`.
- `kind`: `synthetic`, `ctp`, or `ydapi`.
- `enabled`: optional boolean; disabled files are ignored.
- `repeat`: source-packet expansion used for load tests; production should use `1`.
- `source_timeout_seconds`: per-source idle timeout. Synthetic acceptance defaults to
  `60`; CTP/YDApi defaults to `86400` so lunch breaks and overnight market closures do
  not look like transport failures. Production may set an explicit value for its
  trading calendar.

CTP additionally requires `front`, `api_kind`, `latency_mode`, and a non-empty
`instruments` array. Its optional `python` overrides the automatic
`result/ctp-tts-runtime` or `result/ctp-live-runtime` selection. YDApi requires
`account_config`, `api_config`, and `instrument`; it reuses the active
`ydtrader-high-concurrency` Conda Python unless optional `python` overrides it.
`startup_timeout_seconds` is also optional.

Legacy `--sources` and its shared CTP/YDApi flags remain available for backward
compatibility, but they cannot be mixed with `--source-config`.
