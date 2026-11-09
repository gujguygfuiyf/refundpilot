# Quick external usability check (n = 2)

To check whether the evidence-to-decision review drawer is understandable to first-time readers outside the team, we ran an informal qualitative check with **2 independent non-merchant testers**. Both participants inspected pre-recorded claims in `/merchant?tab=all` (they did not submit or process new live claims):

| Tester (Non-merchant) | Timed Inspection Task (~seconds) | Specific Task Outcome | Feedback Summary |
| :--- | :--- | :--- | :--- |
| **Tester 1** | ~20s to inspect drawer | Located suggested payout amount and read matched rule | Summarized feedback: noted that having structured visual observations paired with deterministic rules felt fast to scan, and asked how per-claim AI inference costs scale. |
| **Tester 2** | ~30s to inspect drawer | Located suggested payout amount and read matched rule | Summarized feedback: found the rule trace understandable, but noted that the interface felt utilitarian / engineering-heavy and recommended greater visual polish. |

- **Clarification on inference cost (answering Tester 1)**: Policy replay uses deterministic local rules and incurs no additional LLM API charges. Assessment cost depends on the configured model, input size, and any retries or fallback calls.
- **Scope limits & lack of market validation (answering Tester 2)**: This small informal check (`n = 2`, non-merchants) only demonstrates that two first-time reviewers could locate the decision inputs and rule recommendation in ~20–30 seconds when inspecting a pre-recorded case drawer. It does not measure merchant operational cycle time, proven time savings, merchant ROI, or commercial demand.
- **Action taken on Tester 1's cost question**: Documented that local policy replay adds no extra LLM API charges, and added configurable ops and dispute risk assumptions in the policy simulator (`/merchant/policy`) so merchants can model operational trade-offs across policies.
- **Action taken on Tester 2's UI polish feedback**: Adjusted the visual hierarchy of the Decision card and Merchant drawer based on this feedback (adding color-coded verdict banners, structured step cards for the 4-step rule trace, and copyable credential pills). These adjustments have not been formally re-tested with users.
