import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer,
} from "recharts";

const money = (n, currency = "USD") =>
  new Intl.NumberFormat("en-US", { style: "currency", currency }).format(n);

const shortMoney = (n) =>
  "$" + new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);

const tooltipFormatter = (v) => [money(v), "Cost"];

function Card({ label, value, note }) {
  return (
    <div className="card">
      <div className="card-label">{label}</div>
      <div className="card-value">{value}</div>
      {note && <div className="muted">{note}</div>}
    </div>
  );
}

function CostBarChart({ title, data }) {
  return (
    <section className="panel">
      <h3>{title}</h3>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={data} layout="vertical" margin={{ left: 20, right: 28 }}>
          <CartesianGrid strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" tickFormatter={shortMoney} interval={0} tickCount={4} tick={{ fontSize: 12 }} />
          <YAxis type="category" dataKey="name" width={100} />
          <Tooltip formatter={tooltipFormatter} cursor={{ fill: "#f0f3f8" }} />
          <Bar dataKey="cost" fill="#2a6fdb" barSize={22} radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </section>
  );
}

function SeverityTag({ severity }) {
  return <span className={`tag tag-${severity.toLowerCase()}`}>{severity}</span>;
}

export default function Dashboard({ analysis }) {
  const { summary, currency } = analysis;
  const change = summary.percentChangeFromPreviousPeriod;
  const recommendations = [...analysis.recommendations].sort(
    (a, b) => b.estimatedMonthlySavings - a.estimatedMonthlySavings
  );

  return (
    <div>
      <p className="muted">
        Billing period {analysis.period.startDate} to {analysis.period.endDate}
      </p>

      <div className="cards">
        <Card label="Total cost" value={money(summary.totalCost, currency)} />
        <Card
          label="Potential savings"
          value={money(summary.estimatedSavings, currency)}
          note="per month"
        />
        <Card
          label="Change vs previous period"
          value={change == null ? "—" : `${change > 0 ? "+" : ""}${change}%`}
          note={summary.previousPeriodCost != null && `Previous: ${money(summary.previousPeriodCost, currency)}`}
        />
        <Card label="Recommendations" value={summary.recommendationCount} />
        <Card label="Anomalies" value={summary.anomalyCount} />
      </div>

      <div className="grid">
        <CostBarChart title="Cost by service" data={analysis.serviceCosts} />
        <CostBarChart title="Cost by project" data={analysis.projectCosts} />
        <CostBarChart title="Cost by team" data={analysis.teamCosts} />
        <section className="panel wide">
          <h3>Cost trend</h3>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={analysis.dailyCosts} margin={{ left: 10, right: 20 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" tickMargin={8} />
              <YAxis tickFormatter={shortMoney} />
              <Tooltip formatter={tooltipFormatter} />
              <Line type="monotone" dataKey="cost" stroke="#2a6fdb" strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </section>
      </div>

      <h2>Anomalies</h2>
      <section className="panel">
        {analysis.anomalies.length === 0 ? (
          <p className="muted" style={{ margin: 0 }}>No unusual spending detected in this period.</p>
        ) : (
          <table>
            <thead>
              <tr><th>Severity</th><th>What happened</th><th>Resource</th><th>Observed</th><th>Expected</th></tr>
            </thead>
            <tbody>
              {analysis.anomalies.map((a) => (
                <tr key={a.anomalyId}>
                  <td><SeverityTag severity={a.severity} /></td>
                  <td>{a.message}</td>
                  <td>{a.resourceId ?? "—"}</td>
                  <td>{a.observedValue}</td>
                  <td>{a.expectedValue}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2>Recommendations</h2>
        {recommendations.length === 0 && (
          <p className="muted">No savings opportunities found. Nice work!</p>
        )}
        {recommendations.map((r) => (
          <article key={r.recommendationId} className="panel rec">
            <div className="rec-head">
              <div>
                <SeverityTag severity={r.severity} />{" "}
                <strong>{r.resourceId}</strong>{" "}
                <span className="muted">({r.service} · {r.project} · {r.team})</span>
              </div>
              <div className="savings">
                Save {money(r.estimatedMonthlySavings, r.savingsCurrency)}/mo
              </div>
            </div>
            <p><strong>Problem:</strong> {r.issue}</p>
            <p><strong>What to do:</strong> {r.action}</p>
            {r.aiExplanation && <p><strong>In plain terms:</strong> {r.aiExplanation}</p>}
            <details>
              <summary>Trade-offs and how to check first</summary>
              <p><strong>Performance risk:</strong> {r.performanceRisk}</p>
              <p><strong>Reliability risk:</strong> {r.reliabilityRisk}</p>
              <p><strong>Before you change it:</strong> {r.verificationStep}</p>
              <p className="muted">Confidence: {Math.round(r.confidence * 100)}%</p>
            </details>
          </article>
        ))}
      </section>
    </div>
  );
}
