import unittest
from model_benchmark import edit_distance, normalize

class MetricsTest(unittest.TestCase):
    def test_normalization_preserves_word_differences(self):
        self.assertEqual(normalize('ＡＢＣ　１２３。'), 'abc123')
        self.assertNotEqual(normalize('か'), normalize('が'))

    def test_edit_distance_operations(self):
        self.assertEqual(edit_distance('日本語', '日本ご'), 1)
        self.assertEqual(edit_distance('', '日本'), 2)
        self.assertEqual(edit_distance('日本', ''), 2)
        self.assertEqual(edit_distance('日本', '日本'), 0)

    def test_writing_variants_are_not_silently_treated_as_equal(self):
        self.assertNotEqual(normalize('私'), normalize('わたし'))
        self.assertGreater(edit_distance(normalize('八時間'), normalize('8時間')), 0)

if __name__ == '__main__':
    unittest.main()
