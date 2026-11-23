import { useState } from "react";
import { Authenticator } from "@aws-amplify/ui-react";
import Dashboard from "./Dashboard.jsx";
import Upload from "./Upload.jsx";
import { usingMocks } from "./api.js";

// Skip sign-in locally until the Cognito values are in .env
const authConfigured = Boolean(import.meta.env.VITE_COGNITO_USER_POOL_ID);

function Shell({ user, signOut }) {
  const [analysis, setAnalysis] = useState(null);
  const [fileName, setFileName] = useState(null);

  function showResult(result, name) {
    setAnalysis(result);
    setFileName(name);
  }

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
      {usingMocks && <p className="banner">Demo mode: no API configured, showing sample data.</p>}
      <Upload onComplete={showResult} />
      {analysis ? (
        <Dashboard analysis={analysis} fileName={fileName} />
      ) : (
        <p className="muted empty">Upload a billing CSV to see your cost breakdown and savings.</p>
      )}
    </main>
  );
}

export default function App() {
  if (!authConfigured) return <Shell />;
  return <Authenticator>{(props) => <Shell {...props} />}</Authenticator>;
}
