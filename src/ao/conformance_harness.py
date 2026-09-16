"""A fixture harness for `ao adapters conform`: it records the argv it was given, and nothing else (#77)."""
import json
import os
import sys

if __name__ == "__main__":
    with open(os.environ["AO_HARNESS_RECORD"], "w", encoding="utf-8") as fh:
        json.dump({"argv": sys.argv[1:]}, fh)
    print(json.dumps({"status": "SUCCESS", "response": "conformance"}))
