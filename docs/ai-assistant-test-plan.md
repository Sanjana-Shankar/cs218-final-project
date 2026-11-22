# Workstream 4 Test Plan and Results

## Automated tests

Run from the repository's top folder: `python3 -m pytest tests/test_ai_assistant.py -v`

Last run: November 22, 2026. Result: **16 passed**.

| # | Test | What it proves | Result |
|---|---|---|---|
| 1 | test_explanation_uses_bedrock_and_sends_only_validated_facts | Explanations come from Bedrock, and only the finding's facts are sent (no organization or record IDs) | Pass |
| 2 | test_explanation_falls_back_when_bedrock_fails | A Bedrock failure gives a template explanation instead of an error | Pass |
| 3 | test_add_explanations_fills_every_recommendation_without_new_fields | Every recommendation gets `aiExplanation`, with no fields outside the agreed schema | Pass |
| 4 | test_chat_sends_only_this_analysis_and_matches_response_schema | The model receives only this analysis; the response has all required fields and sources | Pass |
| 5 | test_invented_numbers_lower_confidence_and_add_disclaimer | Numbers not in the data lower confidence to 0.6 and add a disclaimer | Pass |
| 6 | test_chat_falls_back_when_bedrock_fails | A Bedrock failure gives a data-based answer (confidence 0.3) | Pass |
| 7-10 | test_invalid_questions_are_rejected (4 cases) | Empty, too short, too long (over 2,000 characters), and missing questions are rejected | Pass |
| 11 | test_history_is_sent_to_the_model | Earlier questions and answers are included for follow-up questions | Pass |
| 12 | test_handler_answers_and_saves_history | POST /chat answers and saves history; GET /chat/history returns it | Pass |
| 13 | test_other_organization_cannot_read_this_analysis_or_history | Another organization gets 404, its history is empty, and Bedrock is never called with the other organization's data | Pass |
| 14 | test_organization_in_request_body_is_ignored | Putting another organization's ID in the request body does not give access | Pass |
| 15 | test_missing_token_is_rejected | Requests without an organization identity get 401 | Pass |
| 16 | test_bad_json_is_rejected | Invalid request bodies get 400 | Pass |

## Manual tests with real Bedrock (local)

Run on November 21, 2026 against Workstream 2's output for `data/sample_billing.csv`, using Claude Haiku 4.5 in us-east-1.

| # | Test | Expected | Actual | Result |
|---|---|---|---|---|
| M1 | Generate explanations for all 4 recommendations | 4 plain-text explanations using only the finding's numbers, savings called estimates, a risk and a check included | As expected | Pass |
| M2 | Ask "Which resources cost the most?" | gpu-research-001 $1,800, i-analytics-001 $576, i-website-001 $288, bucket-archive-001 $150, lambda-api-001 $26 | Correct list and order. Also said "over 63%" (rounded from 63.38%), which the number check flagged (confidence 0.6) | Pass (check worked as designed) |
| M3 | Ask "What risks should I consider before stopping the GPU?" | Uses the rule's risks and verification step | Listed delayed jobs, lost progress without checkpoints, and checks to make first (confidence 0.9) | Pass |
| M4 | Ask about data not in the report ("What is my Azure spend?") | Says the information is not in the report | Said "I don't have that information in your uploaded report" (tested on demo data, November 21) | Pass |

## Still to test after deployment

These need the shared AWS environment from Workstream 1 and the frontend from Workstream 3.

| # | Test | Expected | Result |
|---|---|---|---|
| D1 | POST /chat without a Cognito token | 401 from API Gateway | Not yet run |
| D2 | Full flow: sign in, upload, dashboard, chatbot | Explanations on the dashboard; chatbot answers with report values | Not yet run |
| D3 | Two users in different organizations | User B cannot see User A's analysis or chat history | Not yet run |
| D4 | Remove the Lambda's Bedrock permission temporarily | Dashboard still loads with template explanations; chat returns the fallback answer | Not yet run |
| D5 | CloudWatch logs | Chatbot requests, fallbacks, and number-check warnings are visible | Not yet run |
| D6 | Chat history survives a page reload | GET /chat/history returns earlier questions | Not yet run |
