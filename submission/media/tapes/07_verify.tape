# Shot 7 / gallery 11 timing: a fresh clone from GitHub in an empty temp dir, timed setup + demo, then every test suite.
# Hidden setup: an empty temp dir, cargo on PATH, and CARGO_TARGET_DIR pointing at a pre-built cache of the same
# sources ($ANCHORIUM_MEDIA_CARGO_TARGET, built by `make contract-test vault-test` with that CARGO_TARGET_DIR), so the contract tests do not recompile for minutes (results are identical; the trees are the same).
Output submission/media/raw/07_verify.mp4
Output submission/media/raw/07_verify.txt
Source submission/media/tapes/settings.tape
Hide
Type "export PATH=$HOME/.cargo/bin:$PATH CARGO_TARGET_DIR=${ANCHORIUM_MEDIA_CARGO_TARGET:-$PWD/contracts/anchorium-guard/target} && cd $(mktemp -d) && clear"
Enter
Sleep 500ms
Show
Type "git clone -q -b near-grand-prize https://github.com/Adesh2204/anchorium-near-Paypal && cd anchorium-near-Paypal"
Sleep 300ms
Enter
Sleep 12s
Type "time (make setup && make demo)"
Sleep 300ms
Enter
Sleep 150s
Screenshot submission/media/raw/07_verify_demo.png
Type "clear && make test-py 2>&1 | tail -1 && make contract-test 2>&1 | grep 'test result: ok. [1-9]' && make vault-test 2>&1 | grep 'test result: ok. [1-9]'"
Sleep 300ms
Enter
Sleep 220s
Screenshot submission/media/raw/07_verify_end.png
Sleep 1s
