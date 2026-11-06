import unittest
from shortlisting import meets_threshold
from screening import WEIGHTS, weighted_score

class TotalScoreTests(unittest.TestCase):
    def assessment(self,scores):
        return {'components':{name:{'score':score,'applicable':True} for name,score in zip(WEIGHTS,scores)}}
    def test_total_passes_even_with_one_component_below_threshold(self):
        result=self.assessment([90,80,60,80])
        self.assertEqual(weighted_score(result),79)
        self.assertTrue(meets_threshold(result,70))
    def test_equal_score_fails_strict_threshold(self):
        result=self.assessment([70]*4)
        self.assertFalse(meets_threshold(result,70))
        self.assertTrue(meets_threshold(result,69.9))
    def test_missing_preferred_criteria_renormalizes(self):
        result=self.assessment([80,80,80,0])
        result['components']['preferred_qualifications']['applicable']=False
        self.assertEqual(weighted_score(result),80)
        self.assertTrue(meets_threshold(result,75))
