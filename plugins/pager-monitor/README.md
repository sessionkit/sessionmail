# sessionmail-pager-monitor (retired)

**Retired: the root plugin arms the monitor.** The sessionmail
plugin at the repo root now ships `monitors/monitors.json`, which runs
`hooks/pager-monitor`: `pager listen --monitor`, from PATH, on by default.

This directory stays for one release, because live launches still pass it
as a `--plugin-dir`. Its `monitors/monitors.json` is an empty list, so it
arms nothing. Loading it beside the root plugin would otherwise arm a
second monitor: the keys `sessionmail:pager` and
`sessionmail-pager-monitor:pager` differ.

Drop the `--plugin-dir <checkout>/plugins/pager-monitor` from your launch
when convenient. A later card deletes this directory.
