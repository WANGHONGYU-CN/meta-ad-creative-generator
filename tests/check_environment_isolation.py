"""Check local/CI and staging have disjoint test records; no remote targets."""
import json
from urllib.request import urlopen


def products(port):
    with urlopen(f"http://127.0.0.1:{port}/api/products", timeout=10) as response:
        return {p["name"] for p in json.load(response)}


local = products(18000)
staging = products(18001)
assert local and staging, "Run the HTTP integration tests on both environments first"
assert local.isdisjoint(staging), "Product records leaked between environments"
print("PASS: local and staging product data are isolated")
