import unittest


class MainProtectionTest(unittest.TestCase):
    def test_temporary_failure_blocks_merge(self):
        self.assertTrue(False, "intentional failure for main branch protection test")


if __name__ == "__main__":
    unittest.main()
