import csv
import hashlib
from pathlib import Path
import random
import tempfile
import unittest

from multigpu.artifacts import canonical_json
from multigpu.datasets import fingerprint_for_spec, inspect_dataset, load_tabular, stratified_split


class DatasetTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".test-runs"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def csv(self, rows):
        path = self.root / "data.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)
        return path

    def test_selected_label_and_train_only_standardization(self):
        labels = [0] * 10 + [1] * 10
        train, validation = stratified_split(labels, 42)
        rows = [["target", "amount", "constant"]]
        rows += [[label, 10000 if i in validation else i, 7] for i, label in enumerate(labels)]
        path = self.csv(rows)
        spec = {"recipe_version": 2, "dataset_path": str(path), "label_column": "target"}
        report = inspect_dataset(spec)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["dataset"]["input_schema"]["features"], ["amount", "constant"])
        data = load_tabular(spec)
        normalization = data["metadata"]["input_schema"]["standardization"]
        self.assertAlmostEqual(normalization["mean"][0], sum(train) / len(train))
        self.assertEqual(normalization["std"][1], 1)
        self.assertTrue(set(train).isdisjoint(validation))
        self.assertAlmostEqual(sum(data["features"][i][0] for i in train), 0, places=10)
        self.assertEqual(data["train_indices"], train)
        self.assertEqual(data["validation_indices"], validation)

    def test_missing_nonfinite_duplicate_headers_and_label_counts(self):
        for rows, fragment in [([["x", "label"], [1, ""]], "missing"),
                               ([["x", "label"], ["nan", 0]], "finite"),
                               ([["x", "x"], [1, 0]], "unique"),
                               ([["x", "label"], *[[1e308 if i % 2 else -1e308, i % 2] for i in range(20)]], "too large"),
                               ([["x", "label"], *[[i, i % 2] for i in range(8)]], "at least 5")]:
            with self.subTest(fragment=fragment):
                report = inspect_dataset({"recipe_version": 2, "dataset_path": str(self.csv(rows))})
                self.assertTrue(report["errors"])
                self.assertIn(fragment, report["errors"][0])

    def test_fingerprint_survives_path_move_and_rejects_changed_settings(self):
        path = self.csv([["x", "label"], *[[i, i % 2] for i in range(20)]])
        copy = self.root / "copied.csv"
        copy.write_bytes(path.read_bytes())
        spec = {"recipe_version": 2, "dataset_path": str(path)}
        self.assertEqual(fingerprint_for_spec(spec), fingerprint_for_spec({**spec, "dataset_path": str(copy)}))
        self.assertNotEqual(fingerprint_for_spec(spec), fingerprint_for_spec({**spec, "seed": 43}))
        copy.write_text(copy.read_text().replace("0,0", "200,0", 1))
        self.assertNotEqual(fingerprint_for_spec(spec), fingerprint_for_spec({**spec, "dataset_path": str(copy)}))

    def test_legacy_fingerprint_algorithm_is_unchanged(self):
        spec = {"recipe": "starter", "steps": 100, "learning_rate": .1, "batch_size": 32, "seed": 42}
        rng = random.Random(43)
        x = [[rng.uniform(-1, 1) for _ in range(4)] for _ in range(512)]
        y = [float(2 * row[0] - row[1] + .5 * row[2] - .2 * row[3] > 0) for row in x]
        data_hash = hashlib.sha256(canonical_json([x, y])).hexdigest()
        expected = hashlib.sha256(canonical_json({"config": spec, "data": data_hash})).hexdigest()
        self.assertEqual(fingerprint_for_spec(spec), expected)
        self.assertEqual(load_tabular(spec)["validation_indices"], [])

    def test_legacy_custom_fingerprint_matches_prechange_worker(self):
        script = self.root / "train.py"
        script.write_text("def train(ctx):\n    pass\n", encoding="utf-8")
        data = self.root / "input.dat"
        data.write_bytes(b"custom training input\n")
        spec = {"recipe": "custom", "steps": 30, "learning_rate": .02, "batch_size": 16, "seed": 7,
                "script_path": str(script), "dataset_path": str(data)}
        hashes = {"script": hashlib.sha256(script.read_bytes()).hexdigest(), "dataset": hashlib.sha256(data.read_bytes()).hexdigest()}
        data_hash = hashlib.sha256(canonical_json(hashes)).hexdigest()
        config = {key: spec[key] for key in ("recipe", "steps", "learning_rate", "batch_size", "seed")}
        expected = hashlib.sha256(canonical_json({"config": config, "data": data_hash})).hexdigest()
        self.assertEqual(fingerprint_for_spec(spec), expected)
        without_data = {key: value for key, value in spec.items() if key != "dataset_path"}
        hashes.pop("dataset")
        expected = hashlib.sha256(canonical_json({"config": config, "data": hashlib.sha256(canonical_json(hashes)).hexdigest()})).hexdigest()
        self.assertEqual(fingerprint_for_spec(without_data), expected)

    def test_image_class_counts_and_corrupt_images(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is optional")
        folder = self.root / "images"
        for label, name in enumerate(("cats", "dogs")):
            (folder / name).mkdir(parents=True)
            for i in range(5):
                Image.new("RGB", (16, 16), (label * 100, i * 20, 20)).save(folder / name / f"{i}.png")
        spec = {"recipe": "image", "recipe_version": 2, "dataset_path": str(folder), "epochs": 5, "batch_size": 16}
        report = inspect_dataset(spec)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["dataset"]["classes"], ["cats", "dogs"])
        self.assertEqual(report["dataset"]["train_rows"], 8)
        self.assertEqual(report["dataset"]["validation_rows"], 2)
        self.assertEqual(report["dataset"]["total_steps"], 5)
        (folder / "cats" / "0.png").unlink()
        self.assertIn("at least 5", inspect_dataset(spec)["errors"][0])
        (folder / "cats" / "0.png").write_bytes(b"not an image")
        self.assertIn("Unreadable image", inspect_dataset(spec)["errors"][0])


if __name__ == "__main__":
    unittest.main()
