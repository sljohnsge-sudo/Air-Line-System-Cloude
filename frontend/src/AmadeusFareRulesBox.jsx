// Fare rules for an Amadeus fare (14/11/2026): the change / cancellation
// conditions Amadeus returns from the airline's filed rules (Fare_CheckRules,
// via /api/amadeus-fare-rules). Shown in the fare pop-up and on Review & Pay,
// in place of the Travelport fare-rules panel. Nothing is shown that Amadeus
// didn't return: without rules it says so.
export default function AmadeusFareRulesBox({ flight, loading = false, compact = false }) {
  const change = flight?.change_policy;
  const cancel = flight?.cancel_policy;
  const has = !!(change || cancel);

  if (compact) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
        {has ? (
          <>
            {change && (
              <div className="fc-line fc-wrap" title={`Changes: ${change}`}>
                <span>🔄</span><span><strong>Changes:</strong> {change}</span>
              </div>
            )}
            {cancel && (
              <div className="fc-line fc-wrap" title={`Cancellations: ${cancel}`}>
                <span>❌</span><span><strong>Cancel:</strong> {cancel}</span>
              </div>
            )}
            <div style={{ fontSize: '0.55rem', color: '#64748b' }}>From the airline's filed fare rules.</div>
          </>
        ) : (
          <div className="fc-line" style={{ color: '#64748b' }}>
            <span>📄</span><span>{loading ? 'Loading fare rules…' : 'Fare rules are not available for this fare right now.'}</span>
          </div>
        )}
      </div>
    );
  }

  return (
    <div style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '0.5rem', padding: '0.9rem 1.1rem' }}>
      <div style={{ fontWeight: 700, color: 'var(--gs-dark)', fontSize: '0.9rem', marginBottom: '0.5rem' }}>Fare rules — changes &amp; cancellations</div>
      {has ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.45rem', fontSize: '0.82rem', color: '#334155' }}>
          {change && <div><strong>Changes:</strong> {change}</div>}
          {cancel && <div><strong>Cancellations / refunds:</strong> {cancel}</div>}
          <div style={{ fontSize: '0.72rem', color: '#64748b' }}>From the airline's filed fare rules for this fare. Amounts are in the currency the airline files them in.</div>
        </div>
      ) : (
        <div style={{ fontSize: '0.82rem', color: '#64748b' }}>
          {loading ? 'Loading the fare rules from the airline…' : 'Fare rules are not available for this fare right now.'}
        </div>
      )}
    </div>
  );
}
