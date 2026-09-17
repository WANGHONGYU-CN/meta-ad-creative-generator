"""HTTP integration checks for the isolated local/CI stack, with no paid AI calls.

Run after scripts/init-local.py and deployment startup. Creates test products/runs
in the disposable local database; deliberately refuses non-loopback HTTP targets.
"""
import json
import os
import unittest
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4


BASE_URL = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:18000").rstrip("/")


def request(path, *, method="GET", body=None):
    data = None if body is None else json.dumps(body).encode()
    req = Request(BASE_URL + path, data=data, method=method,
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=15) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        error.close()
        raise


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed = urlparse(BASE_URL)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("Run deployment checks against the isolated local stack only")
        _, settings = request("/api/settings")
        if any(settings["effective_ready"].values()):
            raise RuntimeError("Refusing to write test data into an environment with API keys")

    def test_product_and_run_persist_changes(self):
        status, product = request("/api/products", method="POST", body={
            "name": "ci-smoke-" + uuid4().hex[:12],
            "info": "Deployment test product", "brand_name": "CI Brand", "ad_language": "English",
        })
        self.assertEqual(status, 201)
        status, created = request("/api/runs", method="POST", body={"product_id": product["id"]})
        self.assertEqual(status, 201)
        path = "/api/runs/" + created["name"]
        _, run = request(path)
        self.assertEqual(run["state"]["product_id"], product["id"])
        self.assertEqual(run["state"]["brand_name"], "CI Brand")
        self.assertEqual(run["state"]["ad_language"], "English")

        request(path, method="PATCH", body={"brand_name": "Updated CI Brand", "title_count": 5})
        _, updated = request(path)
        self.assertEqual(updated["state"]["brand_name"], "Updated CI Brand")
        self.assertEqual(updated["state"]["title_count"], 5)
        _, runs = request("/api/runs")
        self.assertIn(created["name"], [item["name"] for item in runs])
        _, products = request("/api/products")
        self.assertIn(product["id"], [item["id"] for item in products])

    def test_default_prompts_are_seeded(self):
        _, payload = request("/api/prompts")
        self.assertEqual(set(payload["prompts"]), {
            "scene_mining", "image_gen", "copywriting", "ratio_adapt", "refine_text", "image_refine",
        })
        for item in payload["prompts"].values():
            self.assertTrue(item["template"].strip())

    def test_invalid_product_is_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            request("/api/products", method="POST", body={"name": ""})
        self.assertEqual(caught.exception.code, 422)

    def test_unknown_run_is_not_a_frontend_page(self):
        with self.assertRaises(HTTPError) as caught:
            request("/api/runs/run_9223372036854775807")
        self.assertEqual(caught.exception.code, 404)

    def test_no_ai_credentials(self):
        _, settings = request("/api/settings")
        self.assertEqual(settings["effective_ready"], {"anthropic": False, "image": False})


if __name__ == "__main__":
    unittest.main()
