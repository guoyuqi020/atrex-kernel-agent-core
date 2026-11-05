# Independent GPU Research-Claim Review

Review the one Direction event or Experiment in `submissions/target.json`. Treat Agent-written interpretations as claims, not facts. Use only the files in this evidence directory. Do not access the network, run code, modify evidence, or inspect parent directories.

Directly cited evidence may appear in:

- `submissions/direction-proposal.json`
- `submissions/supporting-experiments/`
- `gateway-records/`
- `references/`
- `kernels/`
- `evidence-manifest.json`

First classify the submission from `target.json`:

- A Direction `propose` or `revise` is an exploratory hypothesis, not a claim that its predicted mechanism or benefit has already been proven. Judge whether it is optimization-relevant, falsifiable, scoped, internally coherent, and paired with a bounded discriminating plan. Do not mark it unsupported merely because the proposed hypothesis still needs measurement. Treat asserted existing facts and claimed prior evidence strictly.
- A Direction terminal action or an Experiment records a conclusion. Audit every decision-relevant observation, numerical/comparative statement, causal or mechanistic explanation, scope claim, and recommended action strictly. A measured result does not establish a baseline-relative or causal claim unless the necessary comparison or isolation evidence is present.
- A lifecycle-only `start` is not a scientific claim and should not normally be sent for review.

Use exactly one verdict:

- `supported`: for an exploratory Direction, the hypothesis and plan meet the exploratory standard above and any asserted facts are supported; for a conclusion, every factual, numerical, comparative, causal, mechanistic, scope, and action claim is adequately supported. No material unsupported detail remains.
- `partially_supported`: a useful core conclusion or action is supported, but at least one claim is unsupported, overbroad, imprecise, or not isolated.
- `unsupported`: a decision-critical claim or action is contradicted or lacks necessary evidence, or the supplied evidence cannot justify a useful version of the conclusion.

Write exactly one JSON object to `review.json`:

```json
{
  "verdict": "supported | partially_supported | unsupported",
  "primary_issue": "one concise sentence",
  "findings": [
    {
      "claim": "claim being audited",
      "judgment": "supported | partially_supported | unsupported",
      "decision_critical": true,
      "reason": "evidence-based reason",
      "evidence": ["relative path or Gateway Record ID"]
    }
  ],
  "scope_safe_interpretation": "the strongest conclusion justified by the evidence",
  "next_check": "the smallest discriminating test",
  "confidence": "high | medium | low"
}
```

Keep at most six findings. `supported` requires every finding to be `supported`. `partially_supported` requires a useful supported core and at least one non-supported finding. `unsupported` requires at least one decision-critical unsupported finding. Do not include Markdown or any additional files.
