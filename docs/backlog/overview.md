# AbstractNote backlog overview

Last reconciled: 2026-10-20 (afternoon)

## Counts
| State | Count |
|---|---|
| planned | 1 (track `planned/v1/`: 0029) |
| proposed | 10 |
| completed | 21 (track `completed/v1/`) |
| deprecated | 0 |
| recurrent | 1 (0901) |

Next free id: 0033.

## Priorities and recommended order
1. The global data check of the 1,000-note pass (untracked/eval2/REPORT.md): apply-time guards and the false-cue chain landed (W23, W24); prompt-level quality (summaries, predicates, seats vs packages) and the retrospective-ingestion gate are in flight
2. 0029 Resume the ingest only after the quality lanes are graded (the one-liner stays the resume path)
3. Release preparation (proposed 0023): release abstractmemory `close-assertion`, the gateway seam, then abstractnote 0.1.0 and `@abstractframework/note`; name the versions in README/getting-started/pyproject
4. Operator rulings (0017), then the proposed items as decided

## Ledgers
### Planned
- [0029 Ingest the rest of the framework's history into the operator's notebook](planned/v1/0029_ingest_the_rest_of_the_framework_history.md)

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
- [0001 Notebook store and data model](completed/v1/0001_notebook_store_and_data_model.md) — completed 2026-10-19
- [0002 Capture, attachments and de-duplication](completed/v1/0002_capture_and_deduplication.md) — completed 2026-10-19
- [0003 Processing pipeline: context, extraction, linking, revision](completed/v1/0003_processing_pipeline_extraction_linking_revision.md) — completed 2026-10-19
- [0004 Queue, priorities and the processor automation](completed/v1/0004_queue_priorities_and_processor_automation.md) — completed 2026-10-19
- [0005 Flows bundle and headless runner](completed/v1/0005_flows_bundle_and_headless_runner.md) — completed 2026-10-19
- [0006 Recall tiers, then-versus-now and reflect](completed/v1/0006_recall_tiers_then_now_and_reflect.md) — completed 2026-10-19
- [0007 Gateway integration seam (tools binding, read API, app catalog)](completed/v1/0007_gateway_integration_seam.md) — completed 2026-10-19
- [0008 Web app: Capture, Graph, Remember](completed/v1/0008_web_app_capture_graph_remember.md) — completed 2026-10-19
- [0009 Apple Watch app](completed/v1/0009_apple_watch_app.md) — completed 2026-10-19
- [0010 Importers: Claude sessions, agora hub, Markdown, files](completed/v1/0010_importers.md) — completed 2026-10-19
- [0011 Corpus evaluation: the framework's own history](completed/v1/0011_corpus_evaluation_framework_history.md) — completed 2026-10-19
- [0012 Documentation core set and llms files](completed/v1/0012_documentation_core_set.md) — completed 2026-10-19
- [0013 CLI](completed/v1/0013_cli.md) — completed 2026-10-19
- [0024 Processing quality and speed on a local model](completed/v1/0024_processing_quality_and_speed.md) — completed 2026-10-19
- [0025 Web app: design and experience pass](completed/v1/0025_web_design_and_experience_pass.md) — completed 2026-10-19
- [0026 Server gaps behind the web design pass: review, merged entries, cue evidence, archive snapshot, throughput](completed/v1/0026_server_gaps_behind_the_web_design_pass.md) — completed 2026-10-19
- [0027 Importer fidelity: faithful dates, prompt authorship, agora selection](completed/v1/0027_importer_fidelity_dates_outcomes_agora_selection.md) — completed 2026-10-19
- [0028 Recall relevance, the standard packet budget, contradiction candidates and read-only reads](completed/v1/0028_recall_relevance_budget_and_read_paths.md) — completed 2026-10-19
- [0030 Graph communities, their labels, and statistics](completed/v1/0030_graph_communities_labels_and_statistics.md) — completed 2026-10-19
- [0031 Processing page backend](completed/v1/0031_processing_page_backend.md) — completed 2026-10-20
- [0032 Memory consolidation: a scheduled, triggerable, observable pass over the notebook](completed/v1/0032_memory_consolidation.md) — completed 2026-10-20

### Recurrent
- [0901 Backlog and docs hygiene](recurrent/0901_backlog_and_docs_hygiene.md)

## Operating rules
- Items are `NNNN_slug.md`; numbers are global and never reused; no dates in filenames.
- Every planned item carries `Current code reality` and `Validation`.
- Completion moves the file to `completed/` with a report and updates this overview in the same pass.
- Durable policy lives in [../adr/README.md](../adr/README.md), not here.
