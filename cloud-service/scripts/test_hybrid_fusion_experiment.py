"""Small fixed-candidate checks for the search-only fusion experiment."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hybrid_fusion_experiment import _load_job, fuse, ndcg_at_10, run


class FusionTest(unittest.TestCase):
    def test_rrf_weights_and_rank_constant_change_ranking(self):
        vector = [{"pid": "v", "score": 10}, {"pid": "both", "score": 5}]
        lexical = [{"pid": "l", "score": 10}, {"pid": "both", "score": 5}]
        equal = fuse(vector, lexical, "rrf", lexical_weight=0.5, rank_constant=60)
        vector_heavy = fuse(vector, lexical, "rrf", lexical_weight=0.3, rank_constant=60)
        self.assertEqual(equal[0], "both")
        self.assertEqual(vector_heavy[0], "both")
        self.assertEqual(
            fuse(vector, lexical, "rrf", lexical_weight=0.3, rank_constant=1)[0],
            "v",
        )

    def test_means_and_document_level_ndcg(self):
        vector = [{"pid": "a#0", "score": 10}, {"pid": "b#0", "score": 5}]
        lexical = [{"pid": "b#0", "score": 10}, {"pid": "c#0", "score": 5}]
        arithmetic = fuse(vector, lexical, "arithmetic_mean", depth=2)
        geometric = fuse(vector, lexical, "geometric_mean", depth=2)
        self.assertEqual(arithmetic[0], "a#0")
        self.assertEqual(geometric[0], "b#0")
        self.assertEqual(ndcg_at_10(["b#0", "b#1"], {"b": 2}), 1.0)

    def test_collection_uses_same_two_legs_for_every_variant(self):
        class FakeClient:
            def __init__(self):
                self.bodies = []

            def search(self, index, body):
                self.bodies.append(body)
                if "knn" in body["query"]:
                    return [{"pid": "a#0", "score": 2.0},
                            {"pid": "b#0", "score": 1.0}]
                return [{"pid": "b#0", "score": 3.0},
                        {"pid": "a#0", "score": 1.0}]

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            query_file = base / "gt.json"
            query_file.write_text(json.dumps({"queries": [{
                "query_id": "q1", "text": "bone mass", "vector": [0.1, 0.2],
                "qrels": {"a": 2},
            }, {"query_id": "q2", "text": "unjudged", "vector": [0.2, 0.3]}]}))
            scenario = base / "example-openrag-hybrid-search"
            scenario.mkdir()
            (scenario / "workload-params.json").write_text(json.dumps({
                "queries_file": str(query_file), "target_index_name": "test-index",
                "ef_search": 128, "qrel_id_separator": "#",
            }))
            client = FakeClient()
            result = run(base, client, base / "fusion-experiment")
            self.assertEqual(result["queries_scored"], 1)
            self.assertEqual(len(client.bodies), 2)
            self.assertEqual(client.bodies[0]["query"]["knn"]["emb"]["k"], 100)
            self.assertIn("multi_match", client.bodies[1]["query"]["bool"]["should"][0])
            self.assertEqual(len(result["variants"]), 10)
            self.assertEqual(len((base / "fusion-experiment/candidates.jsonl").read_text().splitlines()), 1)

    def test_relative_ground_truth_path_uses_datasets_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "gt").mkdir()
            (root / "gt/queries.json").write_text(json.dumps({"queries": [{
                "query_id": "q1", "text": "bone mass", "vector": [0.1],
                "qrels": {"doc": 1},
            }]}))
            scenario = root / "results/example-openrag-hybrid-search"
            scenario.mkdir(parents=True)
            (scenario / "workload-params.json").write_text(json.dumps({
                "queries_file": "gt/queries.json", "target_index_name": "test-index",
            }))
            with patch.dict(os.environ, {"DATASETS_ROOT": str(root)}):
                _, judged = _load_job(root / "results")
            self.assertEqual(judged[0]["query_id"], "q1")


if __name__ == "__main__":
    unittest.main()
