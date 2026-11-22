# Demo Script

Total time: about 8 minutes. Uses `data/sample_billing.csv` (October and November 2026, organization `org-demo`).

## Before the demo

- [ ] Sign in to the deployed website once to make sure the test user works.
- [ ] Open the AWS console in a second tab: CloudWatch -> Log groups.
- [ ] Have `data/sample_billing.csv` and `data/sample_billing_invalid.csv` ready to upload.
- [ ] Open a terminal in the project folder in case you need the local backup demo (end of this page).
- [ ] Run the tests once so the result is on screen: `python3 -m pytest tests/test_ai_assistant.py -v`

## Expected numbers (from the sample bill)

| Item | Value |
|---|---|
| Total cost (both months) | $2,840.00 |
| October / November | $1,419.00 / $1,421.00 |
| Estimated savings | $814.50 per month |
| Recommendations | 4 |
| Anomalies | 0 (November is only 0.14% higher than October) |

| Recommendation | Resource | Cost per month | Utilization | Estimated savings |
|---|---|---|---|---|
| Underutilized GPU | gpu-research-001 | $900.00 | 7% | $540.00 |
| Idle compute | i-website-001 | $144.00 | 5% | $136.80 |
| Oversized compute | i-analytics-001 | $288.00 | 20% | $115.20 |
| Rarely accessed storage | bucket-archive-001 | $75.00 | rare access, 500 GB | $22.50 |

## 1. The problem (30 seconds)

**Say:** "Small teams and research labs pay for cloud resources they forget about, like a GPU left running all month. The bill is hard to read, so nobody notices. Our platform reads the bill and explains, in plain language, where the money goes and how to spend less."

## 2. Sign in and upload (1 minute)

**Do:** Sign in. Upload `data/sample_billing.csv`.

**Say:** "Sign-in uses Amazon Cognito. The file goes straight to a private S3 bucket through a short-lived upload link, and that starts the processing workflow."

## 3. Dashboard (1.5 minutes)

**Do:** Point to the total, the service and project charts, and the recommendation list.

**Say:** "The numbers are calculated by fixed rules, not by AI, so they are exact and repeatable. Total cost is $2,840 across two months. The rules found 4 resources wasting money, worth an estimated $814.50 a month."

## 4. AI explanations (1 minute)

**Do:** Open the GPU recommendation and show its explanation.

**Say:** "The rule says: GPU, 7% utilization, $900 a month. Amazon Bedrock turns that into plain language for someone who isn't a cloud expert. It explains the problem, the suggested action, a risk, and what to check first. The AI only sees the validated finding, so it can't invent billing facts. If Bedrock is down, a template explanation is shown instead."

## 5. Chatbot (2 minutes)

Ask these questions in order:

| Ask | What to point out |
|---|---|
| Which resources cost the most? | Correct order: gpu-research-001 $1,800, i-analytics-001 $576, i-website-001 $288, bucket-archive-001 $150, lambda-api-001 $26 (two-month totals). Sources are listed. |
| What risks should I consider before stopping the GPU? | Uses the rule's own risks: delayed jobs and lost progress without checkpoints. |
| How could I reduce my monthly cost by about 30%? | Combines recommendations and calls the savings estimates. |
| What is my Azure spend? | Says the information is not in the report, instead of making something up. |

**Say:** "The chatbot only sees this organization's analysis. After every answer, our code checks each number against the data. If the AI uses a number that isn't in the report, the confidence drops and a warning is added."

## 6. Safety and reliability (1 minute)

**Do:** Switch to the CloudWatch tab and show the chatbot Lambda's log for the questions you just asked.

**Say:**
- "The organization is taken from the login token, never from the request, so one company can never see another's data. We have automated tests that prove this."
- "If Bedrock fails, the user still gets an answer built from the data."
- "The platform only suggests. It never stops or deletes anything."

**Optional:** Upload `data/sample_billing_invalid.csv` to show a clear validation error.

## 7. Tests and cost (30 seconds)

**Do:** Show the terminal with `16 passed`.

**Say:** "Workstream 4 has 16 automated tests, covering fallbacks, invalid input, and data isolation between organizations. Running the whole demo on Bedrock costs under a dollar."

## 8. Limitations and next steps (30 seconds)

**Say:** "Savings are estimates, not guarantees. Next steps would be real AWS Cost and Usage Reports, live CloudWatch utilization metrics, and trends across several uploaded bills."

## Backup: local demo (if the deployed site is not working)

```bash
python3 -m backend.functions.process_billing_file.cli --file data/sample_billing.csv --output my_analysis.json
python3 -m backend.functions.chatbot.local_chat --analysis my_analysis.json --explain
```

Then ask the same chatbot questions as in section 5.
