## Experiment records

Experiment tools are enabled; Direction tools are disabled. At the start, call `list-experiments` and load only entries relevant to the current Kernel or likely bottleneck. After each decisive measurement, call `record-experiment` with `name`, a concrete question in `intent`, the relevant `gateway_record_ids`, and a bounded `interpretation`; omit `direction_id`. Runtime derives source, Kernel, correctness, latency, and failure facts from the cited records, so use `record-read` instead of restating them.
