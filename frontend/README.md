# Frontend (Workstream 3)

React web app for the Cloud Cost Optimizer. Users sign in, upload a billing CSV, see their cost dashboard and recommendations, and ask the AI assistant questions about their bill.

**Stack:** React 18 + Vite, AWS Amplify (Cognito sign-in only), Recharts. Hosted on S3 + CloudFront.

## Run locally

```bash
cd frontend
npm install
npm run dev        # http://localhost:5174
```

With no `.env` values the app runs in **demo mode**: sign-in is skipped, uploads are simulated, and the dashboard and chatbot use `src/sampleAnalysis.json`. A yellow banner shows when demo mode is on.

## Configuration

Copy `.env.example` to `.env` and fill in values from the backend stack outputs:

| Variable | Where to find it | If empty |
|---|---|---|
| `VITE_AWS_REGION` | Region the backend is deployed to | — |
| `VITE_COGNITO_USER_POOL_ID` | Cognito → User pools → User pool ID | Sign-in is skipped |
| `VITE_COGNITO_CLIENT_ID` | Cognito → App integration → App client ID (**must have no client secret**) | — |
| `VITE_API_BASE_URL` | API Gateway → Stages → Invoke URL | Demo mode (mock API) |

Values are baked in at build time. After changing `.env`, restart `npm run dev` or rebuild before deploying.

## API the frontend calls

All requests send `Authorization: Bearer <Cognito ID token>`. Calls live in `src/api.js`.

| Method | Path | Body | Returns |
|---|---|---|---|
| `POST` | `/uploads` | `{ fileName, contentType }` | `{ uploadUrl, analysisId }` |
| `PUT` | `uploadUrl` (pre-signed S3) | the CSV file | — |
| `GET` | `/analyses/{analysisId}` | — | Analysis JSON (`data/analysis_response.json`) |
| `POST` | `/chat` | `{ analysisId, question, conversationId, includeHistory }` | Chat JSON (`data/chat_response.json`) |

After uploading, the app polls `GET /analyses/{id}` every 3 seconds until `status` is `COMPLETED` or `FAILED` (gives up after ~3 minutes). On `FAILED`, `errorMessage` is shown to the user.

## Project structure

```
frontend/
├── src/
│   ├── main.jsx            # Amplify/Cognito setup
│   ├── App.jsx             # Sign-in wrapper, page layout
│   ├── Upload.jsx          # File picker, upload, processing status
│   ├── Dashboard.jsx       # Summary cards, charts, anomalies, recommendations
│   ├── Chat.jsx            # Slide-in AI assistant panel
│   ├── api.js              # Backend calls + demo-mode mocks
│   ├── styles.css
│   └── sampleAnalysis.json # Demo data
├── infrastructure/
│   └── frontend-template.yaml  # S3 bucket + CloudFront (CloudFormation)
└── deploy.sh               # One-command deploy
```

## Deploy

Requires the AWS CLI (`brew install awscli`, then `aws configure`) and a filled-in `.env`.

```bash
cd frontend
./deploy.sh
```

This creates or updates the `cloud-cost-optimizer-frontend` stack (private S3 bucket + CloudFront), builds the app, uploads it, clears the CloudFront cache, and prints the site URL. The first deploy takes ~5–10 minutes for CloudFront to come online. Override defaults with `STACK_NAME=... AWS_REGION=... ./deploy.sh`.

## Connecting to the backend checklist

After the first deploy, the printed CloudFront URL must be allowed by the backend:

- [ ] **API Gateway CORS** allows the CloudFront URL and `http://localhost:5174`, with the `Authorization` and `Content-Type` headers.
- [ ] **S3 upload bucket CORS** allows `PUT` from the same origins, with the `Content-Type` header.
- [ ] **Cognito app client** has no client secret.
- [ ] **Cognito token** includes the organization identity the backend expects (the chatbot returns 401 without it).
- [ ] `POST /uploads` and `GET /analyses/{id}` exist and match the table above.

Then verify in a browser using the deployed URL: sign in → upload `data/sample_billing.csv` → dashboard appears → ask the assistant a question. Upload `data/sample_billing_invalid.csv` to check the error message.
