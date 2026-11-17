Patient history (synthetic data only)

GET /patients/{id}/assistant/history returns the signed-in person's messages in chronological order,
including the boundary exactly 90 days before the injected wall clock. Current patient-page scope
is checked first. Each read is audited. The assistant_message repository prunes older rows on reads
and writes; an idle database is not purged by a background scheduler. Existing patient-question list
and detail routes also enforce ownership and the 90-day cut. Existing job records are not backfilled.

GET /patients/{id}/lab/runs lists completed compare runs newest first; GET of /{lab_run_id} reads
one of those results. Both use current patient-page scope and audit every read. Neither mutates runs.

The optional compare.json field patient_refs is an array of {"run_id": 1, "patient_id": "p-001"}.
The run_id is the source ward run, not the lab job ID. The ingest worker must supply these explicit
links when its result includes those synthetic patient stays. Each link must name a stored patient
in the requested unit. Omission stays compatible with old workers and leaves the result unlinked.
No patient membership is inferred from seeds or matching IDs. A reused ID in a later ward run cannot
read the earlier stay's messages or simulated runs. The external worker was not changed in this task.
