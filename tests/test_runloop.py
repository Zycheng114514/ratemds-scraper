"""The fleet must retry nonzero exits even if a pass processed zero doctors."""
import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("always_fail, expected_exit, expected_calls", [(False, 0, "2"), (True, 2, "2")])
def test_runloop_uses_exit_status_instead_of_processed_count(tmp_path, always_fail, expected_exit, expected_calls):
    binary = tmp_path / "python"
    binary.write_text("""#!/usr/bin/env bash
calls=0
[ -f "$TEST_COUNTER" ] && read -r calls < "$TEST_COUNTER"
calls=$((calls + 1))
printf '%s' "$calls" > "$TEST_COUNTER"
echo 'scrape-doctors done: 0 doctors processed this run.'
if [ "$calls" -eq 1 ] || [ "$TEST_ALWAYS_FAIL" = 1 ]; then exit 2; fi
exit 0
""")
    binary.chmod(0o755)
    counter = tmp_path / "calls"
    log = tmp_path / "run.log"
    script = Path(__file__).resolve().parents[1] / "src/ratemds_scraper/fleet/scripts/runloop.sh"
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "TEST_COUNTER": str(counter), "TEST_ALWAYS_FAIL": "1" if always_fail else "0",
           "OUTPUT": str(tmp_path / "output"), "LOG": str(log), "MAXPASS": "2",
           "RETRY_DELAY": "0", "TRANSPORT": "curl"}
    result = subprocess.run(["bash", str(script), "vt"], env=env, capture_output=True, timeout=10)
    assert result.returncode == expected_exit
    assert counter.read_text() == expected_calls
    assert "INCOMPLETE" in log.read_text()
    assert ("=== runloop COMPLETE ===" in log.read_text()) is (not always_fail)
