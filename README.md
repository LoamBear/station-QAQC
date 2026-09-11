# station-QAQC

A config-driven quality-control engine for weather-station networks. The
engine itself names no variables, stations, or thresholds — every network
supplies that as config, and the same code runs against any of them.

**The package lives in [`wxqc_pipeline/`](wxqc_pipeline/).** Start there:

- **[`wxqc_pipeline/README.md`](wxqc_pipeline/README.md)** — full
  documentation: layout, config file formats, the QC levels (L1.5 → L2 → L3),
  flags, running the pipeline, the interactive L3 editor, and how to add a
  new network.
- **[`wxqc_pipeline/PIPELINE.md`](wxqc_pipeline/PIPELINE.md)** — a text map
  of every automated check, what's live today vs. planned.

[`wxqc_pipeline/config/nevcan/`](wxqc_pipeline/config/nevcan/) is the worked
example: a real network's config (not sample data), showing what plugging in
your own network looks like.
