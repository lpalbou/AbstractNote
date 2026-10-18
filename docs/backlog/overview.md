# AbstractNote backlog overview

Last reconciled: 2026-10-19

## Counts
| State | Count |
|---|---|
| planned | 13 (track `planned/v1/`, items 0001-0013) |
| proposed | 9 (0014-0022) |
| completed | 0 |
| deprecated | 0 |
| recurrent | 1 (0901) |

Next free id: 0023.

## Priorities and recommended order
1. 0001 Notebook store and data model (everything depends on it)
2. 0002 Capture, 0003 Processing pipeline, 0004 Queue (parallel, same package, disjoint modules)
3. 0005 Flows bundle and headless runner, 0006 Recall tiers and reflect
4. 0007 Gateway integration seam (abstractgateway branch)
5. 0008 Web app and 0009 Apple Watch app (parallel; mock-first), 0010 Importers
6. 0011 Corpus evaluation on the framework's own history
7. 0012 Documentation core set, 0013 CLI (grows throughout)

## Ledgers
### Planned
- [0001 Notebook store and data model](planned/v1/0001_notebook_store_and_data_model.md)
- [0002 Capture, attachments and de-duplication](planned/v1/0002_capture_and_deduplication.md)
- [0003 Processing pipeline](planned/v1/0003_processing_pipeline_extraction_linking_revision.md)
- [0004 Queue, priorities and the processor automation](planned/v1/0004_queue_priorities_and_processor_automation.md)
- [0005 Flows bundle and headless runner](planned/v1/0005_flows_bundle_and_headless_runner.md)
- [0006 Recall tiers, then-versus-now and reflect](planned/v1/0006_recall_tiers_then_now_and_reflect.md)
- [0007 Gateway integration seam](planned/v1/0007_gateway_integration_seam.md)
- [0008 Web app](planned/v1/0008_web_app_capture_graph_remember.md)
- [0009 Apple Watch app](planned/v1/0009_apple_watch_app.md)
- [0010 Importers](planned/v1/0010_importers.md)
- [0011 Corpus evaluation](planned/v1/0011_corpus_evaluation_framework_history.md)
- [0012 Documentation core set](planned/v1/0012_documentation_core_set.md)
- [0013 CLI](planned/v1/0013_cli.md)

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

### Completed
(none yet)

### Recurrent
- [0901 Backlog and docs hygiene](recurrent/0901_backlog_and_docs_hygiene.md)

## Operating rules
- Items are `NNNN_slug.md`; numbers are global and never reused; no dates in filenames.
- Every planned item carries `Current code reality` and `Validation`.
- Completion moves the file to `completed/` with a report and updates this overview in the same pass.
- Durable policy lives in [../adr/README.md](../adr/README.md), not here.
