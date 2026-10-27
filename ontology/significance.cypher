// The significance gate.
//
// Runs every few seconds and scores the recent past. Anything above
// threshold is handed to the agents together with the evidence behind it;
// everything else is never spoken about. Roughly 1,300 actions arrive in a
// match and perhaps thirty deserve comment, so this query is what keeps the
// system from either chattering or picking arbitrarily.
//
// It is a query and not an agent on purpose. The same window always scores
// the same, it costs nothing to run 1,800 times a match, it returns in
// milliseconds, and you can disagree with a weight and change it. A model
// asked "was that important?" offers none of those.
//
// The shape is the price of the edge-centric model. Actions are
// relationships of four different types and shots are nodes, so the window
// is assembled in two parts and unioned, where an event-node model would
// have been a single MATCH. That is a real cost, paid once here, in exchange
// for a graph a person can read.

// --name: ESTIMATE_THRESHOLD
// Extracts global statistical event rates across all loaded matches in the
// graph to derive a deterministic significance threshold.
//
// Each scored event contributes the exact same weight used in SCORE_WINDOW:
//   - forced turnover (DISPOSSESSED with pressure > 0.6):          0.8
//   - line-breaking pass (progressive PASSED_TO, difficulty > 0.6): 0.5
//   - shot chance (Shot.shot_xg):                                  3.0 * xG
//
// Scaling total weighted score mass by ($window / total_clock_s) yields the
// expected score of a random window, which is multiplied by $multiplier (or
// falls back to $defaultThreshold when the graph is empty).
MATCH (m:Match)
WITH count(m) AS matches,
     sum(coalesce(m.period_length_s, 2700.0)) AS total_clock_s
OPTIONAL MATCH (:Player)-[r]->()
WHERE type(r) IN ['PASSED_TO', 'CARRIED_INTO', 'DISPOSSESSED', 'PRESSED_IN']
WITH matches, total_clock_s,
     count(r) AS action_count,
     sum(CASE WHEN type(r) = 'DISPOSSESSED' AND coalesce(r.pressure, 0) > 0.6
              THEN 1 ELSE 0 END) AS forced_turnovers,
     sum(CASE WHEN type(r) = 'PASSED_TO' AND r.pass_progressive
                   AND coalesce(r.pass_difficulty, 0) > 0.6
                   AND r.outcome = 'SUCCESS'
              THEN 1 ELSE 0 END) AS breaking_passes
OPTIONAL MATCH (s:Shot)
WITH matches, total_clock_s, action_count, forced_turnovers, breaking_passes,
     count(s) AS shot_count,
     coalesce(sum(s.shot_xg), 0.0) AS total_xg
WITH matches, total_clock_s, action_count, forced_turnovers, breaking_passes,
     shot_count, total_xg,
     (forced_turnovers * 0.8 + breaking_passes * 0.5 + total_xg * 3.0) AS total_score_mass
WITH matches, action_count, forced_turnovers, breaking_passes, shot_count,
     round(1000.0 * total_xg) / 1000.0 AS total_xg,
     CASE WHEN total_clock_s > 0
          THEN total_score_mass * ($window / total_clock_s)
          ELSE 0.0 END AS expected_window_score
RETURN CASE WHEN expected_window_score > 0
            THEN round(1000.0 * expected_window_score * $multiplier) / 1000.0
            ELSE $defaultThreshold END AS threshold,
       round(1000.0 * expected_window_score) / 1000.0 AS expected_window_score,
       matches,
       action_count,
       forced_turnovers,
       breaking_passes,
       shot_count,
       total_xg;

// --name: SCORE_WINDOW
// Actions in the window: passes, carries, turnovers, presses.
MATCH (a:Player)-[r]->(b)
WHERE r.match_id = $matchId
  AND type(r) IN ['PASSED_TO', 'CARRIED_INTO', 'DISPOSSESSED', 'PRESSED_IN']
  AND r.clock_s > $now - $window AND r.clock_s <= $now
WITH collect({
  event_id: r.event_id,
  kind:     type(r),
  clock_s:  r.clock_s,
  actor:    a.name,
  target:   CASE WHEN b:Player THEN b.name ELSE b.name END,
  zone:     r.start_zone,
  pressure: coalesce(r.pressure, 0.0),
  outcome:  r.outcome,
  // A turnover won under heavy pressure is the press working, not a stray
  // pass. That distinction is the difference between control and chaos.
  forced:   CASE WHEN type(r) = 'DISPOSSESSED' AND coalesce(r.pressure, 0) > 0.6
                 THEN 0.8 ELSE 0.0 END,
  // A hard progressive pass completed is someone breaking a line.
  breaking: CASE WHEN type(r) = 'PASSED_TO' AND r.pass_progressive
                      AND coalesce(r.pass_difficulty, 0) > 0.6
                      AND r.outcome = 'SUCCESS'
                 THEN 0.5 ELSE 0.0 END
}) AS actions

// Shots in the window. Nodes, not edges, because a shot has too many
// participants to be a relationship.
OPTIONAL MATCH (p:Player)-[:TOOK]->(s:Shot)
WHERE s.match_id = $matchId
  AND s.clock_s > $now - $window AND s.clock_s <= $now
OPTIONAL MATCH (s)-[:FROM_ZONE]->(sz:Zone)
WITH actions, collect({
  event_id: s.event_id,
  kind:     'SHOT',
  clock_s:  s.clock_s,
  actor:    p.name,
  target:   null,
  zone:     sz.zone_id,
  pressure: coalesce(s.pressure, 0.0),
  outcome:  s.outcome,
  forced:   0.0,
  breaking: 0.0,
  // Weighted by how good the chance actually was, so a tap-in scores far
  // above a hopeful effort from distance.
  chance:   coalesce(s.shot_xg, 0.0) * 3.0
}) AS shots

WITH actions + [x IN shots WHERE x.event_id IS NOT NULL] AS window
WITH window,
     reduce(total = 0.0, x IN window |
            total + x.forced + x.breaking + coalesce(x.chance, 0.0)) AS score
WHERE score > $threshold
RETURN score,
       // The highest-contributing actions first: these are what the
       // Explainer is handed and what an Insight will cite.
       [x IN window WHERE x.forced + x.breaking + coalesce(x.chance, 0.0) > 0][..$maxEvidence]
         AS evidence,
       size(window) AS window_size;
