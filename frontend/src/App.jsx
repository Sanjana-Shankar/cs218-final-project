import { Authenticator } from "@aws-amplify/ui-react";

export default function App() {
  return (
    <Authenticator hideSignUp={false}>
      {({ signOut, user }) => (
        <main style={{ maxWidth: 1100, margin: "0 auto", padding: 16, fontFamily: "system-ui" }}>
          <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h1>Cloud Cost Optimizer</h1>
            <div>
              <span style={{ marginRight: 12 }}>{user?.signInDetails?.loginId}</span>
              <button onClick={signOut}>Sign out</button>
            </div>
          </header>
          <p>Upload, dashboard, and chat sections go here.</p>
        </main>
      )}
    </Authenticator>
  );
}
