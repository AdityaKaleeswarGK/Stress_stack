import unittest
from orchard.service import build, describe, nested, shadow


class ExampleTests(unittest.TestCase):
    def test_imports_really_work(self):
        self.assertEqual(build(4).count, 8)
        self.assertEqual(describe(3), '{"count": 6}')
        self.assertEqual(nested(), 8)
        self.assertEqual(shadow(lambda n: n + 1), 4)
