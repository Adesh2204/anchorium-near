# Shot 8 (NEW): NEAR AI Cloud evidence. verify-ai --full checks the committed TEE attestation offline and asks NVIDIA
# NRAS online (free); then the signed-review verifier's tests (against a fake TEE; signed reviews wait for credits).
Output submission/media/raw/08_near_ai.mp4
Output submission/media/raw/08_near_ai.txt
Source submission/media/tapes/settings.tape
Type 'make -s verify-ai ARGS="--full"'
Sleep 400ms
Enter
Sleep 14s
Screenshot submission/media/raw/08_near_ai_verify.png
Type ".venv/bin/python -m pytest -q tests/test_near_ai.py 2>&1 | tail -1"
Sleep 400ms
Enter
Sleep 6s
Screenshot submission/media/raw/08_near_ai_end.png
Sleep 1s
