# AbstractNote backlog overview

Last reconciled: 2026-10-19 (evening pass)

## Counts
| State | Count |
|---|---|
| planned | 1 (0011, track `planned/v1/`) |
| proposed | 10 (0014-0023) |
| completed | 12 (track `completed/v1/`, items 0001-0010, 0012, 0013) |
| deprecated | 0 |
| recurrent | 1 (0901) |

Next free id: 0024.

## Priorities and recommended order
1. 0011 Corpus evaluation (finish grading, feed prompt findings into a tuning item)
2. Release preparation (proposed 0023): release abstractmemory `close-assertion`, the gateway seam, then abstractnote 0.1.0 and `@abstractframework/note`; name the versions in README/getting-started/pyproject
3. Operator rulings (0017), then the proposed items as decided

## Ledgers
### Planned
- [0011 Corpus evaluation](planned/v1/0011_corpus_evaluation_framework_history.md) (in progress: import done, processing and grading under way)

### Proposed
- [0014 iPhone app](proposed/0014_iphone_app.md)
- [0015 Upstream entity resolution to AbstractMemory](proposed/0015_upstream_entity_resolution_to_abstractmemory.md)
- [0016 Shared Swift gateway client package](proposed/0016_shared_swift_gateway_client_package.md)
- [0017 Operator rulings: credentials and priorities](proposed/0017_operator_rulings_credentials_and_priorities.md)
- [0018 Location capture and map view](proposed/0018_location_capture_and_map_view.md)
- [0019 Notifications for deep recall and insights](proposed/0019_notifications_for_deep_recall_and_insights.md)
- [0020 Gateway worker alternative](proposed/0020_gateway_worker_alternative_to_automation.md)
- [0021 Koine semantic model alignment](proposed/0021_koine_semantic_model_alignment.md)
- [0022 Shared and agent-owned notebooks](proposed/0022_multi_notebook_sharing_and_agent_notebooks.md)
- [0023 Release preparation for 0.1.0](proposed/0023_release_preparation_v0_1_0.md)

### Completed
- [0001 Notebook store and data model](completed/v1/0001_notebook_store_and_data_model.md) — completed 2026-10-19; see its completion report
- [0002 Capture, attachments and de-duplication](completed/v1/0002_capture_and_deduplication.md) — completed 2026-10-19; see its completion report
- [0003 Processing pipeline](completed/v1/0003_processing_pipeline_extraction_linking_revision.md) — completed 2026-10-19; see its completion report
- [0004 Queue, priorities and the processor automation](completed/v1/0004_queue_priorities_and_processor_automation.md) — completed 2026-10-19; see its completion report
- [0005 Flows bundle and headless runner](completed/v1/0005_flows_bundle_and_headless_runner.md) — completed 2026-10-19; see its completion report
- [0006 Recall tiers, then-versus-now and reflect](completed/v1/0006_recall_tiers_then_now_and_reflect.md) — completed 2026-10-19; see its completion report
- [0007 Gateway integration seam](completed/v1/0007_gateway_integration_seam.md) — completed 2026-10-19; see its completion report
- [0008 Web app](completed/v1/0008_web_app_capture_graph_remember.md) — completed 2026-10-19; see its completion report
- [0009 Apple Watch app](completed/v1/0009_apple_watch_app.md) — completed 2026-10-19; see its completion report
- [0010 Importers](completed/v1/0010_importers.md) — completed 2026-10-19; see its completion report
- [0012 Documentation core set](completed/v1/0012_documentation_core_set.md) — completed 2026-10-19; see its completion report
- [0013 CLI](completed/v1/0013_cli.md) — completed 2026-10-19; see its completion report

### Recurrent
- [0901 Backlog and docs hygiene](recurrent/0901_backlog_and_docs_hygiene.md)

## Operating rules
- Items are `NNNN_slug.md`; numbers are global and never reused; no dates in filenames.
- Every planned item carries `Current code reality` and `Validation`.
- Completion moves the file to `completed/` with a report and updates this overview in the same pass.
- Durable policy lives in [../adr/README.md](../adr/README.md), not here.
