# Part 4: AI Assistant (Workstream 4)

Owner: Sowmya Rao

This part adds the AI layer on top of the rule-based analysis from Workstream 2:

1. **Plain-language explanations.** Every recommendation gets an `aiExplanation` written by Amazon Bedrock.
2. **A grounded chatbot.** Users ask questions about their own uploaded bill and get answers based only on their data.

The AI never calculates billing facts itself. All numbers (costs, savings, utilization) come from the deterministic rules in `analytics.py`. The model only rewrites and explains them.

## How it fits into the system

```text
Billing CSV
  -> parser.py + analytics.py        (WS2: validate, total, find waste)
  -> explanations.py                 (WS4: Bedrock fills aiExplanation)
  -> DynamoDB                        (AnalysisResults, Recommendations)
  -> chatbot/handler.py              (WS4: POST /chat, GET /chat/history)
  -> Bedrock answers using ONLY that organization's analysis
```

## Files

| File | Purpose |
|---|---|
| `backend/functions/process_billing_file/explanations.py` | Sends each validated recommendation to Bedrock and stores the result in `aiExplanation`. Uses template text if Bedrock fails. |
| `backend/functions/chatbot/chat.py` | Chatbot logic: builds the data context, calls Bedrock, checks numbers, builds the response. |
| `backend/functions/chatbot/handler.py` | AWS Lambda entry point for `POST /chat` and `GET /chat/history`. Reads and writes DynamoDB. |
| `backend/functions/chatbot/local_chat.py` | Try the chatbot on your own computer. |
| `tests/test_ai_assistant.py` | 16 automated tests (no AWS needed). |

## Model

- Amazon Bedrock, **Claude Haiku 4.5**, US cross-region inference profile
- Model ID: `us.anthropic.claude-haiku-4-5-20251001-v1:0` (override with the `BEDROCK_MODEL_ID` environment variable)
- Region: `us-east-1`
- Chosen because it is fast (about 1 second per answer) and low-cost, which suits short explanations and chat.
- The AWS account that runs the project must complete Anthropic's one-time use case form in the Bedrock console before the model can be called.

## Run it locally

From the repository's top folder, with AWS credentials that can use Bedrock:

```bash
# 1. Run Workstream 2's analysis on the sample bill
python3 -m backend.functions.process_billing_file.cli --file data/sample_billing.csv --output my_analysis.json

# 2. Generate AI explanations, then chat about the results (type 'quit' to exit)
python3 -m backend.functions.chatbot.local_chat --analysis my_analysis.json --explain
```

## Run the tests

No AWS account is needed. Bedrock and DynamoDB are replaced with fakes.

```bash
python3 -m pytest tests/test_ai_assistant.py -v
```

Expected result: `16 passed`.

## API

Both routes require Cognito authorization. Formats follow `data/chat_request.json` and `data/chat_response.json`.

### POST /chat

Request:

```json
{
  "analysisId": "analysis-337313c4f352",
  "question": "Which resources cost the most?",
  "conversationId": null,
  "includeHistory": true
}
```

Response (shortened):

```json
{
  "conversationId": "conv-1a2b3c4d5e6f",
  "analysisId": "analysis-337313c4f352",
  "answer": "Your highest-cost resource is gpu-research-001 at $1,800.00 ...",
  "confidence": 0.9,
  "sources": [
    {"sourceType": "ANALYSIS_SUMMARY", "sourceId": null, "label": "Analysis summary"},
    {"sourceType": "RECOMMENDATION", "sourceId": "gpu-research-001", "label": "UNDERUTILIZED_GPU recommendation for gpu-research-001"}
  ],
  "citedResources": ["gpu-research-001"],
  "suggestedFollowUps": ["Which recommendations have the highest potential savings?"],
  "disclaimer": "Savings are estimates based on your uploaded report. Verify before making changes.",
  "modelUsed": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
  "createdAt": "2026-11-22T20:00:00+00:00"
}
```

To continue a conversation, send the returned `conversationId` with the next question.

### GET /chat/history

Optional query parameter: `?conversationId=conv-...`. Returns `{"items": [...]}`, oldest first, for the signed-in organization only.

### Status codes

| Code | When |
|---|---|
| 200 | Answer returned (including fallback answers) |
| 400 | Missing `analysisId`, question shorter than 3 or longer than 2,000 characters, or invalid JSON |
| 401 | No organization identity in the Cognito token |
| 404 | The analysis does not exist **or belongs to another organization** (same message for both, so nothing leaks) |
| 500 | Unexpected error (details go to CloudWatch, not to the user) |

## How the answers stay grounded and safe

| Safeguard | How it works |
|---|---|
| Only the user's data | The organization comes from the Cognito token (`custom:organizationId`, else `sub`). An `organizationId` in the request body is ignored. Every DynamoDB query uses that organization as the partition key. |
| No invented numbers | The prompt forbids new numbers. After each answer, the code compares every number in the answer with the numbers in the data. If any is missing, confidence drops from 0.9 to 0.6 and a disclaimer is added. The warning is logged to CloudWatch. |
| Unknown questions | The model is told to answer "I don't have that information in your uploaded report." |
| Suggestions only | The prompt forbids saying anything was stopped, changed, or deleted. Savings are always called estimates. |
| Prompt injection | The prompt tells the model to ignore instructions inside the user's question that try to break the rules. |
| Bedrock outage | Explanations fall back to template text built from the rule result. Chat falls back to a summary built from the data (confidence 0.3). The dashboard keeps working. |
| Input limits | Questions must be 3 to 2,000 characters. Only the last 3 question-and-answer pairs are resent to the model. |
| Minimal data to the model | Explanations send only the finding's facts (no organization or record IDs). Chat sends a trimmed summary of one analysis, never the raw CSV. |
| Chat history failure | If saving history fails, the user still gets the answer. The error is logged. |

## What deployment needs (for Workstream 1)

**DynamoDB table:** `ChatHistory`, partition key `organizationId` (String), sort key `timestamp` (String). The code writes timestamps like `2026-11-22T20:00:00+00:00#a1b2c3` so two messages never collide.

**Chatbot Lambda:**

- Code folder: `backend/functions/chatbot`
- Handler: `handler.lambda_handler`
- Runtime: Python 3.12
- Timeout: 30 seconds (Bedrock usually answers in 1 to 3 seconds)
- Memory: 256 MB

**Environment variables:**

| Variable | Used by | Value |
|---|---|---|
| `ANALYSIS_RESULTS_TABLE` | chatbot | AnalysisResults table name |
| `RECOMMENDATIONS_TABLE` | chatbot | Recommendations table name |
| `CHAT_HISTORY_TABLE` | chatbot | ChatHistory table name |
| `BEDROCK_MODEL_ID` | both (optional) | defaults to `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `ENABLE_AI_EXPLANATIONS` | processing (optional) | `true` (default) or `false` to skip Bedrock |
| `CORS_ORIGIN` | chatbot (optional) | the CloudFront URL of the frontend |

**IAM permissions:**

- Chatbot Lambda: `dynamodb:GetItem` and `dynamodb:Query` on AnalysisResults and Recommendations; `dynamodb:Query` and `dynamodb:PutItem` on ChatHistory; Bedrock invoke (below).
- Processing Lambda: Bedrock invoke (below), in addition to its current permissions.

Bedrock invoke policy (replace `ACCOUNT_ID`). The US inference profile can route to several US regions, so the model ARN uses a region wildcard:

```json
{
  "Effect": "Allow",
  "Action": "bedrock:InvokeModel",
  "Resource": [
    "arn:aws:bedrock:us-east-1:ACCOUNT_ID:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0"
  ]
}
```

**API Gateway routes:** `POST /chat` and `GET /chat/history`, both with the Cognito authorizer, both pointing to the chatbot Lambda.

**Connecting explanations:** `pipeline.py` should call `add_explanations(analysis)` right after `analyze_records(...)` and before saving, in both `process_csv_file` and `process_csv_text`.

## Cost estimate

Bedrock prices for Claude Haiku 4.5 (on-demand): $1.00 per million input tokens and $5.00 per million output tokens. Check the AWS Bedrock pricing page before relying on these figures.

| Action | Approx. tokens | Approx. cost |
|---|---|---|
| One explanation | 340 in, 150 out | $0.001 |
| One uploaded bill (4 explanations) | | $0.005 |
| One chat question (including up to 3 earlier turns) | 2,500 in, 300 out | $0.004 |
| **Whole project demo and testing** (20 uploads, 150 questions) | | **under $1** |

Bedrock has no hourly charge: it costs nothing when nobody is using it. DynamoDB on-demand and Lambda costs for this demo are a few cents, and much of it may fall within the AWS free tier.

## Limitations

- **Savings are estimates.** They come from fixed savings rates in the rules (for example, 60% for a scheduled GPU), not from real AWS pricing.
- **The number check is simple.** It catches numbers that are not in the data, but it also flags harmless rounding (for example, "63%" when the data says 63.38%). Flagged answers are still shown, with lower confidence and a disclaimer, not blocked.
- **It cannot check wording.** If the model misdescribes a fact without using a number, the check will not catch it. The strict prompt and low temperature (0.1) reduce this risk.
- **One analysis at a time.** The chatbot answers about one uploaded report, not trends across several uploads.
- **English only**, and plain-text answers.
- **History is basic.** Only the last 3 question-and-answer pairs are used. Old history is never deleted (a time-to-live setting on the table could add this).
- **No streaming.** The full answer appears at once, usually after 1 to 3 seconds.
- **No automatic actions.** By design, the assistant only suggests. It cannot change any cloud resources.

## Cleanup

Bedrock itself has nothing to delete: there are no servers or stored models, and it only charges per request.

After the final demo:

1. Delete the whole stack with the infrastructure-as-code delete command (this removes the chatbot Lambda, the ChatHistory table, and the API routes along with everything else). See README section 19.
2. Delete the CloudWatch log groups for the chatbot and processing Lambdas if the stack does not remove them.
3. Delete any personal access keys or IAM users created for teammates.
4. Check **Billing and Cost Management** the next day to confirm costs have stopped.
