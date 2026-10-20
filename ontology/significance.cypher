// The significance gate. Scores the last 90 seconds of match time and
// returns anything above $threshold together with its evidence.
//
// Each signal is independently defensible; the weights are the one place
// tuning happens. See docs/technical-design.md.

MATCH (m:Match {match_id: $matchId})
MATCH (e:Event)-[:PART_OF]->(m)
WHERE e.clock_s > $now - 90 AND e.clock_s <= $now
WITH e,
     CASE WHEN e.type = 'SHOT' THEN coalesce(e.shot_xg, 0) * 3.0 ELSE 0 END AS chance,
     CASE WHEN e.outcome = 'FAIL' AND e.pressure > 0.6 THEN 0.8 ELSE 0 END AS forced,
     CASE WHEN e.pass_progressive AND e.pass_difficulty > 0.6 THEN 0.5 ELSE 0 END AS breaking
WITH sum(chance + forced + breaking) AS score,
     collect(e)[..12] AS evidence
WHERE score > $threshold
RETURN score, evidence
