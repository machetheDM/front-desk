"""The owner analyses run end to end on demo data, and their headline claims hold."""

from __future__ import annotations

from collections import Counter

from frontdesk_ml import analyses, clustering, dataset, forecast


def test_the_forecast_beats_last_week_and_its_intervals_are_not_too_narrow():
    bt = forecast.backtest(forecast.load_weeks())
    assert bt["level x profile"]["mase"] < 1.0
    assert bt["level x profile"]["coverage_80"] >= 0.80


def test_umap_then_hdbscan_recovers_intents_better_than_kmeans_told_the_answer():
    items = dataset.load() + dataset.load(dataset.HOLDOUT)
    q = clustering.evaluate([i.text for i in items], [i.intent for i in items])
    assert q["umap_hdbscan_ari"] > q["kmeans_ari"] and q["umap_hdbscan_nmi"] > q["kmeans_nmi"]
    assert q["noise_share"] < 0.2 < q["hdbscan_without_umap_noise_share"]


def test_topics_are_named_and_every_question_is_either_in_a_topic_or_listed_as_noise():
    texts = [i.text for i in dataset.load()][:60]
    topics, noise = clustering.topics(texts)
    assert topics and all(t.keywords and t.example in t.members for t in topics)
    assert sum(t.size for t in topics) + len(noise) == len(texts)


def test_the_drift_rule_rarely_cries_wolf_where_the_first_version_always_did():
    mix = Counter(installation=46, pricing=43, warranty=26, general=19, hours=15, service_area=15, payment=13,
                  discount=10, booking=7, human=7, order_status=4, out_of_scope=3, location=2, complaint=1, fault=1)
    (r,) = analyses.alarm_rates(mix, sizes=(50,), sims=300)
    assert r["rule_false_alarms"] < 0.05 < 0.5 < r["naive_false_alarms"]
    assert r["rule_detects_faults_at_15_percent"] > 0.7


def test_the_full_analysis_runs_and_writes_every_section(tmp_path):
    out = analyses.run(tmp_path / "reports", evidence=tmp_path / "insights.md")
    text = (tmp_path / "insights.md").read_text()
    for heading in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5."):
        assert heading in text
    assert out["topics"] and out["ab"]["n_per_arm"] == 906
