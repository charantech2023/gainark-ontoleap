"""
Story-based ICP prompts: the model chooses words, never facts.

A fake model stands in for Gemini, so these test the checks around it - quotes must be
on the page, prompts may carry no name or number the story lacks, the vendor is never
named, and a prompt that pulls in the profile's competitors is kept apart. No network.
"""

import json
import unittest

import buyer_stories as bs

PAGE = (
    "Gentreo is a digital estate planning SaaS. For the first few years of our journey, "
    "Gentreo had been using Recurly for subscription management and payment processing. "
    "As the platform evolved we realized that B2B/B2B2C was the future for our business. "
    "Billing was slow and error-prone because so much of it was manual. "
    "“We needed one platform for consumer and business billing,” said Jane Doe, CFO."
)

PROFILE = {
    "brand_name": "Ordway",
    "known_competitors": ["Recurly", "Zuora"],
    "icp_evidence": {
        "known_replaces": {
            "Recurly for subscription management": {
                "source_url": "https://ordway.example/gentreo",
                "quote": "Gentreo had been using Recurly for subscription management and payment processing."},
        },
        "known_industries": {
            "Digital estate planning SaaS": {
                "source_url": "https://ordway.example/gentreo",
                "quote": "Gentreo is a digital estate planning SaaS."},
            "Healthcare platform": {
                "source_url": "https://ordway.example/paytient",
                "quote": "Paytient is a healthcare platform."},
        },
    },
}

STORY = {
    "voice": "customer",
    "parts": {
        "company": {"text": "digital estate planning SaaS",
                    "quote": "Gentreo is a digital estate planning SaaS."},
        "before": {"text": "Recurly for subscriptions and payments",
                   "quote": "Gentreo had been using Recurly for subscription management and payment processing."},
        "trigger": {"text": "moving into B2B and B2B2C",
                    "quote": "As the platform evolved we realized that B2B/B2B2C was the future for our business."},
        "pain": {"text": "manual, slow and error-prone billing",
                 "quote": "Billing was slow and error-prone because so much of it was manual."},
        "role": {"text": "CFO, Head of Billing",
                 "quote": "We needed one platform for consumer and business billing, said Jane Doe, CFO."},
        "need": {"text": "one platform for consumer and business billing",
                 "quote": "This sentence is not on the page at all, so the part must go."},
    },
}


def fake_model(story=STORY, prompts=()):
    """Answers the read step with `story` and the write step with `prompts`."""
    def call(prompt, system):
        return json.dumps(story if "extract facts" in system else list(prompts))
    return call


class GroupingTest(unittest.TestCase):

    def test_one_page_is_one_story(self):
        pages = bs.evidence_by_page(PROFILE)
        self.assertEqual(sorted(pages), ["https://ordway.example/gentreo", "https://ordway.example/paytient"])
        self.assertEqual({e["field"] for e in pages["https://ordway.example/gentreo"]},
                         {"known_replaces", "known_industries"})


class QuoteTest(unittest.TestCase):

    def test_a_quote_matches_across_punctuation_and_bad_decoding(self):
        self.assertTrue(bs.quote_on_page("platform for consumer and business billing,� said Jane", PAGE))

    def test_a_paraphrase_is_not_a_quote(self):
        self.assertFalse(bs.quote_on_page("Gentreo used Recurly for its subscriptions", PAGE))

    def test_a_fragment_is_too_short_to_count(self):
        self.assertFalse(bs.quote_on_page("using Recurly", PAGE))


class ReadStoryTest(unittest.TestCase):

    def test_parts_without_a_real_quote_are_dropped(self):
        story = bs.read_story("u", PAGE, [], "Ordway", call=fake_model())
        self.assertNotIn("need", story.parts)
        self.assertEqual([d["part"] for d in story.dropped], ["need"])
        self.assertEqual(story.voice, "customer")

    def test_one_role(self):
        story = bs.read_story("u", PAGE, [], "Ordway", call=fake_model())
        self.assertEqual(story.parts["role"].text, "CFO")

    def test_a_part_that_says_more_than_its_quote_is_dropped(self):
        extra = {"voice": "customer", "parts": {"need": {
            "text": "usage-based billing with metering",
            "quote": "Billing was slow and error-prone because so much of it was manual."}}}
        story = bs.read_story("u", PAGE, [], "Ordway", call=fake_model(story=extra))
        self.assertEqual(story.parts, {})
        self.assertEqual(story.dropped[0]["reason"], "text says more than its quote")

    def test_an_outcome_naming_the_vendor_is_not_a_need(self):
        page = PAGE + " The best part of Ordway is that billing is finally automatic for us."
        outcome = {"voice": "customer", "parts": {"need": {
            "text": "automatic billing",
            "quote": "The best part of Ordway is that billing is finally automatic for us."}}}
        story = bs.read_story("u", page, [], "Ordway", call=fake_model(story=outcome))
        self.assertEqual(story.parts, {})
        self.assertIn("outcome", story.dropped[0]["reason"])

    def test_support_uses_stems(self):
        self.assertEqual(bs.supported("prorations for upgrades",
                                      "could not perform proration on an upgrade"), 1.0)

    def test_unparseable_output_gives_an_empty_story(self):
        story = bs.read_story("u", PAGE, [], "Ordway", call=lambda p, s: "not json")
        self.assertEqual(story.parts, {})


class CheckTest(unittest.TestCase):

    def setUp(self):
        self.story = bs.read_story("u", PAGE, [], "Ordway", call=fake_model())

    def check(self, text, uses=("company", "before")):
        return bs.check_prompt(text, list(uses), self.story, "Ordway", ["Recurly", "Zuora"])

    def test_a_grounded_prompt_passes(self):
        self.assertEqual(self.check(
            "We're a digital estate planning SaaS on Recurly. I've outgrown it - what should we look at?"),
            (None, "story"))

    def test_naming_the_vendor_is_rejected(self):
        self.assertIn("names the vendor", self.check(
            "We're a digital estate planning SaaS on Recurly. Is Ordway a good fit for us?")[0])

    def test_an_invented_number_is_rejected(self):
        reason, _ = self.check("We're a digital estate planning SaaS with 40 staff on Recurly and need help.")
        self.assertIn("40", reason)

    def test_an_invented_name_is_rejected(self):
        reason, _ = self.check("We're a digital estate planning SaaS on Recurly and moving to NetSuite soon.")
        self.assertIn("NetSuite", reason)

    def test_a_profile_competitor_is_kept_apart(self):
        self.assertEqual(self.check(
            "We're a digital estate planning SaaS on Recurly. Should we look at Zuora instead?"),
            (None, "profile"))

    def test_a_capital_after_a_full_stop_is_not_a_name(self):
        self.assertEqual(self.check(
            "We run a digital estate planning SaaS on Recurly. Billing keeps breaking. Help?")[1], "story")


class WritePromptsTest(unittest.TestCase):

    def test_rejections_keep_their_reason(self):
        prompts = [
            {"stage": "switch", "text": "We're a digital estate planning SaaS on Recurly and moving "
                                        "into B2B. What should we switch to?", "uses": ["company", "before", "trigger"]},
            {"stage": "shortlist", "text": "Our 12 person digital estate planning SaaS needs billing software.",
             "uses": ["company"]},
            {"stage": "pitch", "text": "Tell me about billing for a digital estate planning SaaS.", "uses": ["company"]},
        ]
        story = bs.read_story("u", PAGE, [], "Ordway", call=fake_model())
        kept, rejected = bs.write_prompts(story, "Ordway", ["Recurly"], call=fake_model(prompts=prompts))
        self.assertEqual([p.stage for p in kept], ["switch"])
        self.assertEqual(kept[0].sources["before"]["quote"], STORY["parts"]["before"]["quote"])
        self.assertEqual(sorted(r["stage"] for r in rejected), ["pitch", "shortlist"])

    def test_uses_given_as_phrases_are_read_off_the_prompt(self):
        prompts = [{"stage": "switch", "uses": ["Recurly", "estate planning"],
                    "text": "We're a digital estate planning SaaS using Recurly for subscriptions "
                            "and payments. What should we move to?"}]
        story = bs.read_story("u", PAGE, [], "Ordway", call=fake_model())
        kept, _ = bs.write_prompts(story, "Ordway", [], call=fake_model(prompts=prompts))
        self.assertEqual(sorted(kept[0].uses), ["before", "company"])


class SiteTest(unittest.TestCase):

    def test_a_site_end_to_end_and_the_fanout_selection(self):
        prompts = [
            {"stage": "switch", "uses": ["company", "before"],
             "text": "We're a digital estate planning SaaS on Recurly. What should we move to?"},
            {"stage": "compare", "uses": ["company", "before"],
             "text": "We're a digital estate planning SaaS on Recurly. Is Zuora better for us?"},
        ]
        fetched = []
        result = bs.prompts_for_site("ordway.example", profile=PROFILE,
                                     call=fake_model(prompts=prompts),
                                     fetch=lambda url: fetched.append(url) or PAGE)
        self.assertEqual(len(result["stories"]), 2)
        self.assertEqual(len(fetched), 2)
        picked = bs.for_fanout(result)
        self.assertTrue(picked and all(p["grounding"] == "story" for p in picked))
        self.assertNotIn("Zuora", " ".join(p["text"] for p in picked))

    def test_an_unreadable_page_is_reported_not_raised(self):
        def fetch(url):
            raise OSError("blocked")
        result = bs.prompts_for_site("ordway.example", profile=PROFILE, call=fake_model(), fetch=fetch)
        self.assertTrue(all("error" in s for s in result["stories"]))


if __name__ == "__main__":
    unittest.main()
