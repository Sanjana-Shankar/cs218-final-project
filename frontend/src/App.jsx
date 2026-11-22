import { Authenticator } from "@aws-amplify/ui-react";
import Dashboard from "./Dashboard.jsx";
import sampleAnalysis from "./sampleAnalysis.json";

// Skip sign-in locally until the Cognito values are in .env
const authConfigured = Boolean(import.meta.env.VITE_COGNITO_USER_POOL_ID);

function Shell({ user, signOut }) {
  return (
    <main>
      <header>
        <h1>Cloud Cost Optimizer</h1>
        {signOut && (
          <div>
            <span className="muted" style={{ marginRight: 12 }}>{user?.signInDetails?.loginId}</span>
            <button onClick={signOut}>Sign out</button>
          </div>
        )}
      </header>
      <Dashboard analysis={sampleAnalysis} />
    </main>
  );
}

export default function App() {
  if (!authConfigured) return <Shell />;
  return <Authenticator>{(props) => <Shell {...props} />}</Authenticator>;
}
