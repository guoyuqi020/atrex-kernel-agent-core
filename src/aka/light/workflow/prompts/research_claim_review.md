# Independent GPU Research-Claim Review

Review the one Direction event or Experiment in `submissions/target.json`. Treat Agent-written interpretations as claims, not facts. Use only the files in this evidence directory. Do not access the network, run code, modify evidence, or inspect parent directories.

Directly cited evidence may appear in:

- `submissions/direction-proposal.json`
- `submissions/supporting-experiments/`
- `gateway-records/`
- `references/`
- `kernels/`
- `evidence-manifest.json`

First classify the submission from `target.json`, then apply the matching standard below. Review the decision the record is meant to support, not every incidental sentence with equal weight.

## Direction standard

- A Direction `propose` or `revise` asks whether a bounded investigation is worth doing. Mark it `supported` when the optimization target is relevant, the hypothesis is coherent and falsifiable, the applicability is reasonably scoped, and the plan can discriminate success from failure at bounded cost. The predicted mechanism and benefit are hypotheses, not facts that must already be proven. Unverified estimates, approximate magnitudes, and secondary mechanism details are non-decision-critical unless the proposal has no useful rationale without them.
- A terminal Direction asks whether the evidence justifies the stated `hypothesis_status` and the decision to close, defer, block, abandon, or derive follow-up work. Mark it `supported` when the cited measurements justify that decision within a defensible scope. Uncertain root-cause details, incomplete explanations, or imprecise benefit magnitudes do not downgrade the verdict when they do not change the terminal decision; narrow them in `scope_safe_interpretation` instead.
- Use `partially_supported` only when a decision-critical premise, stop-condition assessment, or closure decision remains genuinely uncertain but still has useful support. Use `unsupported` only when a decision-critical premise or action is contradicted or lacks enough evidence to justify the investigation or terminal decision.

## Experiment standard

- An Experiment asks whether the recorded observation and the resulting keep, repair, restore, or follow-up decision are trustworthy. Mark it `supported` when the candidate/source identity, relevant measurement or diagnostic, comparator when one is needed, and resulting action are sufficiently traceable and mutually consistent.
- Mechanistic interpretation is useful but not required for `supported`. A result may be supported even when the exact root cause, theoretical explanation, generality outside the measured scope, or precise expected magnitude remains uncertain. Preserve those limitations in `scope_safe_interpretation` and `next_check` without downgrading the whole Experiment.
- Use `partially_supported` when a decision-critical measurement, comparator, attribution, candidate identity, or action is incomplete or ambiguous but the Experiment still contains a useful observation. Use `unsupported` only when the core result or action is contradicted, untraceable, or not justified by the supplied evidence.

A lifecycle-only Direction `start` is not a scientific claim and should not normally be sent for review.

Use exactly one verdict:

- `supported`: every decision-critical finding is supported under the applicable Direction or Experiment standard. Non-decision-critical limitations may remain and must be stated without lowering the verdict.
- `partially_supported`: no decision-critical finding is unsupported, but at least one decision-critical finding is only partially supported.
- `unsupported`: at least one decision-critical finding is unsupported.

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

Keep at most six findings and include at least one decision-critical finding. Only decision-critical findings determine the overall verdict: `supported` requires all of them to be supported; `partially_supported` requires at least one partially supported decision-critical finding and no unsupported decision-critical finding; `unsupported` requires at least one unsupported decision-critical finding. Non-decision-critical findings may be partial or unsupported under an overall `supported` verdict. Do not include Markdown or any additional files.
