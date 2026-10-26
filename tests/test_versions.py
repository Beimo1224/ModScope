from __future__ import annotations

import unittest

from modscope.versions import (
    class_version_to_java,
    compare,
    parse_fabric_predicate,
    parse_maven_range,
    parse_quilt_spec,
    versions_equal,
)


class VersionComparisonTests(unittest.TestCase):
    def test_ordering(self):
        self.assertGreater(compare("1.20.1", "1.20"), 0)
        self.assertLess(compare("1.20.1-pre1", "1.20.1"), 0)
        self.assertEqual(compare("1.20.0", "1.20"), 0)
        self.assertEqual(compare("1.10", "1.9"), 1)
        self.assertGreater(compare("1.21.10", "1.21.9"), 0)
        self.assertTrue(versions_equal("1.0.0", "1.0.0+build.7"))

    def test_fabric_predicates(self):
        spec = parse_fabric_predicate(">=1.20 <1.21")
        self.assertIsNone(spec.parse_error)
        self.assertTrue(spec.matches("1.20.1"))
        self.assertTrue(spec.matches("1.20.6"))
        self.assertFalse(spec.matches("1.21"))

        wildcard = parse_fabric_predicate("1.20.x")
        self.assertTrue(wildcard.matches("1.20"))
        self.assertTrue(wildcard.matches("1.20.4"))
        self.assertFalse(wildcard.matches("1.21"))

        self.assertTrue(parse_fabric_predicate("~1.20.1").matches("1.20.8"))
        self.assertFalse(parse_fabric_predicate("~1.20.1").matches("1.21"))
        self.assertTrue(parse_fabric_predicate("^1.20.1").matches("1.21"))
        self.assertFalse(parse_fabric_predicate("^1.20.1").matches("2.0"))

        exact = parse_fabric_predicate("1.20.1")
        self.assertTrue(exact.matches("1.20.1"))
        self.assertFalse(exact.matches("1.20.2"))
        self.assertTrue(parse_fabric_predicate("*").matches("1.7.10"))
        self.assertIsNotNone(parse_fabric_predicate("not-a-version!!").parse_error)

    def test_maven_ranges(self):
        spec = parse_maven_range("[1.20.1,1.21)")
        self.assertTrue(spec.matches("1.20.1"))
        self.assertTrue(spec.matches("1.20.9"))
        self.assertFalse(spec.matches("1.21"))

        union = parse_maven_range("(,1.12],[1.14,)")
        self.assertTrue(union.matches("1.11.2"))
        self.assertTrue(union.matches("1.12"))
        self.assertFalse(union.matches("1.13"))
        self.assertTrue(union.matches("1.14"))
        self.assertTrue(union.matches("1.20.1"))

        exact = parse_maven_range("[1.20.1]")
        self.assertTrue(exact.matches("1.20.1"))
        self.assertFalse(exact.matches("1.20.2"))

        self.assertTrue(parse_maven_range("").matches("1.0"))
        self.assertTrue(parse_maven_range("[1.0,2.0)").matches("1.9"))
        self.assertFalse(parse_maven_range("[1.0,2.0)").matches("2.0"))

    def test_quilt_specs(self):
        bare = parse_quilt_spec("1.0.0")
        self.assertTrue(bare.matches("1.5.0"))
        self.assertFalse(bare.matches("2.0.0"))

        any_spec = parse_quilt_spec({"any": [">=1.20", "1.19.2"]})
        self.assertTrue(any_spec.matches("1.19.2"))
        self.assertTrue(any_spec.matches("1.20.1"))
        self.assertFalse(any_spec.matches("1.18.2"))

        all_spec = parse_quilt_spec({"all": [">=1.19", "<1.21"]})
        self.assertTrue(all_spec.matches("1.20.1"))
        self.assertFalse(all_spec.matches("1.21"))

        old_array = parse_quilt_spec([">=1.20", "<1.21"])
        # Quilt's deprecated array syntax is OR, not AND.
        self.assertTrue(old_array.matches("1.21"))
        self.assertTrue(old_array.matches("1.19"))

    def test_class_version_mapping(self):
        self.assertEqual(class_version_to_java(52), 8)
        self.assertEqual(class_version_to_java(61), 17)
        self.assertEqual(class_version_to_java(65), 21)


if __name__ == "__main__":
    unittest.main()
