// Derived edges: the aggregate layer.
//
// These are recomputed on a timer by graph/derive.py, never written by the
// loader. The raw action edges underneath are what make their weights true
// and what an Insight cites; these are what the UI draws and what the
// significance gate reads.
//
// Scope differs by edge, and the difference is deliberate:
//   LINKS_WITH, CIRCULATES_THROUGH, PRESSES_IN  - per match
//   NEMESIS_OF                                  - across every match loaded
// A passing network is a fact about one game. A matchup only becomes
// interesting once it has recurred across fixtures.
//
// Each statement is separated by the marker below so derive.py can split
// the file without a Cypher parser.

// --name: LINKS_WITH
// Who is combining with whom. The passing network, as weighted edges.
MATCH (a:Player)-[r:PASSED_TO]->(b:Player)
WHERE r.match_id = $matchId
WITH a, b,
     count(r)                                                   AS passes,
     sum(CASE WHEN r.pass_progressive THEN 1 ELSE 0 END)        AS progressive,
     sum(CASE WHEN r.outcome = 'SUCCESS' THEN 1 ELSE 0 END)     AS completed,
     avg(r.pass_difficulty)                                     AS difficulty,
     avg(r.hold_time_s)                                         AS hold
WHERE passes >= $minPasses
MERGE (a)-[l:LINKS_WITH {match_id: $matchId}]->(b)
SET l.passes          = passes,
    l.progressive     = progressive,
    l.completion      = round(1000.0 * completed / passes) / 1000.0,
    l.mean_difficulty = round(1000.0 * difficulty) / 1000.0,
    l.mean_hold_s     = round(100.0 * hold) / 100.0,
    l.updated_s       = $now;

// --name: CIRCULATES_THROUGH
// Who the side routes play through. Share of the team's received passes,
// which is a cheap proxy for centrality and needs no graph algorithms
// library - the free tier does not have one.
//
// Note the UNWIND ... WITH pair below. Cypher will not accept a map field
// such as row.player as a node in a pattern; the endpoint has to be a bound
// variable, so the map is unpacked before the MERGE.
MATCH (a:Player)-[r:PASSED_TO]->(b:Player)
WHERE r.match_id = $matchId
MATCH (b)-[:PLAYS_FOR]->(t:Team)
WITH t, b, count(r) AS received
WITH t, collect({player: b, received: received}) AS rows, sum(received) AS total
WHERE total > 0
UNWIND rows AS row
WITH t, total, row.player AS p, row.received AS received
MERGE (t)-[c:CIRCULATES_THROUGH {match_id: $matchId}]->(p)
SET c.received  = received,
    c.share     = round(1000.0 * received / total) / 1000.0,
    c.updated_s = $now;

// --name: PRESSES_IN
// Where a side wins the ball back, and how hard it squeezed to do it.
// This is the edge that lets the Explainer say a press has found a seam.
MATCH (winner:Player)-[d:DISPOSSESSED]->(:Player)
WHERE d.match_id = $matchId AND d.start_zone IS NOT NULL
MATCH (winner)-[:PLAYS_FOR]->(t:Team)
MATCH (z:Zone {zone_id: d.start_zone})
WITH t, z, count(d) AS turnovers, avg(d.pressure) AS intensity
MERGE (t)-[p:PRESSES_IN {match_id: $matchId}]->(z)
SET p.turnovers      = turnovers,
    p.mean_pressure  = round(1000.0 * intensity) / 1000.0,
    p.updated_s      = $now;

// --name: NEMESIS_OF
// Individual matchups, across every match in the graph. Deliberately not
// scoped to one fixture: a duel record is only worth remarking on once it
// has recurred.
MATCH (winner:Player)-[d:DISPOSSESSED]->(loser:Player)
WITH winner, loser, count(d) AS wins
WHERE wins >= $minDuels
OPTIONAL MATCH (loser)-[back:DISPOSSESSED]->(winner)
WITH winner, loser, wins, count(back) AS losses
MERGE (winner)-[n:NEMESIS_OF]->(loser)
SET n.wins      = wins,
    n.losses    = losses,
    n.duels     = wins + losses,
    n.win_rate  = round(1000.0 * wins / (wins + losses)) / 1000.0,
    n.updated_s = $now;
