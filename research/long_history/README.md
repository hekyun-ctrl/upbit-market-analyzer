# External perpetual validation evidence

Read `docs/lbank_long_history_validation_2026-10-20.md` first. None of these
candidates passed promotion. Production is not changed by these scripts.

`protocol_20261020.json` and `entry_location_protocol_20261020.json` are the
original preregistrations; retain their hashes. The second experiment's 2026
results are exploratory because that year was already observed. The separate
2023 evaluation is a historical robustness check, not prospective evidence.

`results/` contains all aggregate outcomes and verified archive URLs/hashes.
The downloadable evidence package also includes:

- `ledgers/initial/selected_trades.csv.gz`: selected portfolios, with an
  `evaluation` column; the same candidate can occur in multiple portfolios.
  Do not concatenate the portfolios and call the sum one account's profit.
- `ledgers/initial/labelled_development_and_test.csv.gz`: all eligible candidate
  labels, including overlaps. These are not independent realized trades.
- `ledgers/preplanned_2024_2026/preplanned_trades.csv.gz` and
  `ledgers/preplanned_2023/preplanned_trades.csv.gz`: hypothetical fills by
  independent family; `family` 0,1,2 corresponds to `entry_location.NAMES`.
- `evidence_manifest.json`: hashes of every included payload file.

The package intentionally excludes the large original market archives. Download
them using `download_binance.py` and compare to the recorded official SHA256s.
No exchange API key, Telegram credentials, private database, or user's actual
position records are required or included. Python 3.12.14 was used.

The final packaging revision only made gzip timestamps deterministic, copied
the unchanged `NAMES` and Wilson formula locally to remove production imports,
and added descriptive price-vs-net decomposition. It did not fit thresholds or
alter trades after viewing outcomes. See `packaging_audit.json`.

Run commands from the package/repository root with `PYTHONPATH=.`. Install the
four pinned analysis dependencies in `requirements.txt`. The 16 included tests
check causal implementation; they are not performance certification.
