WITH stats AS MATERIALIZED (
  SELECT w.wordid AS word_id, w.word,
    COUNT(DISTINCT s.synsetid) AS total_synsets,
    COUNT(*) AS total_senses,
    COUNT(DISTINCT syn.domainid) AS distinct_domains,
    COUNT(DISTINCT syn.posid) AS distinct_posids
  FROM words w
  JOIN senses s ON w.wordid = s.wordid
  JOIN synsets syn ON s.synsetid = syn.synsetid
  GROUP BY w.wordid, w.word
  HAVING total_synsets >= 2 AND distinct_domains >= 2 AND total_senses >= 2
  ORDER BY total_senses DESC, total_synsets DESC, distinct_domains DESC, word_id ASC
  LIMIT 500
), ranked AS (
  SELECT s.wordid, s.synsetid, COUNT(*) AS n,
    ROW_NUMBER() OVER (PARTITION BY s.wordid ORDER BY COUNT(*) DESC, s.synsetid ASC) AS rn
  FROM senses s
  JOIN (SELECT DISTINCT word_id FROM stats) chosen ON chosen.word_id = s.wordid
  GROUP BY s.wordid, s.synsetid
)
SELECT stats.word_id, stats.word, stats.total_synsets, stats.total_senses,
  stats.distinct_domains, stats.distinct_posids,
  ranked.synsetid AS top_synsetid, ranked.n AS top_synset_sense_count
FROM stats JOIN ranked ON ranked.wordid = stats.word_id AND ranked.rn = 1
ORDER BY total_senses DESC, total_synsets DESC, distinct_domains DESC, word_id ASC;
