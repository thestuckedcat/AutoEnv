# Product Log Source Configuration Implementation Plan

> **For agentic workers:** Implement the approved conversation design task by task; use isolated file ownership and independent code review. Steps use checkbox syntax for tracking.

**Goal:** Move existing product-log selection and recognition rules into visible settings, bind sources to parser profiles, parse only configured rules, and resolve ancestor directory matches consistently in Python and offline HTML.

**Architecture:** A packaged `product_log/default_config.json` is the single initial configuration for Python and the generated offline page. An explicit `analysis_config` snapshot defines sources, parser profiles, category/component rules, module aliases, firmware labels and module mappings. The three business categories remain user_driver, kernel_driver and firmware; engines retain stateful record framing and firmware stitching.

**Tech Stack:** Python stdlib/SQLite, browser JavaScript workers, pytest and real Node/browser fixtures.

**Spec:** The user-approved design in this conversation: named selectable sources, source-bound parsers, no hidden fallback for removed rules, settings-only configuration, and ancestor-aware directory selection.

## Global Constraints

- Preserve the sole Web launcher `python -X utf8 startWeb.py` and endpoint `http://127.0.0.1:8765/`.
- Keep input files immutable; retain provenance boundaries structurally, including literal `!/` names.
- Initial rules are extracted from current code. Existing broad recognition is represented by explicit `any` rules, not implicit parser fallback. Removing those rules disables output.
- No invented kbox format/default: users may configure a source against a supported parser; its real format needs a matching profile.
- Source options belong to rule `source_ids`; sources not referenced by any rule are not parsed.
- Configuration editor and source options appear only inside Settings; normal import and browsing stay compact.

## Review Focus

- Mixed diag user/firmware sources must read once and retain firmware continuations before filtering.
- Empty config/rules and invalid references must not silently restore defaults.
- Shared files with incompatible parser profiles must fail explicitly.
- Different directory branches must remain ambiguous even when one path is shorter.
- Bundled defaults and timestamps must remain identical across Python and offline workers.

## Shared Configuration Contract

- `version: 1`.
- `sources`: array of `{id, name, directory_patterns: [str], file_patterns: [str], file_regex: str, parser_id: str, recursive: bool, order: {type: "name"|"current_last"|"numeric_rotation", current: str, pattern: str}}`. Ordered directory patterns express explicit absence fallback. Empty file_regex means no extra constraint. Source options are selected through rule source_ids.
- `parsers`: array of `{id, engine: "diag"|"kernel", time_format: "iso"|"dmesg"|"none", patterns: {name: regex}, months: [str]}`. Regex patterns use portable numbered capture groups. Diag patterns: header (time, host), outer_shape, slot, cpu, module, service, info, firmware (device, type, seg, body), firmware_marker, uptime. Kernel: date (month, day, clock, fraction, year). No regex-specific named groups.
- `rules`: array of `{id, category: "user_driver"|"kernel_driver"|"firmware", component: str, source_ids: [str], match: {field: "raw_text"|"module_name"|"log_type"|"device_id"|"slot_id"|"cpu_id"|"text", operator: "any"|"equals"|"contains"|"in"|"regex", value?: str, values?: [str], ignore_case?: bool}}`. Multiple matching rules retain one source record. Rules gate completed records, not raw lines.
- `module_aliases`: array of `{pattern, replacement, ignore_case}`; `firmware_types`: object; `modules`: existing `{module,user_module_name,kernel_keyword}` array. These are explicit display/query settings; no automatic module discovery.
- Python `normalize_config(value=None) -> dict`, `default_config() -> dict`, `active_sources(config) -> list[dict]`, `record_enabled(config, source_ids, category, metadata, raw, body) -> bool`, `normalize_module(value, config=None) -> str`, `firmware_type_name(value, config=None) -> str` in `product_log/configuration.py`.
- JavaScript `ProductLogConfig` exposes equivalent `normalize`, `defaults`, `activeSources`, `recordEnabled`, `normalizeModule`, `firmwareTypeName` in `product_log/standalone/configuration.js`. The builder injects the same JSON as `globalThis.ProductLogDefaultConfig` before configuration.js in worker and app.
- `run_analysis(..., analysis_config=None)` and offline prepare rules.analysis_config accept config; sources carry `configured_source_ids`, `parser_profile`, and `analysis_config`. Offline `parseSources(sources, analysisConfig)` receives the snapshot. No configuration mutation during parsing.

### Task 1: Explicit Configuration Contract

**Files:** default_config.json, configuration.py, standalone/configuration.js, tests/test_product_log_configuration.py.
- [x] Add failing validation/empty configuration/rule selection tests; run and confirm failures.
- [x] Extract defaults and implement matching/validation consistently in Python and JavaScript.
- [x] Verify references, regexes, engine/category compatibility, duplicate IDs, source selections, independent clones and no fallback for empty lists.

### Task 2: Source Collection and Stateful Parsing

**Files:** workflow.py, parser.py, standalone/archive.js, standalone/parser.js, standalone/worker.js, focused Python/JS tests.
- [x] Add failing tests for ancestor candidates, separate branches, archive-only fallback, explicit source selection and source-bound parser timestamps.
- [x] Resolve base-layer candidates first, choose a unique containing ancestor, preserve genuine ambiguity, fall back to archive-only candidates when absent.
- [x] Collect only selected sources with configured patterns/order; share equal-profile physical files and reject incompatible profiles.
- [x] Parse via configured profiles, filter complete records, preserve firmware segment boundaries and source identities.
- [x] Run focused parity tests and existing archive/parser regressions.

### Task 3: Settings, Public Entry Points and Packaging

**Files:** static/standalone HTML/app/CSS, web.py, __main__.py, module_map.py, index.py, builder, pyproject.toml, UI/API tests.
- [x] Add failing Settings/configuration route, CLI and standalone bundle tests.
- [x] Put existing advanced controls and module editor plus config/source editing in a closed Settings dialog; persist/download/import explicit configuration, validate before import.
- [x] Pass snapshot through HTTP, CLI and workers; package JSON and embed it in offline HTML.
- [x] Move module aliases/firmware display labels to configuration and remove automatic module insertion.
- [x] Verify real browser Settings interaction, source selection, invalid JSON, import and log browsing.

### Task 4: Review and Verification

**Files:** product_log/README.md, standalone/README.md, tests/README.md, newFeature/2026-10-20-product-log-source-configuration.md.
- [x] Update documentation and feature record with semantics and real-sample limitations.
- [x] Run focused tests, build, compilation, registration/skill checks and full offline suite.
- [x] Independent review, repair findings, re-run affected checks and report exact outcomes.

On 2026-10-21 the user explicitly requested completion, a Git commit, branch push and GitHub PR. No real-device run is requested. Final full suite: 978 passed, 2 Windows symlink-permission skips; HTML/wheel, browser settings, static contracts and review repairs verified.
